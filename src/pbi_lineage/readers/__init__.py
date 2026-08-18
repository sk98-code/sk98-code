"""Public reader entry points: `read_pbix`, `read_pbip`, `read_any`.

`read_pbip` accepts a `.pbip` file, a project folder containing one, or a
`<name>.Report` folder directly. It auto-detects PBIR (`definition/pages/…`)
versus the older single-file `report.json` (legacy layout shape, UTF-8) and
TMDL versus `model.bim` for the semantic model. Thin reports (spec §5.4.1)
are detected in both formats: `byConnection` in `definition.pbir`, or a
PBIX with no `DataModel` part and a remote connection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pbi_lineage.readers.bim import read_model_bim
from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.readers.pbir import read_report_definition
from pbi_lineage.readers.pbix import read_pbix
from pbi_lineage.readers.tmdl import read_definition_dir
from pbi_lineage.schema import LiveConnection, ReadResult

__all__ = ["detect_format", "read_any", "read_pbip", "read_pbix"]

_DATASET_ID_RE = re.compile(r"(?i)initial catalog\s*=\s*([^;]+)")


def detect_format(path: str | Path) -> str | None:
    """Return "pbix" or "pbip" for a readable input, else None."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".pbix":
        return "pbix"
    if path.is_file() and path.suffix.lower() == ".pbip":
        return "pbip"
    if path.is_dir():
        if any(path.glob("*.pbip")) or _find_dir(path, ".Report") or path.name.endswith(".Report"):
            return "pbip"
    return None


def read_any(path: str | Path) -> ReadResult:
    detected = detect_format(path)
    if detected == "pbix":
        return read_pbix(path)
    if detected == "pbip":
        return read_pbip(path)
    raise ValueError(f"{path}: not a recognizable PBIX file or PBIP project")


def read_pbip(path: str | Path) -> ReadResult:
    path = Path(path)
    result = ReadResult(source_path=str(path), source_format="pbip")

    report_dir, project_name = _locate_report_dir(path, result.warnings)
    if report_dir is None:
        result.warnings.append(f"{path}: no .Report folder found")
        return result

    model_dir = _resolve_model_dir(report_dir, result)

    # ---- report layer -----------------------------------------------------
    if (report_dir / "definition" / "pages").is_dir():
        result.report = read_report_definition(report_dir, name=project_name, warnings=result.warnings)
    elif (report_dir / "report.json").exists():
        try:
            layout = json.loads((report_dir / "report.json").read_text(encoding="utf-8-sig"))
            result.report = parse_layout(layout, name=project_name, warnings=result.warnings)
        except ValueError as exc:
            result.warnings.append(f"{report_dir / 'report.json'}: {exc}")
    else:
        result.warnings.append(f"{report_dir}: neither definition/pages nor report.json found")

    # ---- model layer ------------------------------------------------------
    if model_dir is not None:
        definition_dir = model_dir / "definition"
        if definition_dir.is_dir() and any(definition_dir.glob("*.tmdl")):
            result.model = read_definition_dir(definition_dir, result.warnings)
        elif (model_dir / "model.bim").exists():
            result.model = read_model_bim(model_dir / "model.bim", result.warnings)
        else:
            result.warnings.append(f"{model_dir}: no TMDL definition or model.bim found")
    elif not result.is_thin:
        result.warnings.append("no semantic model folder resolved")

    return result


def _find_dir(parent: Path, suffix: str) -> Path | None:
    for candidate in sorted(parent.iterdir()):
        if candidate.is_dir() and candidate.name.endswith(suffix):
            return candidate
    return None


def _locate_report_dir(path: Path, warnings: list[str]) -> tuple[Path | None, str | None]:
    """Resolve the `<name>.Report` folder from any accepted input shape."""
    if path.is_file() and path.suffix.lower() == ".pbip":
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
            for artifact in manifest.get("artifacts", []) or []:
                report = artifact.get("report")
                if report and report.get("path"):
                    report_dir = (path.parent / report["path"]).resolve()
                    return report_dir, path.stem
        except (ValueError, OSError) as exc:
            warnings.append(f"{path}: {exc}")
        found = _find_dir(path.parent, ".Report")
        return found, path.stem
    if path.is_dir():
        if path.name.endswith(".Report"):
            return path, path.name[: -len(".Report")]
        manifest_file = next(iter(sorted(path.glob("*.pbip"))), None)
        if manifest_file is not None:
            return _locate_report_dir(manifest_file, warnings)
        found = _find_dir(path, ".Report")
        if found is not None:
            return found, found.name[: -len(".Report")]
    return None, None


def _resolve_model_dir(report_dir: Path, result: ReadResult) -> Path | None:
    """Follow `definition.pbir`: `byPath` → local model dir, `byConnection`
    → thin report (record the remote model, spec §5.4.1)."""
    pbir_file = report_dir / "definition.pbir"
    if pbir_file.exists():
        try:
            pbir = json.loads(pbir_file.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as exc:
            result.warnings.append(f"{pbir_file}: {exc}")
            pbir = {}
        dataset_ref = pbir.get("datasetReference", {}) or {}
        by_path = dataset_ref.get("byPath")
        if by_path and by_path.get("path"):
            return (report_dir / by_path["path"]).resolve()
        by_connection = dataset_ref.get("byConnection")
        if by_connection:
            result.is_thin = True
            connection_string = by_connection.get("connectionString")
            dataset_id = by_connection.get("pbiModelDatabaseName")
            if dataset_id is None and connection_string:
                match = _DATASET_ID_RE.search(connection_string)
                if match:
                    dataset_id = match.group(1).strip()
            result.connection = LiveConnection(
                dataset_id=dataset_id,
                connection_string=connection_string,
                raw=by_connection,
            )
            return None
    # no usable definition.pbir — fall back to the sibling .SemanticModel
    return _find_dir(report_dir.parent, ".SemanticModel")
