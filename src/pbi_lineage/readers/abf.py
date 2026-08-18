"""Offline model extraction from a PBIX `DataModel` (ABF) part.

The build spec (§4.1) says not to hand-parse the ABF binary and to use an
existing extractor instead. `pbixray` does that and exposes the very
TMSCHEMA_* rowsets the Milestone-2 DMV ingest already understands, so the
adapter here just presents them through the `DmvExecutor` protocol —
`read_model_via_dmv` then runs unchanged, and a file-based model comes out
identical in shape to a live one.

Optional dependency: `pip install "pbi-lineage[pbi-file]"` (pbixray).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pbi_lineage.connectors.dmv import read_calc_dependencies, read_model_via_dmv
from pbi_lineage.schema import Measure, Model, Relationship, Role

_SUPPLEMENTED = {
    "tmschema_measures",
    "tmschema_relationships",
    "tmschema_roles",
    "tmschema_table_permissions",
}


class PbixrayExecutor:
    """Serves `SELECT * FROM $SYSTEM.<name>` out of a PBIX file's ABF part.

    Only the TMSCHEMA_* family is available offline; storage/VertiPaq DMVs
    and DISCOVER_CALC_DEPENDENCY are not, so callers get a model inventory
    but no size attribution and no engine-resolved dependency edges (the
    DAX parser supplies those instead — see `resolve._parse_model_dax`).
    """

    def __init__(self, path: str | Path):
        try:
            from pbixray import PBIXRay  # noqa: PLC0415 — optional dependency
        except ImportError as exc:  # pragma: no cover - exercised without the extra
            raise RuntimeError(
                "reading a .pbix model offline needs pbixray: "
                'pip install "pbi-lineage[pbi-file]" — or use live Desktop mode (M2)'
            ) from exc
        self._model = PBIXRay(str(path))

    @property
    def raw(self) -> Any:
        """The underlying extractor, for frames outside the TMSCHEMA family."""
        return self._model

    def query(self, dmv_query: str) -> list[dict]:
        name = dmv_query.split("$SYSTEM.")[-1].strip()
        if not name.upper().startswith("TMSCHEMA_"):
            raise RuntimeError(f"{name} is not available from a file (needs a live engine)")
        attribute = name.lower()
        frame = getattr(self._model, attribute, None)
        if frame is None:
            # measures, relationships and RLS are published by name instead
            # of as ID-joined rowsets; `_supplement_from_name_frames` reads
            # them, so their absence here is expected, not a gap.
            if attribute in _SUPPLEMENTED:
                return []
            raise RuntimeError(f"{name} not exposed by the extractor")
        return _frame_to_rows(frame)

    def close(self) -> None:
        close = getattr(self._model, "close", None)
        if callable(close):  # pragma: no cover - depends on extractor version
            close()


def _frame_to_rows(frame: Any) -> list[dict]:
    """DataFrame → list[dict], with pandas NA collapsed to None."""
    if hasattr(frame, "to_dict"):
        rows = frame.to_dict(orient="records")
    else:  # pragma: no cover - already a sequence of mappings
        rows = list(frame)
    cleaned: list[dict] = []
    for row in rows:
        cleaned.append({key: (None if _is_na(value) else value) for key, value in row.items()})
    return cleaned


def _is_na(value: Any) -> bool:
    if value is None:
        return True
    try:
        import pandas as pd  # noqa: PLC0415 — pbixray brings pandas with it

        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except Exception:  # noqa: BLE001 - never let NA detection break ingest
        return False


def read_model_from_pbix(path: str | Path, warnings: list[str] | None = None) -> Model | None:
    """Extract the semantic model from a PBIX's ABF part, or None when the
    extractor is unavailable (the caller degrades to report-layer only)."""
    warnings = warnings if warnings is not None else []
    try:
        executor = PbixrayExecutor(path)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 — a corrupt ABF must not abort the read
        warnings.append(f"DataModel could not be extracted: {exc}")
        return None

    model = read_model_via_dmv(executor, name=Path(path).stem, warnings=warnings)
    _supplement_from_name_frames(model, executor, warnings)
    if not model.tables:
        warnings.append("DataModel extracted but contained no tables")
        return None
    return model


def _supplement_from_name_frames(model: Model, executor: "PbixrayExecutor", warnings: list[str]) -> None:
    """Fill in what the extractor publishes by *name* rather than as an
    ID-joined TMSCHEMA rowset: measures, relationships and RLS.

    Measures matter for correctness, not just completeness — a measure is
    what keeps the columns it aggregates from reading as unused, so a model
    whose measures were silently dropped would produce false 'Unused'
    verdicts (§5.3).
    """
    source = executor.raw

    measures = _frame_to_rows(getattr(source, "dax_measures", []))
    attached = 0
    for row in measures:
        table = model.table(str(row.get("TableName") or ""))
        if table is None:
            warnings.append(f"measure [{row.get('Name')}] references unknown table {row.get('TableName')}")
            continue
        table.measures.append(
            Measure(
                name=str(row.get("Name") or ""),
                expression=str(row.get("Expression") or ""),
                display_folder=(str(row["DisplayFolder"]) if row.get("DisplayFolder") else None),
                description=(str(row["Description"]) if row.get("Description") else None),
            )
        )
        attached += 1
    if measures and not attached:
        warnings.append("measures were found but none could be attached to a table")

    for index, row in enumerate(_frame_to_rows(getattr(source, "relationships", []))):
        model.relationships.append(
            Relationship(
                name=str(row.get("Name") or f"Relationship{index + 1}"),
                from_table=str(row.get("FromTableName") or ""),
                from_column=str(row.get("FromColumnName") or ""),
                to_table=str(row.get("ToTableName") or ""),
                to_column=str(row.get("ToColumnName") or ""),
                is_active=_truthy(row.get("IsActive"), default=True),
                to_cardinality=(str(row["Cardinality"]) if row.get("Cardinality") else None),
                cross_filtering=(
                    str(row["CrossFilteringBehavior"]) if row.get("CrossFilteringBehavior") else None
                ),
            )
        )

    for row in _frame_to_rows(getattr(source, "dax_columns", [])):
        table = model.table(str(row.get("TableName") or ""))
        if table is None:
            continue
        name = str(row.get("ColumnName") or "")
        column = next((c for c in table.columns if c.name == name), None)
        if column is not None:
            column.expression = str(row.get("Expression") or "") or None
            column.is_calculated = True

    roles: dict[str, Role] = {}
    for row in _frame_to_rows(getattr(source, "rls", [])):
        name = str(row.get("RoleName") or "")
        if not name:
            continue
        role = roles.get(name)
        if role is None:
            role = Role(name=name)
            roles[name] = role
            model.roles.append(role)
        table_name = str(row.get("TableName") or "")
        expression = str(row.get("FilterExpression") or row.get("Expression") or "")
        if table_name and expression:
            role.table_permissions[table_name] = expression


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def read_calc_dependencies_from_pbix(path: str | Path, warnings: list[str] | None = None) -> list:
    """CALC_DEPENDENCY is a live-engine DMV; offline it is simply absent."""
    warnings = warnings if warnings is not None else []
    try:
        return read_calc_dependencies(PbixrayExecutor(path), warnings=warnings)
    except RuntimeError:
        return []
