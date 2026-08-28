"""Incremental-scan bookkeeping.

`GetModifiedWorkspaces` answers "what changed since T", so the only state a
scan really needs is T. We keep a little more than that — a per-workspace
watermark and a content hash — so a re-scan can tell "changed" from
"re-reported unchanged", and so a failed workspace is retried next run
instead of being silently skipped forever.

SQLite because it is in the standard library and a scan checkpoint should
never be the reason a run cannot start.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

__all__ = ["ScanState", "WorkspaceCheckpoint", "api_timestamp"]

#: GetModifiedWorkspaces only accepts a modifiedSince within the last 30 days
MAX_LOOKBACK_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_checkpoint (
    workspace_id   TEXT PRIMARY KEY,
    last_scanned   TEXT NOT NULL,
    content_hash   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'ok',
    detail         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scan_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mode           TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    workspaces     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'running',
    detail         TEXT NOT NULL DEFAULT ''
);
"""


def api_timestamp(moment: datetime) -> str:
    """Format a datetime the way the admin APIs want it (UTC, 7 fractional digits)."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}0Z"


def content_hash(payload: Any) -> str:
    """Stable hash of a workspace's scanned content, for change detection."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkspaceCheckpoint:
    workspace_id: str
    last_scanned: datetime
    content_hash: str = ""
    status: str = "ok"
    detail: str = ""


class ScanState:
    """Checkpoint store. Safe to open concurrently; writes are transactional."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ScanState:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- watermarks -------------------------------------------------------
    def last_successful_scan(self) -> datetime | None:
        row = self._connection.execute(
            "SELECT MIN(last_scanned) AS watermark FROM workspace_checkpoint WHERE status = 'ok'"
        ).fetchone()
        if row is None or not row["watermark"]:
            return None
        return datetime.fromisoformat(row["watermark"])

    def modified_since(self, now: datetime | None = None) -> str:
        """The `modifiedSince` value for the next incremental scan.

        Falls back to the API's 30-day limit when the last scan is older than
        that (or when there has never been one), which correctly degrades an
        incremental run into a full one.
        """
        current = now or datetime.now(timezone.utc)
        floor = current - timedelta(days=MAX_LOOKBACK_DAYS - 1)
        watermark = self.last_successful_scan()
        if watermark is None or watermark < floor:
            return ""
        # step back a minute to tolerate clock skew between us and the service
        return api_timestamp(watermark - timedelta(minutes=1))

    # -- checkpoints ------------------------------------------------------
    def get(self, workspace_id: str) -> WorkspaceCheckpoint | None:
        row = self._connection.execute(
            "SELECT * FROM workspace_checkpoint WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkspaceCheckpoint(
            workspace_id=row["workspace_id"],
            last_scanned=datetime.fromisoformat(row["last_scanned"]),
            content_hash=row["content_hash"],
            status=row["status"],
            detail=row["detail"],
        )

    def record(
        self,
        workspace_id: str,
        scanned_at: datetime | None = None,
        payload_hash: str = "",
        status: str = "ok",
        detail: str = "",
    ) -> bool:
        """Save a checkpoint; returns True when the content actually changed."""
        moment = scanned_at or datetime.now(timezone.utc)
        previous = self.get(workspace_id)
        changed = previous is None or previous.content_hash != payload_hash
        self._connection.execute(
            """
            INSERT INTO workspace_checkpoint (workspace_id, last_scanned, content_hash, status, detail)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                last_scanned = excluded.last_scanned,
                content_hash = excluded.content_hash,
                status = excluded.status,
                detail = excluded.detail
            """,
            (workspace_id, moment.isoformat(), payload_hash, status, detail[:1000]),
        )
        self._connection.commit()
        return changed

    def failed_workspaces(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT workspace_id FROM workspace_checkpoint WHERE status <> 'ok'"
        ).fetchall()
        return [row["workspace_id"] for row in rows]

    def known_workspaces(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT workspace_id FROM workspace_checkpoint ORDER BY workspace_id"
        ).fetchall()
        return [row["workspace_id"] for row in rows]

    def forget(self, workspace_ids: Iterable[str]) -> int:
        ids = list(workspace_ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self._connection.execute(
            f"DELETE FROM workspace_checkpoint WHERE workspace_id IN ({placeholders})", ids
        )
        self._connection.commit()
        return cursor.rowcount

    # -- run log ----------------------------------------------------------
    def start_run(self, mode: str) -> int:
        cursor = self._connection.execute(
            "INSERT INTO scan_run (mode, started_at) VALUES (?, ?)",
            (mode, datetime.now(timezone.utc).isoformat()),
        )
        self._connection.commit()
        return int(cursor.lastrowid or 0)

    def finish_run(self, run_id: int, workspaces: int, status: str, detail: str = "") -> None:
        self._connection.execute(
            "UPDATE scan_run SET finished_at = ?, workspaces = ?, status = ?, detail = ? " "WHERE id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                workspaces,
                status,
                detail[:2000],
                run_id,
            ),
        )
        self._connection.commit()

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM scan_run ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
