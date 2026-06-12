"""Knowledge base: SQLite + JSON + Markdown, queried at runtime.

Tables:
    functions        — source-platform function reference w/ DAX mapping status
    target_functions — DAX & M references (emitter validation)
    conversion_rules — Section 6 rule store (generated from function seeds
                       + structural pattern seeds)

Converters never hardcode function lists: they call lookup_function() /
rules_for_tool(). `bimigrate kb init` (re)builds the store from seeds and
`bimigrate kb add-rule` extends it in the field.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bimigrate.models.core import ConversionRule, tier_for_confidence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS functions (
    source_tool TEXT NOT NULL,
    name TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    signature TEXT,
    category TEXT,
    dax_equivalent TEXT,
    dax_template TEXT,
    confidence REAL NOT NULL,
    notes TEXT,
    PRIMARY KEY (source_tool, name_lower)
);
CREATE TABLE IF NOT EXISTS target_functions (
    language TEXT NOT NULL,    -- 'dax' | 'm'
    name TEXT NOT NULL,
    category TEXT,
    PRIMARY KEY (language, name)
);
CREATE TABLE IF NOT EXISTS conversion_rules (
    rule_id TEXT PRIMARY KEY,
    source_tool TEXT NOT NULL,
    source_pattern TEXT NOT NULL,
    source_example TEXT,
    dax_template TEXT,
    dax_example TEXT,
    confidence REAL NOT NULL,
    preconditions TEXT,          -- JSON list
    known_failure_modes TEXT,    -- JSON list
    tier TEXT NOT NULL
);
"""


class KnowledgeBase:
    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- build from seeds -------------------------------------------------

    def init_from_seeds(self) -> dict[str, int]:
        from bimigrate.kb.seeds import (
            DAX_FUNCTIONS,
            M_FUNCTIONS,
            QLIK_FUNCTIONS,
            SPOTFIRE_FUNCTIONS,
            TABLEAU_FUNCTIONS,
        )
        from bimigrate.kb.seeds.patterns import PATTERN_RULES

        counts = {"functions": 0, "target_functions": 0, "conversion_rules": 0}
        seed_sets = [
            ("tableau", TABLEAU_FUNCTIONS),
            ("spotfire", SPOTFIRE_FUNCTIONS),
            ("qlikview", QLIK_FUNCTIONS),
            ("qliksense", QLIK_FUNCTIONS),  # shared expression language
        ]
        for tool, rows in seed_sets:
            for name, sig, cat, dax_name, template, conf, notes in rows:
                self._conn.execute(
                    "INSERT OR REPLACE INTO functions VALUES (?,?,?,?,?,?,?,?,?)",
                    (tool, name, name.lower(), sig, cat, dax_name, template, conf, notes),
                )
                counts["functions"] += 1
                # every mapped function becomes a data-driven conversion rule
                if template or dax_name:
                    rule = ConversionRule(
                        rule_id=f"{tool[:4]}-fn-{name.lower()}",
                        source_tool=tool,
                        source_pattern=rf"(?i)\b{name}\s*\(",
                        source_example=sig,
                        dax_template=template or f"{dax_name}({{0}})",
                        dax_example=template or f"{dax_name}(...)",
                        confidence=conf,
                        preconditions=[],
                        known_failure_modes=[notes] if notes else [],
                        tier=tier_for_confidence(conf).value,
                    )
                    self._insert_rule(rule)
                    counts["conversion_rules"] += 1

        for lang, ref in (("dax", DAX_FUNCTIONS), ("m", M_FUNCTIONS)):
            for category, names in ref.items():
                for name in names:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO target_functions VALUES (?,?,?)",
                        (lang, name, category),
                    )
                    counts["target_functions"] += 1

        for rule_id, tool, pattern, src_ex, template, dax_ex, conf, pre, fail in PATTERN_RULES:
            rule = ConversionRule(
                rule_id=rule_id,
                source_tool=tool,
                source_pattern=pattern,
                source_example=src_ex,
                dax_template=template or "",
                dax_example=dax_ex or "",
                confidence=conf,
                preconditions=list(pre),
                known_failure_modes=list(fail),
                tier=tier_for_confidence(conf).value,
            )
            self._insert_rule(rule)
            counts["conversion_rules"] += 1

        self._conn.commit()
        return counts

    def _insert_rule(self, rule: ConversionRule) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO conversion_rules VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                rule.rule_id,
                rule.source_tool,
                rule.source_pattern,
                rule.source_example,
                rule.dax_template,
                rule.dax_example,
                rule.confidence,
                json.dumps(rule.preconditions),
                json.dumps(rule.known_failure_modes),
                rule.tier,
            ),
        )

    def add_rule(self, rule: ConversionRule) -> None:
        self._insert_rule(rule)
        self._conn.commit()

    # -- runtime queries ------------------------------------------------------

    def lookup_function(self, source_tool: str, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM functions WHERE source_tool=? AND name_lower=?",
            (source_tool, name.lower()),
        ).fetchone()
        return dict(row) if row else None

    def is_valid_target_function(self, language: str, name: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM target_functions WHERE language=? AND UPPER(name)=UPPER(?)",
                (language, name),
            ).fetchone()
            is not None
        )

    def rules_for_tool(self, source_tool: str) -> list[ConversionRule]:
        rows = self._conn.execute(
            "SELECT * FROM conversion_rules WHERE source_tool=?", (source_tool,)
        ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def get_rule(self, rule_id: str) -> ConversionRule | None:
        row = self._conn.execute("SELECT * FROM conversion_rules WHERE rule_id=?", (rule_id,)).fetchone()
        return self._row_to_rule(row) if row else None

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> ConversionRule:
        return ConversionRule(
            rule_id=row["rule_id"],
            source_tool=row["source_tool"],
            source_pattern=row["source_pattern"],
            source_example=row["source_example"] or "",
            dax_template=row["dax_template"] or "",
            dax_example=row["dax_example"] or "",
            confidence=row["confidence"],
            preconditions=json.loads(row["preconditions"] or "[]"),
            known_failure_modes=json.loads(row["known_failure_modes"] or "[]"),
            tier=row["tier"],
        )

    def stats(self) -> dict[str, int]:
        out = {}
        for table in ("functions", "target_functions", "conversion_rules"):
            out[table] = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out

    # -- exports -----------------------------------------------------------------

    def export_json(self, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for table in ("functions", "target_functions", "conversion_rules"):
            rows = [dict(r) for r in self._conn.execute(f"SELECT * FROM {table}").fetchall()]
            path = out_dir / f"{table}.json"
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            written.append(path)
        return written

    def export_markdown(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# bimigrate knowledge base", ""]
        for tool in ("tableau", "spotfire", "qlikview", "qliksense"):
            rows = self._conn.execute(
                "SELECT * FROM functions WHERE source_tool=? ORDER BY category, name", (tool,)
            ).fetchall()
            if not rows:
                continue
            lines += [
                f"## {tool} functions ({len(rows)})",
                "",
                "| Function | Category | DAX mapping | Confidence | Notes |",
                "|---|---|---|---|---|",
            ]
            for r in rows:
                mapping = r["dax_template"] or r["dax_equivalent"] or "*(none — see notes)*"
                lines.append(
                    f"| `{r['name']}` | {r['category']} | `{mapping}` "
                    f"| {r['confidence']:.2f} | {(r['notes'] or '').replace('|', '/')} |"
                )
            lines.append("")
        path = out_dir / "knowledge_base.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
