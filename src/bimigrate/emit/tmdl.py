"""TMDL semantic-model emitter.

Builds the definition/ folder of a PBIP semantic model: model.tmdl,
one table .tmdl per table (columns, partitions in M, measures with
display folders), relationships.tmdl, roles (RLS) and expressions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TmdlMeasure:
    name: str
    expression: str
    display_folder: str = ""
    format_string: str = ""
    description: str = ""


@dataclass
class TmdlColumn:
    name: str
    data_type: str = "string"  # string | int64 | double | dateTime | boolean | decimal
    source_column: str | None = None
    is_hidden: bool = False


@dataclass
class TmdlTable:
    name: str
    columns: list[TmdlColumn] = field(default_factory=list)
    measures: list[TmdlMeasure] = field(default_factory=list)
    partition_m: str | None = None
    is_date_table: bool = False


@dataclass
class TmdlRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str = "manyToOne"  # manyToOne | oneToOne | manyToMany
    cross_filter: str = "singleDirection"  # singleDirection | bothDirections


@dataclass
class TmdlRole:
    name: str
    table_filters: dict[str, str] = field(default_factory=dict)  # table -> DAX filter


def _q(name: str) -> str:
    """TMDL-quote an object name when it needs it."""
    if name.replace("_", "").isalnum() and not name[0].isdigit():
        return name
    return f"'{name}'"


def _indent(text: str, level: int) -> str:
    """Indent every line; interior blank lines are dropped because Power BI
    Desktop's TMDL parser rejects empty lines inside an object body."""
    pad = "\t" * level
    return "\n".join(pad + line for line in text.splitlines() if line.strip())


def _one_line(text: str) -> str:
    """Doc-comments and property values must be single-line in TMDL."""
    return " ".join(text.split())


@dataclass
class TmdlModel:
    name: str
    tables: list[TmdlTable] = field(default_factory=list)
    relationships: list[TmdlRelationship] = field(default_factory=list)
    roles: list[TmdlRole] = field(default_factory=list)
    culture: str = "en-US"

    # -- rendering --------------------------------------------------------

    def render_model(self) -> str:
        # canonical object name is `Model`; the artifact name lives in
        # database.tmdl — Power BI Desktop follows the same convention
        lines = [
            "model Model",
            f"\tculture: {self.culture}",
            "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
            "\tsourceQueryCulture: en-US",
            "",
        ]
        for table in self.tables:
            lines.append(f"ref table {_q(table.name)}")
        if self.roles:
            for role in self.roles:
                lines.append(f"ref role {_q(role.name)}")
        return "\n".join(lines) + "\n"

    def render_table(self, table: TmdlTable) -> str:
        out = [f"table {_q(table.name)}"]
        if table.is_date_table:
            out.append("\tdataCategory: Time")
        for measure in table.measures:
            # TMDL doc-comments (///) must IMMEDIATELY PRECEDE the object
            # declaration; emitting them after properties breaks Desktop's
            # parser with InvalidLineType/Indentation errors.
            if measure.description:
                out.append(f"\t/// {_one_line(measure.description)}")
            expr = measure.expression
            if "\n" in expr:
                out.append(f"\tmeasure {_q(measure.name)} =")
                out.append(_indent(expr, 3))
            else:
                out.append(f"\tmeasure {_q(measure.name)} = {expr}")
            if measure.format_string:
                out.append(f"\t\tformatString: {measure.format_string}")
            if measure.display_folder:
                out.append(f"\t\tdisplayFolder: {measure.display_folder}")
            out.append("")
        for col in table.columns:
            out.append(f"\tcolumn {_q(col.name)}")
            out.append(f"\t\tdataType: {col.data_type}")
            if col.is_hidden:
                out.append("\t\tisHidden")
            out.append("\t\tsummarizeBy: none")
            out.append(f"\t\tsourceColumn: {col.source_column or col.name}")
            out.append("")
        if table.partition_m:
            out.append(f"\tpartition {_q(table.name + '-partition')} = m")
            out.append("\t\tmode: import")
            out.append("\t\tsource =")
            out.append(_indent(table.partition_m, 3))
        return "\n".join(out).rstrip("\n") + "\n"

    def render_relationships(self) -> str:
        out = []
        for i, rel in enumerate(self.relationships):
            out.append(f"relationship Relationship{i + 1}")
            if rel.cardinality != "manyToOne":
                out.append(f"\ttoCardinality: {'many' if rel.cardinality == 'manyToMany' else 'one'}")
            if rel.cross_filter == "bothDirections":
                out.append("\tcrossFilteringBehavior: bothDirections")
            out.append(f"\tfromColumn: {_q(rel.from_table)}.{_q(rel.from_column)}")
            out.append(f"\ttoColumn: {_q(rel.to_table)}.{_q(rel.to_column)}")
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"

    def render_role(self, role: TmdlRole) -> str:
        # no blank line between properties: Desktop's TMDL parser rejects
        # empty lines inside an object body
        out = [f"role {_q(role.name)}", "\tmodelPermission: read"]
        for table, dax in role.table_filters.items():
            out.append(f"\ttablePermission {_q(table)} = {dax}")
        return "\n".join(out) + "\n"

    # -- writing -------------------------------------------------------------

    def write(self, definition_dir: Path) -> list[Path]:
        definition_dir.mkdir(parents=True, exist_ok=True)
        written = [definition_dir / "model.tmdl"]
        written[0].write_text(self.render_model(), encoding="utf-8")

        (definition_dir / "database.tmdl").write_text(
            f"database {_q(self.name)}\n\tcompatibilityLevel: 1604\n", encoding="utf-8"
        )
        written.append(definition_dir / "database.tmdl")

        tables_dir = definition_dir / "tables"
        tables_dir.mkdir(exist_ok=True)
        for table in self.tables:
            path = tables_dir / f"{_safe_filename(table.name)}.tmdl"
            path.write_text(self.render_table(table), encoding="utf-8")
            written.append(path)

        if self.relationships:
            path = definition_dir / "relationships.tmdl"
            path.write_text(self.render_relationships(), encoding="utf-8")
            written.append(path)

        if self.roles:
            roles_dir = definition_dir / "roles"
            roles_dir.mkdir(exist_ok=True)
            for role in self.roles:
                path = roles_dir / f"{_safe_filename(role.name)}.tmdl"
                path.write_text(self.render_role(role), encoding="utf-8")
                written.append(path)
        return written


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
