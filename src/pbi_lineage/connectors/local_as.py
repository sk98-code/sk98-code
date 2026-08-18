"""Local Analysis Services connector plumbing (build spec §4.2, mode M2).

Power BI Desktop hosts a local AS instance on a random port, published in
`msmdsrv.port.txt` (UTF-16 encoded) under an AnalysisServicesWorkspaces
folder — one location for the installer build, another for the Store build.
Discovery is pure filesystem work, so it is injectable and testable
anywhere; only the ADOMD executor at the bottom needs Windows.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_PORT_FILE = "msmdsrv.port.txt"


@dataclass(frozen=True)
class LocalInstance:
    port: int
    workspace_dir: Path

    @property
    def connection_string(self) -> str:
        return f"Data Source=localhost:{self.port}"


def _candidate_workspace_roots(local_app_data: Path) -> list[Path]:
    roots = [local_app_data / "Microsoft" / "Power BI Desktop" / "AnalysisServicesWorkspaces"]
    packages = local_app_data / "Packages"
    if packages.is_dir():
        for package in sorted(packages.glob("Microsoft.MicrosoftPowerBIDesktop_*")):
            roots.append(
                package
                / "LocalCache"
                / "Local"
                / "Microsoft"
                / "Power BI Desktop Store App"
                / "AnalysisServicesWorkspaces"
            )
    return roots


def _read_port(port_file: Path) -> int | None:
    """The port file is UTF-16 (usually with BOM); tolerate LE-without-BOM
    and plain ASCII writers."""
    raw = port_file.read_bytes()
    for encoding in ("utf-16", "utf-16-le", "utf-8-sig"):
        try:
            text = raw.decode(encoding).strip().lstrip("\ufeff")
        except UnicodeDecodeError:
            continue
        if text.isdigit():
            return int(text)
    return None


def discover_local_instances(workspace_roots: list[Path] | None = None) -> list[LocalInstance]:
    """Find running Power BI Desktop AS instances via their port files.

    `workspace_roots` overrides the default %LOCALAPPDATA%-derived locations
    (installer and Store builds) — pass explicit paths in tests or when
    scanning a non-standard install.
    """
    if workspace_roots is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return []
        workspace_roots = _candidate_workspace_roots(Path(local_app_data))

    instances: list[LocalInstance] = []
    for root in workspace_roots:
        if not root.is_dir():
            continue
        for port_file in sorted(root.glob(f"*/Data/{_PORT_FILE}")):
            port = _read_port(port_file)
            if port is not None:
                instances.append(LocalInstance(port=port, workspace_dir=port_file.parent.parent))
    return instances


def write_pbitool_manifest(
    target_dir: Path,
    *,
    name: str,
    description: str,
    exe_path: str,
    arguments: str = '--server "%server%" --database "%database%"',
    icon_data: str = "",
) -> Path:
    """Write the External Tools `.pbitool.json` manifest (§4.2).

    The standard target is
    `C:\\Program Files (x86)\\Common Files\\Microsoft Shared\\Power BI
    Desktop\\External Tools\\` — writing there needs admin rights, so a
    portable install must fall back to `discover_local_instances` instead
    of ribbon integration.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1.0",
        "name": name,
        "description": description,
        "path": exe_path,
        "arguments": arguments,
        "iconData": icon_data,
    }
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
    path = target_dir / f"{safe_name}.pbitool.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


class PyadomdExecutor:
    """DMV executor backed by `pyadomd` (Windows + ADOMD.NET only).

    Satisfies the `DmvExecutor` protocol in `connectors.dmv`; everything
    above this class runs anywhere.
    """

    def __init__(self, connection_string: str):
        try:
            from pyadomd import Pyadomd  # noqa: PLC0415 — optional, Windows-only
        except ImportError as exc:  # pragma: no cover - exercised on Windows only
            raise RuntimeError(
                "pyadomd (and the ADOMD.NET client) is required for live Desktop "
                "connections — pip install pyadomd on a machine with Power BI Desktop"
            ) from exc
        self._factory = Pyadomd
        self._connection_string = connection_string

    def query(self, dmv_query: str) -> list[dict]:  # pragma: no cover - Windows only
        with self._factory(self._connection_string) as connection:
            with connection.cursor().execute(dmv_query) as cursor:
                columns = [d[0] for d in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
