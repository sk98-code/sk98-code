"""Safe removal: impact preview, TMSL/TMDL script, backup (spec §5.4.6,
§7.4 — Milestone 6, capability C7).

Hard rules encoded here:

- only objects whose verdict is exactly `Unused` are scriptable; every
  other status (report-level, indeterminate, external-consumer, protected)
  BLOCKS the plan — the §5.4.4 gate is enforced in code, not UI copy;
- deletion order: dependents (calculated columns, hierarchies,
  relationships) before the objects they depend on, or the XMLA commit
  fails mid-way;
- every plan ships a machine-readable impact manifest naming each
  affected downstream report — the artifact that gets a change approved;
- a backup command is always generated; `apply` is not the default and
  requires an explicit write executor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from pbi_lineage.graph import DependencyGraph
from pbi_lineage.resolve import STATUS_UNUSED, UsageVerdict
from pbi_lineage.schema import Model
from pbi_lineage.size import SizeReport, reclaimable_range


class XmlaWriteExecutor(Protocol):
    """Something that can execute a TMSL script (XMLA write endpoint)."""

    def execute(self, tmsl_script: str) -> None: ...


@dataclass
class ImpactItem:
    object_id: str
    kind: str
    detail: str


@dataclass
class RemovalPlan:
    database: str
    targets: list[str]  # node ids, deletion order (dependents first)
    blocked: bool
    block_reasons: list[str] = field(default_factory=list)
    impact: list[ImpactItem] = field(default_factory=list)
    reclaimable_low: int = 0
    reclaimable_high: int = 0
    tmsl_script: str = ""
    backup_script: str = ""
    manifest: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"Proposed removals: {len(self.targets)} object(s)"]
        for item in self.impact:
            lines.append(f"  -> {item.kind}: {item.detail}")
        if self.reclaimable_high:
            lines.append(f"  reclaimable: {self.reclaimable_low:,} - {self.reclaimable_high:,} bytes")
        lines.append(f"  VERDICT: {'BLOCKED - ' + '; '.join(self.block_reasons) if self.blocked else 'OK'}")
        return "\n".join(lines)


def _parse_node_id(node_id: str) -> tuple[str, str | None, str]:
    """node id → (kind, table, name)."""
    kind, _, rest = node_id.partition(":")
    if kind == "column":
        table, _, bracketed = rest.partition("[")
        return kind, table, bracketed.rstrip("]")
    if kind in ("hierarchy", "calcitem", "partition"):
        table, _, name = rest.partition("/")
        return kind, table, name
    return kind, None, rest


def _tmsl_delete_object(database: str, node_id: str, verdict: UsageVerdict) -> dict | None:
    kind, table, name = _parse_node_id(node_id)
    base = {"database": database}
    if kind == "column":
        return {"delete": {"object": {**base, "table": table, "column": name}}}
    if kind == "measure":
        return {"delete": {"object": {**base, "table": verdict.table or "", "measure": name}}}
    if kind == "hierarchy":
        return {"delete": {"object": {**base, "table": table, "hierarchy": name}}}
    if kind == "table":
        return {"delete": {"object": {**base, "table": name}}}
    return None


def plan_removal(
    graph: DependencyGraph,
    verdicts: dict[str, UsageVerdict],
    target_ids: list[str],
    *,
    model: Model,
    size_report: SizeReport | None = None,
) -> RemovalPlan:
    plan = RemovalPlan(database=model.name, targets=[], blocked=False)
    target_set = set(target_ids)

    # ---- eligibility: only exactly-Unused objects are scriptable ---------
    for node_id in target_ids:
        verdict = verdicts.get(node_id)
        if verdict is None:
            plan.blocked = True
            plan.block_reasons.append(f"{node_id}: unknown object")
            continue
        if verdict.status != STATUS_UNUSED:
            plan.blocked = True
            plan.block_reasons.append(f"{node_id}: status is '{verdict.status}', not '{STATUS_UNUSED}'")

    # ---- blast radius: every consumer outside the delete set -------------
    breaks = {"visual": 0, "report_level_measure": 0, "rls": 0, "measure": 0, "other": 0}
    for node_id in target_ids:
        for consumer in sorted(graph.consumers(node_id)):
            if consumer in target_set:
                continue
            node = graph.nodes.get(consumer)
            if node is None:
                continue
            if node.kind == "visual":
                breaks["visual"] += 1
                plan.impact.append(ImpactItem(consumer, "breaks visual", f"{consumer} loses {node_id}"))
            elif node.kind == "measure" and node.meta.get("is_report_level"):
                breaks["report_level_measure"] += 1
                plan.impact.append(
                    ImpactItem(
                        consumer,
                        "breaks report-level measure",
                        f"report {node.meta.get('host_report')} -> [{node.name}]",
                    )
                )
            elif node.kind == "role":
                breaks["rls"] += 1
                plan.impact.append(ImpactItem(consumer, "breaks RLS expression", node.name))
            elif node.kind == "measure":
                breaks["measure"] += 1
                plan.impact.append(ImpactItem(consumer, "breaks measure", node.name))
            elif node.kind in ("page", "report", "relationship"):
                breaks["other"] += 1
                plan.impact.append(ImpactItem(consumer, f"affects {node.kind}", node.name))
    if any(count for key, count in breaks.items()):
        plan.blocked = True
        plan.block_reasons.append(
            "removal breaks downstream consumers: "
            + ", ".join(f"{count} {key}" for key, count in breaks.items() if count)
        )

    # ---- deletion order: dependents before dependencies ------------------
    # Within the delete set, anything that consumes another target must be
    # deleted first (calc column before its source column, etc.).
    ordered: list[str] = []
    remaining = list(target_ids)
    while remaining:
        progressed = False
        for node_id in list(remaining):
            # a target is deletable once no other still-remaining target
            # consumes it — so dependents surface first
            consumers_in_set = (graph.consumers(node_id) & set(remaining)) - {node_id}
            if not consumers_in_set:
                ordered.append(node_id)
                remaining.remove(node_id)
                progressed = True
        if not progressed:  # cycle — keep declaration order for the rest
            ordered.extend(remaining)
            break
    plan.targets = ordered

    # ---- size range ------------------------------------------------------
    if size_report is not None:
        column_targets = []
        for node_id in ordered:
            kind, table, name = _parse_node_id(node_id)
            if kind == "column" and table:
                column_targets.append((table, name))
        plan.reclaimable_low, plan.reclaimable_high = reclaimable_range(size_report, column_targets)

    # ---- scripts ---------------------------------------------------------
    operations = []
    for node_id in ordered:
        verdict = verdicts.get(node_id)
        if verdict is None:
            continue
        operation = _tmsl_delete_object(model.name, node_id, verdict)
        if operation is not None:
            operations.append(operation)
    plan.tmsl_script = json.dumps({"sequence": {"operations": operations}}, indent=2)
    plan.backup_script = json.dumps(
        {
            "backup": {
                "database": model.name,
                "file": f"{model.name}-pre-removal.abf",
                "allowOverwrite": False,
            }
        },
        indent=2,
    )

    # ---- machine-readable impact manifest (change-request artifact) ------
    plan.manifest = {
        "database": model.name,
        "targets": [
            {
                "id": node_id,
                "kind": _parse_node_id(node_id)[0],
                "table": verdicts[node_id].table if node_id in verdicts else None,
                "name": verdicts[node_id].name if node_id in verdicts else None,
            }
            for node_id in ordered
        ],
        "blocked": plan.blocked,
        "block_reasons": plan.block_reasons,
        "impact": [
            {"object_id": item.object_id, "kind": item.kind, "detail": item.detail} for item in plan.impact
        ],
        "reclaimable_bytes": {"low": plan.reclaimable_low, "high": plan.reclaimable_high},
    }
    return plan


def apply_plan(plan: RemovalPlan, executor: XmlaWriteExecutor, *, backup_first: bool = True) -> None:
    """Execute a plan. Refuses blocked plans outright; backup is enforced
    unless explicitly disabled (spec: always generate a reversible path)."""
    if plan.blocked:
        raise ValueError("removal plan is BLOCKED: " + "; ".join(plan.block_reasons))
    if not plan.targets:
        return
    if backup_first:
        executor.execute(plan.backup_script)
    executor.execute(plan.tmsl_script)
