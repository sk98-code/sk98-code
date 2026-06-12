"""Builds a TmdlModel from an ArtifactInventory + conversion decisions.

This is the glue between Tier 1 (inventory) and the PBIP emitters:
tables/columns from the inventory, measures from AUTO/ASSISTED conversion
decisions (MANUAL items become documented placeholders), relationships
inferred from the source model, RLS roles from the security proposal.
"""

from __future__ import annotations

import re

from bimigrate.convert.loadscript_m import LoadScriptConverter
from bimigrate.emit.rls import build_rls
from bimigrate.emit.tmdl import (
    TmdlColumn,
    TmdlMeasure,
    TmdlModel,
    TmdlRelationship,
    TmdlTable,
)
from bimigrate.models.core import ConfidenceTier, ConversionDecision
from bimigrate.models.inventory import ArtifactInventory

_DATATYPE_MAP = {
    "integer": "int64",
    "real": "double",
    "number": "double",
    "string": "string",
    "date": "dateTime",
    "datetime": "dateTime",
    "boolean": "boolean",
}


def build_model(inv: ArtifactInventory, decisions: list[ConversionDecision]) -> TmdlModel:
    model = TmdlModel(name=inv.artifact_name)

    # tables + columns
    seen_tables: dict[str, TmdlTable] = {}
    for src_table in inv.tables:
        if src_table.name in seen_tables:
            continue
        table = TmdlTable(
            name=src_table.name,
            columns=[TmdlColumn(name=c) for c in dict.fromkeys(src_table.columns)],
        )
        seen_tables[table.name] = table
        model.tables.append(table)
    if not model.tables:
        model.tables.append(TmdlTable(name="Data", columns=[TmdlColumn(name="Value")]))
    primary = model.tables[0]

    # partitions from converted load scripts (Qlik) or source connections
    qlik_scripts = [s for s in inv.scripts if s.language == "qlik-load-script"]
    if qlik_scripts:
        converted = LoadScriptConverter().convert(qlik_scripts[0].code)
        m_by_name = {q.name: q.m_code for q in converted.queries}
        for table in model.tables:
            table.partition_m = m_by_name.get(table.name)
    for table in model.tables:
        if table.partition_m is None:
            table.partition_m = (
                "let\n    Source = null /* TODO: bind to migrated source for "
                f"{table.name} */\nin\n    Source"
            )

    # measures from conversion decisions (display folder mirrors source kind)
    folder_by_mapping = {
        "Set analysis": "Set Analysis",
        "Table calculation": "Table Calculations",
        "LOD FIXED": "LOD",
        "Master measures": "Master Measures",
    }
    used_names: set[str] = set()
    for decision in decisions:
        name = decision.source_artifact.split("/", 1)[-1] or "Measure"
        while name in used_names:
            name += " (2)"
        used_names.add(name)
        if decision.tier == ConfidenceTier.MANUAL or not decision.target_expression:
            primary.measures.append(
                TmdlMeasure(
                    name=name,
                    expression="BLANK() /* MANUAL: see exception register */",
                    display_folder="_Manual Items",
                    description=(decision.fallback_strategy or "manual conversion required")[:200],
                )
            )
            continue
        primary.measures.append(
            TmdlMeasure(
                name=name,
                expression=decision.target_expression,
                display_folder=folder_by_mapping.get(decision.feature_mapping or "", "Migrated"),
                description="; ".join(decision.annotations)[:200],
            )
        )

    # relationships inferred from source model
    for rel in inv.relationships:
        expr = rel.get("expression", "")
        m = re.search(r"\[([^\]]+)\]\s*=\s*\[([^\]]+)\]", expr)
        if m and len(model.tables) >= 2:
            model.relationships.append(
                TmdlRelationship(
                    from_table=model.tables[0].name,
                    from_column=m.group(1),
                    to_table=model.tables[1].name,
                    to_column=m.group(2),
                )
            )

    # RLS
    proposal = build_rls(inv, table_hint=primary.name)
    model.roles.extend(proposal.roles)
    return model
