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
import struct
import zipfile
from pathlib import Path

from pbi_lineage.readers.layout_legacy import parse_layout
from pbi_lineage.schema import LiveConnection, ReadResult

_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_EOCD = b"PK\x05\x06"
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
    with zipfile.ZipFile(path) as package:
        return _read_package(package, source=str(path), name=path.stem, model_path=path)


def read_pbix_bytes(data: bytes, *, name: str) -> ReadResult:
    """Parse PBIX content already in memory — e.g. a report Export from the
    REST API (§5.4.3) — without touching disk."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return _read_package(package, source=f"<bytes:{name}>", name=name)


def _read_package(
    package: zipfile.ZipFile, *, source: str, name: str, model_path: Path | None = None
) -> ReadResult:
    result = ReadResult(source_path=source, source_format="pbix")
    parts = set(package.namelist())

    if "Report/Layout" in parts:
        try:
            layout = decode_layout(package.read("Report/Layout"))
            result.report = parse_layout(layout, name=name, warnings=result.warnings)
        except ValueError as exc:
            result.warnings.append(f"Report/Layout: {exc}")
    else:
        result.warnings.append("no Report/Layout part found")

    if "Connections" in parts:
        result.connection = _parse_connections(package.read("Connections"), result.warnings)

    has_model = "DataModel" in parts
    if has_model and model_path is not None:
        # §4.1: don't hand-parse the ABF binary — hand it to an extractor.
        from pbi_lineage.readers.abf import read_model_from_pbix  # noqa: PLC0415 — optional

        result.model = read_model_from_pbix(model_path, result.warnings)
        if result.model is None:
            result.warnings.append(
                "DataModel (ABF) present but not read — install the pbi-file extra, "
                "or use live Desktop mode (M2), for model inventory"
            )
    elif has_model:
        result.warnings.append(
            "DataModel (ABF) present but not parsed — model inventory needs the file "
            "on disk (use read_pbix) or live Desktop mode (M2)"
        )
    result.is_thin = not has_model and result.connection is not None

    if "DataMashup" in parts:
        result.m_section = _extract_m_section(package.read("DataMashup"), result.warnings)
        if result.m_section and result.model is not None:
            _merge_m_section(result.model, result.m_section, result.warnings)

    return result


_SQL_WRAPPER = re.compile(r"(?is)^\s*select\s+\*\s+from\s*\[[^\]]+\]\s*$")


def _is_real_m(partition) -> bool:
    """Whether a partition's stored expression is actually Power Query.

    In a large class of files it is not: the partition carries the M
    engine's own wrapper, `SELECT * FROM [Sales]`, and the query itself
    lives only in the mashup's section document. Treating that wrapper as
    the query is what makes a model look like it has no data source at
    all."""
    expression = (partition.expression or "").strip()
    if not expression or _SQL_WRAPPER.match(expression):
        return False
    return partition.source_type in (None, "m") or "let" in expression.lower()


def _merge_m_section(model, section: str, warnings: list[str]) -> None:
    """Fold the mashup's shared queries into the model.

    A query whose name matches a table supplies that table's partition
    expression when the partition does not already hold real M. Everything
    else — parameters, custom functions, staging queries that are not
    loaded — becomes a shared expression, which is also the only place
    those appear at all when the extractor cannot read TMSCHEMA_EXPRESSIONS.
    """
    from pbi_lineage.readers.msection import parse_section  # noqa: PLC0415 — local format
    from pbi_lineage.schema import NamedExpression, Partition  # noqa: PLC0415

    try:
        queries = parse_section(section)
    except Exception as exc:  # noqa: BLE001 — a malformed section must not abort the read
        warnings.append(f"DataMashup: section document did not parse: {exc}")
        return

    by_table = {table.name: table for table in model.tables}
    known = {expression.name for expression in model.expressions}
    recovered = 0
    for query in queries:
        table = by_table.get(query.name)
        if table is not None and not query.is_function:
            partition = next((p for p in table.partitions if not _is_real_m(p)), None)
            if partition is None and not table.partitions:
                partition = Partition(name=query.name, source_type="m", mode="import")
                table.partitions.append(partition)
            if partition is not None:
                partition.expression = query.expression
                partition.source_type = "m"
                recovered += 1
                continue
            if any(_is_real_m(p) for p in table.partitions):
                continue
        if query.name not in known:
            model.expressions.append(NamedExpression(name=query.name, expression=query.expression))
            known.add(query.name)
    if recovered:
        warnings.append(
            f"recovered Power Query for {recovered} table(s) from the mashup section document"
        )


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


def _mashup_package(raw: bytes) -> list[bytes]:
    """Candidate byte ranges for the zip inside a DataMashup container.

    The container is length-prefixed: a 4-byte version, a 4-byte length,
    then that many bytes of zip, then further length-prefixed sections
    (permissions, metadata, permission bindings). Handing `zipfile`
    everything from the zip magic onwards is *not* equivalent: it scans
    backwards for the end-of-central-directory record and finds a false
    one in the trailing sections, then reports an archive with no entries
    and no error. That silently cost every query in the file.

    Ordered most to least trustworthy; the caller takes the first that
    yields Section1.m.
    """
    candidates: list[bytes] = []
    if len(raw) >= 8:
        version, length = struct.unpack_from("<II", raw, 0)
        if version in (0, 1) and 0 < length <= len(raw) - 8 and raw[8:12] == _ZIP_MAGIC:
            candidates.append(raw[8 : 8 + length])

    offset = raw.find(_ZIP_MAGIC)
    if offset >= 0:
        body = raw[offset:]
        # truncate at the last real end-of-central-directory record
        end = body.rfind(_ZIP_EOCD)
        if end >= 0:
            candidates.append(body[: end + 22])
        candidates.append(body)
    return candidates


def _extract_m_section(raw: bytes, warnings: list[str]) -> str | None:
    """Pull `Formulas/Section1.m` out of the nested DataMashup container."""
    candidates = _mashup_package(raw)
    if not candidates:
        warnings.append("DataMashup: no embedded zip found")
        return None
    for candidate in candidates:
        try:
            with zipfile.ZipFile(io.BytesIO(candidate)) as mashup:
                for name in mashup.namelist():
                    if name.endswith("Section1.m"):
                        return mashup.read(name).decode("utf-8-sig")
        except (zipfile.BadZipFile, OSError, EOFError):
            continue
    warnings.append("DataMashup: Section1.m not found in the embedded package")
    return None
