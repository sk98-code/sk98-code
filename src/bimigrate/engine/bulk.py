"""Bulk discovery engine.

Design constraints (Section 3):
* multiprocessing worker pool — parsing is CPU-bound (XML/zip/regex);
* SQLite-backed job persistence — every file's status + inventory JSON
  is written as it completes, so a crash loses at most in-flight files;
* resumable — re-running the same job_db skips files already done;
* per-file error isolation — a corrupt DXP/QVW records an error row and
  the batch continues.

Workers do the parse+score+scrub and return serialized inventories; only
the parent process writes SQLite, avoiding cross-process lock contention.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import structlog

from bimigrate.engine.complexity import score_inventory
from bimigrate.engine.scrub import scrub_inventory
from bimigrate.models.inventory import ArtifactInventory
from bimigrate.parsers.base import ParserError, get_parser_for, registered_extensions

log = structlog.get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    path TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error
    error TEXT,
    inventory_json TEXT,
    complexity_score REAL,
    complexity_band TEXT,
    fingerprint TEXT,
    source_tool TEXT,
    finished_at TEXT DEFAULT (datetime('now'))
);
"""


def _parse_one(path_str: str, scrub: bool) -> tuple[str, str | None, str | None]:
    """Worker entrypoint. Returns (path, inventory_json, error)."""
    path = Path(path_str)
    parser = get_parser_for(path)
    if parser is None:
        return path_str, None, f"no parser registered for {path.suffix}"
    try:
        inv = parser.parse(path)
        score_inventory(inv)
        if scrub:
            scrub_inventory(inv)
        return path_str, inv.model_dump_json(), None
    except ParserError as exc:
        return path_str, None, str(exc)
    except Exception as exc:  # isolation: never let one file kill the batch
        return path_str, None, f"unexpected {type(exc).__name__}: {exc}"


class BulkRunner:
    def __init__(self, job_db: Path, workers: int = 4, scrub: bool = False):
        self.job_db = job_db
        self.workers = max(1, workers)
        self.scrub = scrub
        self._conn = sqlite3.connect(job_db)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- discovery -------------------------------------------------------

    @staticmethod
    def discover_files(roots: list[Path]) -> list[Path]:
        exts = set(registered_extensions())
        found: list[Path] = []
        for root in roots:
            if root.is_file():
                if root.suffix.lower() in exts:
                    found.append(root)
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in exts:
                    found.append(path)
        return found

    # -- job control --------------------------------------------------------

    def enqueue(self, files: list[Path]) -> int:
        new = 0
        for f in files:
            cur = self._conn.execute("INSERT OR IGNORE INTO jobs(path) VALUES (?)", (str(f),))
            new += cur.rowcount
        self._conn.commit()
        return new

    def pending(self) -> list[str]:
        rows = self._conn.execute("SELECT path FROM jobs WHERE status='pending'").fetchall()
        return [r[0] for r in rows]

    def run(self, progress_callback=None) -> dict[str, int]:
        """Process all pending jobs; resumable across crashes/re-runs."""
        todo = self.pending()
        stats = {"done": 0, "error": 0, "skipped_existing": 0}
        stats["skipped_existing"] = self._count("status != 'pending'")
        if not todo:
            return stats

        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_parse_one, p, self.scrub): p for p in todo}
            for fut in as_completed(futures):
                path, inv_json, error = fut.result()
                if error:
                    self._conn.execute(
                        "UPDATE jobs SET status='error', error=?, finished_at=datetime('now') "
                        "WHERE path=?",
                        (error, path),
                    )
                    stats["error"] += 1
                    log.warning("parse_failed", path=path, error=error)
                else:
                    inv = json.loads(inv_json)
                    self._conn.execute(
                        "UPDATE jobs SET status='done', inventory_json=?, complexity_score=?, "
                        "complexity_band=?, fingerprint=?, source_tool=?, "
                        "finished_at=datetime('now') WHERE path=?",
                        (
                            inv_json,
                            inv.get("complexity_score"),
                            inv.get("complexity_band"),
                            inv.get("fingerprint"),
                            inv.get("source_tool"),
                            path,
                        ),
                    )
                    stats["done"] += 1
                self._conn.commit()  # checkpoint per file: crash-safe
                if progress_callback:
                    progress_callback(path, error)
        return stats

    # -- results -------------------------------------------------------------

    def load_inventories(self) -> list[ArtifactInventory]:
        rows = self._conn.execute("SELECT inventory_json FROM jobs WHERE status='done'").fetchall()
        return [ArtifactInventory.model_validate_json(r[0]) for r in rows]

    def errors(self) -> list[tuple[str, str]]:
        return self._conn.execute("SELECT path, error FROM jobs WHERE status='error'").fetchall()

    def _count(self, where: str) -> int:
        return self._conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}").fetchone()[0]
