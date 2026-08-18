"""PBIX (OPC zip) reader — build spec §4.1 / §5.1.

Extracts the legacy `Report/Layout` (UTF-16 LE, BOM optional), detects
thin reports via the missing `DataModel` part plus the `Connections`
part's remote-model connection, and pulls `Formulas/Section1.m` out of
the nested `DataMashup` container. The ABF `DataModel` binary is *not*
parsed (per spec: use live Desktop mode or an extractor library instead) —
its presence is only recorded.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.schema import LiveConnection, ReadResult

_ZIP_MAGIC = b"PK\x03\x04"
_DATASET_ID_RE = re.compile(r"(?i)initial catalog\s*=\s*([^;]+)")


def decode_layout(raw: bytes) -> dict:
    """Decode the Report/Layout part: UTF-16 LE with or without a BOM;
    some third-party writers emit UTF-8."""
    for encoding in ("utf-16", "utf-16-le", "utf-8-sig"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError("Report/Layout is neither UTF-16 (LE) nor UTF-8 JSON")


def read_pbix(path: str | Path) -> ReadResult:
    path = Path(path)
    result = ReadResult(source_path=str(path), source_format="pbix")

    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())

        if "Report/Layout" in names:
            try:
                layout = decode_layout(package.read("Report/Layout"))
                result.report = parse_layout(layout, name=path.stem, warnings=result.warnings)
            except ValueError as exc:
                result.warnings.append(f"Report/Layout: {exc}")
        else:
            result.warnings.append("no Report/Layout part found")

        if "Connections" in names:
            result.connection = _parse_connections(package.read("Connections"), result.warnings)

        has_model = "DataModel" in names
        if has_model:
            result.warnings.append(
                "DataModel (ABF) present but not parsed — use live Desktop mode (M2) "
                "or an extractor library for model inventory"
            )
        result.is_thin = not has_model and result.connection is not None

        if "DataMashup" in names:
            result.m_section = _extract_m_section(package.read("DataMashup"), result.warnings)

    return result


def _parse_connections(raw: bytes, warnings: list[str]) -> LiveConnection | None:
    try:
        connections = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        warnings.append("Connections part did not parse as JSON")
        return None

    dataset_id = None
    connection_string = None
    for artifact in connections.get("RemoteArtifacts", []) or []:
        if artifact.get("DatasetId"):
            dataset_id = str(artifact["DatasetId"])
            break
    for entry in connections.get("Connections", []) or []:
        text = entry.get("ConnectionString", "")
        if "pbiazure" in text.lower() or entry.get("ConnectionType", "").lower().startswith("pbiservice"):
            connection_string = text
            if dataset_id is None:
                match = _DATASET_ID_RE.search(text)
                if match:
                    dataset_id = match.group(1).strip()
            if dataset_id is None and entry.get("PbiModelDatabaseName"):
                dataset_id = str(entry["PbiModelDatabaseName"])
            break

    if dataset_id is None and connection_string is None:
        return None
    return LiveConnection(dataset_id=dataset_id, connection_string=connection_string, raw=connections)


def _extract_m_section(raw: bytes, warnings: list[str]) -> str | None:
    """DataMashup is a length-prefixed container whose first blob is a zip
    holding Formulas/Section1.m — locate the zip by magic and read it."""
    offset = raw.find(_ZIP_MAGIC)
    if offset < 0:
        warnings.append("DataMashup: no embedded zip found")
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw[offset:])) as mashup:
            for name in mashup.namelist():
                if name.endswith("Section1.m"):
                    return mashup.read(name).decode("utf-8-sig")
    except zipfile.BadZipFile:
        warnings.append("DataMashup: embedded zip is unreadable")
        return None
    warnings.append("DataMashup: Section1.m not found")
    return None
