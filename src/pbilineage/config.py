"""Runtime configuration.

Secrets are read from the environment (or a gitignored .env), never from
anything that is committed. `Settings.load()` is the only place in the
package that touches os.environ for credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

POWERBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
AAD_AUTHORITY = "https://login.microsoftonline.com"

# Scanner API caps the workspace list per getInfo call.
SCANNER_BATCH_SIZE = 100
# Tenant setting "Service principals can use read-only admin APIs" must be on.
ADMIN_API_HINT = (
    "Enable tenant setting 'Service principals can use read-only Power BI admin APIs' "
    "and grant the app registration Tenant.Read.All (admin consent)."
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Minimal .env loader — no dependency, does not overwrite real env vars.

    Returns the keys it set so callers can log *which* file supplied config
    without ever logging the values.
    """
    file = Path(path)
    if not file.is_file():
        return {}
    applied: dict[str, str] = {}
    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            applied[key] = "set"
    return applied


@dataclass(slots=True)
class Settings:
    """Everything the collectors need, resolved once at process start."""

    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    certificate_path: str = ""
    certificate_thumbprint: str = ""

    api_root: str = POWERBI_API_ROOT
    authority: str = AAD_AUTHORITY

    # Collection toggles — each maps to an optional, separately-permissioned surface.
    enable_xmla: bool = True
    enable_report_export: bool = True
    enable_dataflows: bool = True
    exclude_personal_workspaces: bool = True

    # Politeness / resilience.
    max_retries: int = 5
    backoff_base_seconds: float = 2.0
    request_timeout_seconds: float = 120.0
    scan_poll_seconds: float = 5.0
    scan_timeout_seconds: float = 1800.0
    max_concurrent_scans: int = 8

    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    state_path: Path = field(default_factory=lambda: Path("out/lineage/state.sqlite"))
    graph_path: Path = field(default_factory=lambda: Path("out/lineage/graph.json"))

    @classmethod
    def load(cls, dotenv: str | Path | None = ".env") -> Settings:
        if dotenv:
            load_dotenv(dotenv)
        return cls(
            tenant_id=_env("PBI_TENANT_ID"),
            client_id=_env("PBI_CLIENT_ID"),
            client_secret=_env("PBI_CLIENT_SECRET"),
            certificate_path=_env("PBI_CERTIFICATE_PATH"),
            certificate_thumbprint=_env("PBI_CERTIFICATE_THUMBPRINT"),
            api_root=_env("PBI_API_ROOT", POWERBI_API_ROOT),
            authority=_env("PBI_AUTHORITY", AAD_AUTHORITY),
            enable_xmla=_env_bool("PBI_ENABLE_XMLA", True),
            enable_report_export=_env_bool("PBI_ENABLE_REPORT_EXPORT", True),
            enable_dataflows=_env_bool("PBI_ENABLE_DATAFLOWS", True),
            exclude_personal_workspaces=_env_bool("PBI_EXCLUDE_PERSONAL_WORKSPACES", True),
            neo4j_uri=_env("NEO4J_URI"),
            neo4j_user=_env("NEO4J_USER", "neo4j"),
            neo4j_password=_env("NEO4J_PASSWORD"),
            neo4j_database=_env("NEO4J_DATABASE", "neo4j"),
            state_path=Path(_env("PBI_STATE_PATH", "out/lineage/state.sqlite")),
            graph_path=Path(_env("PBI_GRAPH_PATH", "out/lineage/graph.json")),
        )

    @property
    def has_credentials(self) -> bool:
        if not (self.tenant_id and self.client_id):
            return False
        return bool(self.client_secret or (self.certificate_path and self.certificate_thumbprint))

    def missing(self) -> list[str]:
        """Human-readable list of what is not configured yet (never values)."""
        gaps: list[str] = []
        if not self.tenant_id:
            gaps.append("PBI_TENANT_ID")
        if not self.client_id:
            gaps.append("PBI_CLIENT_ID")
        if not self.client_secret and not self.certificate_path:
            gaps.append("PBI_CLIENT_SECRET (or PBI_CERTIFICATE_PATH + PBI_CERTIFICATE_THUMBPRINT)")
        return gaps

    def redacted(self) -> dict[str, object]:
        """Safe-to-log view of the configuration."""
        return {
            "tenant_id": self.tenant_id or "<unset>",
            "client_id": self.client_id or "<unset>",
            "credential": (
                "certificate"
                if self.certificate_path
                else "client_secret" if self.client_secret else "<unset>"
            ),
            "api_root": self.api_root,
            "enable_xmla": self.enable_xmla,
            "enable_report_export": self.enable_report_export,
            "neo4j": self.neo4j_uri or "<in-memory graph>",
        }


def xmla_connection_string(settings: Settings, workspace_name: str, dataset_name: str = "") -> str:
    """Build the AAD service-principal XMLA connect string for a workspace.

    Only workspaces on Premium / PPU / Fabric capacity expose an XMLA
    endpoint; Pro-only workspaces will refuse the connection and the caller
    is expected to fall back to the Scanner-API path.
    """
    if not settings.has_credentials:
        raise ValueError("XMLA connection requires tenant/client credentials; see .env.example")
    parts = [
        f"Provider=MSOLAP;Data Source=powerbi://api.powerbi.com/v1.0/myorg/{workspace_name}",
        f"User ID=app:{settings.client_id}@{settings.tenant_id}",
        f"Password={settings.client_secret}",
    ]
    if dataset_name:
        parts.insert(1, f"Initial Catalog={dataset_name}")
    return ";".join(parts)
