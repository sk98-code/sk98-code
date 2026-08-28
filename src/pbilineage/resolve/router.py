"""Capacity-aware routing between the two resolvers.

The rule is simple and worth stating plainly: a workspace has an XMLA
endpoint only if it sits on Premium, PPU or Fabric capacity. Pro-only (shared
capacity) workspaces do not, so they take the DAX-parser path.

The router also degrades: if a workspace *should* have XMLA but the
connection fails (endpoint disabled at the tenant level, service principal
not in the workspace, capacity paused), it falls back to the parser path and
records why, rather than dropping the dataset's lineage entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pbilineage.models import CapacityTier, DatasetSpec, WorkspaceSpec
from pbilineage.resolve.base import DependencyResolver, DependencyResult
from pbilineage.resolve.dax_resolver import DaxDependencyResolver

#: SKU prefix -> tier. Order matters: longest prefix wins.
SKU_PREFIXES: tuple[tuple[str, CapacityTier], ...] = (
    ("PP", CapacityTier.PPU),  # Premium per user
    ("EM", CapacityTier.PRO),  # embedded EM SKUs expose no XMLA endpoint
    ("F", CapacityTier.FABRIC),
    ("P", CapacityTier.PREMIUM),
    ("A", CapacityTier.PREMIUM),  # A SKUs support XMLA read
)


def tier_from_sku(sku: str) -> CapacityTier:
    normalized = (sku or "").strip().upper()
    if not normalized:
        return CapacityTier.UNKNOWN
    if normalized in ("PREMIUMPERUSER", "PPU"):
        return CapacityTier.PPU
    for prefix, tier in SKU_PREFIXES:
        if normalized.startswith(prefix):
            return tier
    return CapacityTier.UNKNOWN


def tier_from_workspace(
    workspace: WorkspaceSpec, capacity_skus: dict[str, str] | None = None
) -> CapacityTier:
    """Decide a workspace's tier from its SKU, or its capacity assignment."""
    sku = workspace.capacity_sku
    if not sku and capacity_skus and workspace.capacity_id:
        sku = capacity_skus.get(workspace.capacity_id, "")
    tier = tier_from_sku(sku)
    if tier is not CapacityTier.UNKNOWN:
        return tier
    if workspace.capacity_id:
        # On a capacity, but we could not identify the SKU: assume an endpoint
        # exists and let the connection attempt settle it.
        return CapacityTier.PREMIUM
    return CapacityTier.PRO


@dataclass(slots=True)
class RoutingDecision:
    workspace_id: str
    dataset_id: str
    tier: CapacityTier
    path: str
    fell_back: bool = False
    reason: str = ""


@dataclass(slots=True)
class CapacityRouter:
    """Picks a resolver per workspace and degrades to the parser on failure."""

    xmla_resolver: DependencyResolver | None = None
    dax_resolver: DependencyResolver = field(default_factory=DaxDependencyResolver)
    capacity_skus: dict[str, str] = field(default_factory=dict)
    decisions: list[RoutingDecision] = field(default_factory=list)

    def tier_for(self, workspace: WorkspaceSpec) -> CapacityTier:
        if workspace.tier is not CapacityTier.UNKNOWN:
            return workspace.tier
        return tier_from_workspace(workspace, self.capacity_skus)

    def resolve(self, workspace: WorkspaceSpec, dataset: DatasetSpec) -> DependencyResult:
        tier = self.tier_for(workspace)
        decision = RoutingDecision(
            workspace_id=workspace.id,
            dataset_id=dataset.id,
            tier=tier,
            path=self.dax_resolver.path,
        )

        if tier.has_xmla and self.xmla_resolver is not None:
            decision.path = self.xmla_resolver.path
            result = self.xmla_resolver.resolve(dataset)
            if result.available:
                dataset.resolution_path = result.path
                self.decisions.append(decision)
                return result
            decision.fell_back = True
            decision.reason = "; ".join(result.warnings) or "XMLA endpoint unreachable"
            decision.path = self.dax_resolver.path
            fallback = self.dax_resolver.resolve(dataset)
            fallback.warnings = [
                f"workspace '{workspace.name}' is on {tier.value} capacity but XMLA was "
                f"unavailable ({decision.reason}); fell back to the DAX parser",
                *result.warnings,
                *fallback.warnings,
            ]
            dataset.resolution_path = fallback.path
            self.decisions.append(decision)
            return fallback

        if tier.has_xmla and self.xmla_resolver is None:
            decision.fell_back = True
            decision.reason = "no XMLA client configured"

        result = self.dax_resolver.resolve(dataset)
        if not tier.has_xmla:
            result.warnings.insert(
                0,
                f"workspace '{workspace.name}' has no XMLA endpoint ({tier.value} capacity); "
                "dependencies come from the DAX parser and are heuristic",
            )
        dataset.resolution_path = result.path
        self.decisions.append(decision)
        return result

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            key = f"{decision.path}{'+fallback' if decision.fell_back else ''}"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
