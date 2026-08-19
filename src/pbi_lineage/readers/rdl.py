"""Paginated (RDL) report reader — build spec §5.4.5.

An RDL is XML; the field references live in each DataSet's
`<CommandText>` as DAX or MDX query text. The spec's named trap: a measure
referenced as `Table[Measure]` inside SQL-style query text looks like a
column to a naive reader, so command text is run through the real DAX
scanner and resolved measures-first (see `dax.resolve_refs`).

MDX (`[Table].[Column]`, `[Measures].[X]`) is handled separately — its
bracket syntax is not DAX.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from pbi_lineage import dax
from pbi_lineage.schema import FieldReference, Model, RefKind

_MDX_MEASURE = re.compile(r"\[Measures\]\s*\.\s*\[([^\]]+)\]")
_MDX_MEMBER = re.compile(r"\[([^\]]+)\]\s*\.\s*\[([^\]]+)\]")


@dataclass
class RdlDataSet:
    name: str
    command_text: str = ""
    command_type: str = ""


@dataclass
class RdlReport:
    name: str
    datasets: list[RdlDataSet] = field(default_factory=list)
    dataset_references: list[str] = field(default_factory=list)  # shared dataset names
    warnings: list[str] = field(default_factory=list)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_rdl(data: bytes | str, *, name: str) -> RdlReport:
    report = RdlReport(name=name)
    try:
        root = ET.fromstring(data if isinstance(data, bytes) else data.encode("utf-8"))
    except ET.ParseError as exc:
        report.warnings.append(f"{name}: RDL is not parseable XML: {exc}")
        return report

    for element in root.iter():
        if _localname(element.tag) != "DataSet":
            continue
        dataset = RdlDataSet(name=element.get("Name", ""))
        for child in element.iter():
            local = _localname(child.tag)
            if local == "CommandText" and child.text:
                dataset.command_text = child.text
            elif local == "CommandType" and child.text:
                dataset.command_type = child.text
            elif local == "SharedDataSetReference" and child.text:
                report.dataset_references.append(child.text)
        report.datasets.append(dataset)
    return report


def _is_mdx(text: str) -> bool:
    upper = text.upper()
    return "[MEASURES]" in upper or ("SELECT" in upper and "ON COLUMNS" in upper)


def rdl_references(report: RdlReport, model: Model) -> list[FieldReference]:
    """Resolve every command text against the model → FieldReferences.

    Unresolvable names are dropped rather than guessed; the caller treats
    an RDL it could not parse as unretrieved coverage, not as "no usage".
    """
    references: list[FieldReference] = []
    for dataset in report.datasets:
        text = dataset.command_text
        if not text:
            continue
        evidence = f"rdl:{report.name}/DataSet[{dataset.name}]/CommandText"
        if _is_mdx(text):
            references.extend(_mdx_references(text, model, evidence))
            continue
        analysis = dax.analyze(text)
        resolution = dax.resolve_refs(analysis, model)
        for ref in resolution.refs:
            if ref.kind == "measure":
                references.append(
                    FieldReference(
                        table=ref.table,
                        name=ref.name,
                        kind=RefKind.MEASURE,
                        scope="paginated:query",
                        evidence=evidence,
                    )
                )
            elif ref.kind == "column":
                references.append(
                    FieldReference(
                        table=ref.table,
                        name=ref.name,
                        kind=RefKind.COLUMN,
                        scope="paginated:query",
                        evidence=evidence,
                    )
                )
    return references


def _mdx_references(text: str, model: Model, evidence: str) -> list[FieldReference]:
    references: list[FieldReference] = []
    measures_by_name = {m.name: t.name for t in model.tables for m in t.measures}
    for name in _MDX_MEASURE.findall(text):
        if name in measures_by_name:
            references.append(
                FieldReference(
                    table=measures_by_name[name],
                    name=name,
                    kind=RefKind.MEASURE,
                    scope="paginated:query",
                    evidence=evidence,
                )
            )
    for table_name, member in _MDX_MEMBER.findall(text):
        if table_name == "Measures":
            continue
        table = model.table(table_name)
        if table is None:
            continue
        if any(c.name == member for c in table.columns):
            references.append(
                FieldReference(
                    table=table_name,
                    name=member,
                    kind=RefKind.COLUMN,
                    scope="paginated:query",
                    evidence=evidence,
                )
            )
    return references
