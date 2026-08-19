"""Run the whole pipeline over a folder of real .pbix files and report.

Written because the tool had only ever seen two real files, and running it
over Microsoft's published sample corpus immediately exposed four bugs
that no unit test would have found: a DataMashup container read the wrong
way (silently, with no error), the section document parsed and then
discarded, references dropped rather than resolved, and a rule engine
emitting one finding per occurrence.

    git clone --depth 1 https://github.com/microsoft/powerbi-desktop-samples
    python scripts/check_corpus.py powerbi-desktop-samples

Nothing here asserts a specific number — the corpus changes. It reports
what came out so a person can see whether it looks right, and it fails
loudly on a file that raises.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from pbi_lineage.lineage import (
    attach_sources,
    column_lineage_graph,
    column_lineage_tree,
    end_to_end_rows,
)
from pbi_lineage.mindex import MExpressionIndex
from pbi_lineage.readers import read_any
from pbi_lineage.resolve import analyze_model
from pbi_lineage.rules import EstateContext, run_rules


def analyze(path: Path) -> dict:
    row: dict = {"file": path.name, "size_mb": round(path.stat().st_size / 1e6, 1)}
    started = time.perf_counter()
    try:
        result = read_any(path)
        row["warnings"] = result.warnings[:4]
        if result.model is None:
            row["stage"] = "no-model"
            return row
        reports = [result.report] if result.report is not None else []
        analysis = analyze_model(result.model, reports)
        index = MExpressionIndex()
        index.add_model(result.model)
        attach_sources(analysis.graph, result.model, index)

        row["tables"] = len(result.model.tables)
        row["columns"] = sum(len(t.columns) for t in result.model.tables)
        row["measures"] = sum(len(t.measures) for t in result.model.tables)
        row["used"] = sum(1 for v in analysis.verdicts.values() if v.status == "Used")
        row["unused"] = sum(1 for v in analysis.verdicts.values() if v.status == "Unused")
        row["unresolved"] = len(analysis.unresolved)
        row["m_entries"] = len(index.entries)
        row["sources"] = len(column_lineage_tree(result.model, analysis, index))
        row["e2e"] = len(end_to_end_rows(result.model, analysis, index))
        graph = column_lineage_graph(result.model, analysis, index, reports)
        row["cards"], row["hops"] = len(graph["nodes"]), len(graph["edges"])
        row["findings"] = len(
            run_rules(
                EstateContext(model=result.model, analysis=analysis, reports=reports, m_index=index)
            )
        )
        row["stage"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the point is to catch and report
        row["stage"] = "FAIL"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc().splitlines()[-5:]
    finally:
        row["secs"] = round(time.perf_counter() - started, 1)
    return row


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = sorted(root.rglob("*.pbix"))
    if not files:
        print(f"no .pbix files under {root}")
        return 1

    rows = [analyze(path) for path in files]
    for row in rows:
        marker = "FAIL" if row["stage"] == "FAIL" else "  ok"
        print(f"{marker}  {row['file'][:52]:54} {row.get('stage'):9} {row['secs']:>5}s")
        if row["stage"] == "FAIL":
            print(f"        {row['error']}")

    ok = [r for r in rows if r["stage"] == "ok"]
    failed = [r for r in rows if r["stage"] == "FAIL"]
    print(f"\n{len(ok)} analyzed, {len(failed)} failed, {len(rows) - len(ok) - len(failed)} had no model")
    if ok:
        total = lambda key: sum(r.get(key, 0) for r in ok)  # noqa: E731
        print(f"  with source lineage : {sum(1 for r in ok if r.get('sources', 0) > 0)} of {len(ok)}")
        print(f"  used / unused       : {total('used')} / {total('unused')}")
        print(f"  unresolved refs     : {total('unresolved')}")
        print(f"  findings            : {total('findings')}")
        print(f"  lineage hops        : {total('hops')}")

    out = Path("corpus_results.json")
    out.write_text(json.dumps(rows, indent=1))
    print(f"\nper-file detail: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
