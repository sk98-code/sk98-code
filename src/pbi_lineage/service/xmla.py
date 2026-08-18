"""XMLA / Service mode (build spec §3 modes M3–M4, §7.4 — Milestone 7).

The governing constraint: **the XMLA endpoint does not exist on Power BI
Pro workspaces.** Any tenant-wide capability needing full model metadata
therefore requires Premium / PPU / Fabric capacity. Mode M4 (Pro) is an
explicitly degraded path — this module labels it as such so the UI can
never silently present lower-confidence output as fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

XMLA_ROOT = "powerbi://api.powerbi.com/v1.0/myorg"


class Fidelity(str, Enum):
    FULL = "full"  # XMLA: TOM + all DMVs incl. VertiPaq sizes
    REDUCED = "reduced"  # Scanner datasetSchema only: no DAX for all types, no sizes
    NONE = "none"


class Mode(str, Enum):
    M1_LOCAL_FILE = "M1"
    M2_LIVE_DESKTOP = "M2"
    M3_SHARED_ONLINE = "M3"
    M4_PRO_WORKSPACE = "M4"
    M5_TENANT_SCAN = "M5"
    M6_SSAS_AAS = "M6"


# Capacity SKU prefixes that carry an XMLA endpoint
_XMLA_CAPABLE_SKUS = ("P", "EM", "A", "F", "PP")


@dataclass
class WorkspaceCapability:
    workspace_id: str
    name: str = ""
    capacity_id: str | None = None
    capacity_sku: str | None = None
    is_on_dedicated_capacity: bool = False
    is_ppu: bool = False

    @property
    def has_xmla(self) -> bool:
        """Premium / PPU / Fabric only (§3 critical constraint)."""
        if self.is_ppu:
            return True
        if not self.is_on_dedicated_capacity:
            return False
        sku = (self.capacity_sku or "").upper()
        return sku.startswith(_XMLA_CAPABLE_SKUS) if sku else bool(self.capacity_id)

    @property
    def mode(self) -> Mode:
        return Mode.M3_SHARED_ONLINE if self.has_xmla else Mode.M4_PRO_WORKSPACE

    @property
    def fidelity(self) -> Fidelity:
        return Fidelity.FULL if self.has_xmla else Fidelity.REDUCED

    def degradation_notice(self) -> str | None:
        """Text the UI must surface for a degraded workspace (§3, §14)."""
        if self.has_xmla:
            return None
        return (
            f"Workspace '{self.name or self.workspace_id}' is not on Premium/PPU/Fabric capacity: "
            "no XMLA endpoint, so model metadata comes from the Scanner API's datasetSchema "
            "(no DAX for all object types, no VertiPaq sizes). Results are reduced-confidence "
            "and excluded from size-based recommendations."
        )


def workspace_capability_from_scanner(workspace_json: dict) -> WorkspaceCapability:
    return WorkspaceCapability(
        workspace_id=str(workspace_json.get("id", "")),
        name=workspace_json.get("name", ""),
        capacity_id=workspace_json.get("capacityId"),
        capacity_sku=workspace_json.get("capacitySku") or workspace_json.get("sku"),
        is_on_dedicated_capacity=bool(
            workspace_json.get("isOnDedicatedCapacity", workspace_json.get("capacityId"))
        ),
        is_ppu=str(workspace_json.get("capacitySku", "")).upper().startswith("PP")
        or workspace_json.get("type") == "PersonalGroup"
        and bool(workspace_json.get("capacityId")),
    )


def xmla_connection_string(
    workspace_name: str, *, dataset: str | None = None, readwrite: bool = False
) -> str:
    """`Data Source=powerbi://…/<workspace>;Initial Catalog=<dataset>`.

    `readwrite=true` selects the XMLA *write* endpoint used for removal
    scripts (§7.4) — Premium/PPU/Fabric only, and the tenant's XMLA
    endpoint setting must be Read Write.
    """
    source = f"{XMLA_ROOT}/{workspace_name}"
    if readwrite:
        source += ";Mode=ReadWrite"
    parts = [f"Data Source={source}"]
    if dataset:
        parts.append(f"Initial Catalog={dataset}")
    return ";".join(parts)


@dataclass
class SourceDecision:
    """Which connector a given workspace's model should be read through."""

    workspace: WorkspaceCapability
    mode: Mode
    fidelity: Fidelity
    connection_string: str | None = None
    notices: list[str] = field(default_factory=list)

    @property
    def size_analysis_available(self) -> bool:
        """VertiPaq sizes need the storage DMVs, i.e. XMLA (§7.2)."""
        return self.fidelity is Fidelity.FULL


def decide_source(workspace: WorkspaceCapability, *, dataset: str | None = None) -> SourceDecision:
    decision = SourceDecision(workspace=workspace, mode=workspace.mode, fidelity=workspace.fidelity)
    if workspace.has_xmla:
        decision.connection_string = xmla_connection_string(
            workspace.name or workspace.workspace_id, dataset=dataset
        )
    else:
        notice = workspace.degradation_notice()
        if notice:
            decision.notices.append(notice)
    return decision


class XmlaExecutor:  # pragma: no cover - requires ADOMD + a Premium tenant
    """DMV executor over an XMLA endpoint; satisfies `DmvExecutor`, so the
    whole Milestone 2 ingest works unchanged against a Service model."""

    def __init__(self, connection_string: str, *, access_token: str | None = None):
        try:
            from pyadomd import Pyadomd  # noqa: PLC0415 — optional, Windows/.NET
        except ImportError as exc:
            raise RuntimeError("pyadomd + ADOMD.NET are required for XMLA access") from exc
        if access_token:
            connection_string = f"{connection_string};Password={access_token}"
        self._factory = Pyadomd
        self._connection_string = connection_string

    def query(self, dmv_query: str) -> list[dict]:
        with self._factory(self._connection_string) as connection:
            with connection.cursor().execute(dmv_query) as cursor:
                columns = [d[0] for d in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]


class XmlaWriteClient:  # pragma: no cover - requires ADOMD + a Premium tenant
    """TMSL execution against the XMLA write endpoint (§7.4). Satisfies the
    `XmlaWriteExecutor` protocol used by `removal.apply_plan`."""

    def __init__(self, workspace_name: str, *, access_token: str | None = None):
        self.connection_string = xmla_connection_string(workspace_name, readwrite=True)
        self._executor = XmlaExecutor(self.connection_string, access_token=access_token)

    def execute(self, tmsl_script: str) -> None:
        self._executor.query(tmsl_script)
