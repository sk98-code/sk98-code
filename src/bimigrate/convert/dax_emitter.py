"""AST -> DAX emitter.

Every function translation is a runtime knowledge-base lookup; the
emitter owns only structure (IF/CASE/LOD/set-analysis/OVER shapes).
The result carries the minimum confidence across all mappings touched,
the rule ids cited, and human-readable annotations — that is what makes
each conversion traceable to FeatureMapping / ConversionRule records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from bimigrate.convert.ast_nodes import (
    BinOp,
    Bool,
    CaseExpr,
    DollarExpansion,
    FieldRef,
    FuncCall,
    Ident,
    IfExpr,
    LodExpr,
    Node,
    NullLit,
    Num,
    OverExpr,
    SetExpression,
    Str,
    ThenExpr,
    UnaryOp,
)
from bimigrate.kb.knowledge_base import KnowledgeBase

FieldResolver = Callable[[str], str]


@dataclass
class EmitResult:
    dax: str
    confidence: float = 1.0
    annotations: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)

    def merge(self, other: "EmitResult") -> str:
        self.confidence = min(self.confidence, other.confidence)
        self.annotations.extend(other.annotations)
        self.rule_ids.extend(other.rule_ids)
        return other.dax


_DATEPART_TO_DAX_INTERVAL = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "week": "WEEK",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
}
_DATEPART_EXTRACTOR = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
    "week": "WEEKNUM",
    "weekday": "WEEKDAY",
    "dayofyear": None,
}

_OP_MAP = {
    "=": "=",
    "==": "=",
    "<>": "<>",
    "!=": "<>",
    "AND": "&&",
    "OR": "||",
    "&": "&",
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    "^": "^",
    "<": "<",
    ">": ">",
    "<=": "<=",
    ">=": ">=",
}


def default_resolver(name: str) -> str:
    return f"[{name}]"


class DaxEmitter:
    def __init__(
        self,
        kb: KnowledgeBase,
        source_tool: str,
        field_resolver: FieldResolver | None = None,
        table_hint: str = "Data",
    ):
        self.kb = kb
        # qliksense shares the qlik function table via seeds; both keys exist
        self.source_tool = source_tool
        self.resolve = field_resolver or default_resolver
        self.table_hint = table_hint
        self._unresolved_fields: set[str] = set()

    # -- public ------------------------------------------------------------

    def emit(self, node: Node) -> EmitResult:
        result = self._emit(node)
        if self._unresolved_fields:
            result.annotations.append(
                "field-to-table binding required for: " + ", ".join(sorted(self._unresolved_fields))
            )
        return result

    # -- dispatch ------------------------------------------------------------

    def _emit(self, node: Node) -> EmitResult:
        method = getattr(self, f"_emit_{type(node).__name__.lower()}", None)
        if method is None:
            return EmitResult(
                dax=f"/* unsupported node {type(node).__name__} */",
                confidence=0.0,
                annotations=[f"unsupported AST node {type(node).__name__}"],
            )
        return method(node)

    def _emit_num(self, node: Num) -> EmitResult:
        return EmitResult(dax=node.value)

    def _emit_str(self, node: Str) -> EmitResult:
        escaped = node.value.replace('"', '""')
        return EmitResult(dax=f'"{escaped}"')

    def _emit_bool(self, node: Bool) -> EmitResult:
        return EmitResult(dax="TRUE()" if node.value else "FALSE()")

    def _emit_nulllit(self, node: NullLit) -> EmitResult:
        return EmitResult(dax="BLANK()")

    def _emit_fieldref(self, node: FieldRef) -> EmitResult:
        resolved = self.resolve(node.name)
        if resolved == f"[{node.name}]":
            self._unresolved_fields.add(node.name)
        return EmitResult(dax=resolved)

    def _emit_ident(self, node: Ident) -> EmitResult:
        return EmitResult(
            dax=f"[{node.name}]",
            annotations=[f"bare identifier '{node.name}' treated as field/measure ref"],
            confidence=0.8,
        )

    def _emit_dollarexpansion(self, node: DollarExpansion) -> EmitResult:
        return EmitResult(
            dax=f"[{node.name} Value]",
            confidence=0.55,
            annotations=[
                f"$({node.name}) variable expansion: map to a measure or "
                "what-if parameter named here as a placeholder"
            ],
            rule_ids=["qs-variable-expansion"],
        )

    def _emit_unaryop(self, node: UnaryOp) -> EmitResult:
        result = EmitResult(dax="")
        inner = result.merge(self._emit(node.operand))
        result.dax = f"NOT({inner})" if node.op == "NOT" else f"{node.op}{inner}"
        return result

    def _emit_binop(self, node: BinOp) -> EmitResult:
        result = EmitResult(dax="")
        left = result.merge(self._emit(node.left))
        right = result.merge(self._emit(node.right))
        if node.op == "%":
            result.dax = f"MOD({left}, {right})"
            return result
        if node.op == "LIKE":
            result.dax = f"CONTAINSSTRING({left}, {right})"
            result.confidence = min(result.confidence, 0.6)
            result.annotations.append(
                "LIKE pattern translated to CONTAINSSTRING; " "wildcards %/_ need review"
            )
            return result
        dax_op = _OP_MAP.get(node.op)
        if dax_op is None:
            result.confidence = 0.0
            result.annotations.append(f"unknown operator {node.op}")
            dax_op = node.op
        result.dax = f"{left} {dax_op} {right}"
        return result

    # -- structural shapes -----------------------------------------------------

    def _emit_ifexpr(self, node: IfExpr) -> EmitResult:
        result = EmitResult(dax="")
        if len(node.branches) == 1:
            cond, value = node.branches[0]
            c = result.merge(self._emit(cond))
            v = result.merge(self._emit(value))
            e = result.merge(self._emit(node.else_value)) if node.else_value else "BLANK()"
            result.dax = f"IF({c}, {v}, {e})"
        else:
            parts = []
            for cond, value in node.branches:
                parts.append(result.merge(self._emit(cond)))
                parts.append(result.merge(self._emit(value)))
            default = result.merge(self._emit(node.else_value)) if node.else_value else "BLANK()"
            joined = ", ".join(parts)
            result.dax = f"SWITCH(TRUE(), {joined}, {default})"
        return result

    def _emit_caseexpr(self, node: CaseExpr) -> EmitResult:
        result = EmitResult(dax="")
        if node.subject is not None:
            subject = result.merge(self._emit(node.subject))
            parts = []
            for test, value in node.whens:
                parts.append(result.merge(self._emit(test)))
                parts.append(result.merge(self._emit(value)))
            default = result.merge(self._emit(node.else_value)) if node.else_value else "BLANK()"
            result.dax = f"SWITCH({subject}, {', '.join(parts)}, {default})"
        else:
            parts = []
            for test, value in node.whens:
                parts.append(result.merge(self._emit(test)))
                parts.append(result.merge(self._emit(value)))
            default = result.merge(self._emit(node.else_value)) if node.else_value else "BLANK()"
            result.dax = f"SWITCH(TRUE(), {', '.join(parts)}, {default})"
        return result

    def _emit_lodexpr(self, node: LodExpr) -> EmitResult:
        from bimigrate.convert.ast_nodes import walk

        result = EmitResult(dax="")
        nested = any(isinstance(d, LodExpr) for d in walk(node.expr))
        inner = result.merge(self._emit(node.expr))
        dims = ", ".join(self.resolve(d) for d in node.dims)

        if node.kind == "fixed":
            if not node.dims:
                result.dax = f"CALCULATE({inner}, REMOVEFILTERS())"
                result.rule_ids.append("tab-lod-fixed-nodim")
                result.confidence = min(result.confidence, 0.88)
            else:
                result.dax = f"CALCULATE({inner}, ALLEXCEPT('{self.table_hint}', {dims}))"
                result.rule_ids.append("tab-lod-fixed")
                result.confidence = min(result.confidence, 0.82)
                result.annotations.append(
                    "FIXED -> ALLEXCEPT assumes dimensions live on the fact table; "
                    "review filter-context interaction"
                )
        elif node.kind == "include":
            first = self.resolve(node.dims[0]) if node.dims else "[?]"
            result.dax = f"AVERAGEX(VALUES({first}), CALCULATE({inner}))"
            result.rule_ids.append("tab-lod-include")
            result.confidence = min(result.confidence, 0.72)
            result.annotations.append(
                "INCLUDE emitted with AVERAGEX outer iterator: replace outer aggregation "
                "to match the visual's aggregation"
            )
        else:  # exclude
            result.dax = f"CALCULATE({inner}, REMOVEFILTERS({dims}))"
            result.rule_ids.append("tab-lod-exclude")
            result.confidence = min(result.confidence, 0.78)

        if nested:
            result.confidence = min(result.confidence, 0.62)  # ASSISTED cap (Section 6)
            result.rule_ids.append("tab-lod-nested")
            result.annotations.append("nested LOD: capped at ASSISTED tier, mandatory review")
        return result

    def _emit_overexpr(self, node: OverExpr) -> EmitResult:
        result = EmitResult(dax="")
        inner = result.merge(self._emit(node.expr))
        nav = node.navigation
        if isinstance(nav, FuncCall):
            method = nav.name
            kb_row = self.kb.lookup_function("spotfire", method)
            axis = "[Axis.X]"
            if nav.args and isinstance(nav.args[0], (FieldRef, Ident)):
                axis = self.resolve(getattr(nav.args[0], "name"))
            if kb_row and kb_row["dax_template"]:
                result.dax = kb_row["dax_template"].replace("{0}", inner).replace("{1}", axis)
                result.confidence = min(result.confidence, kb_row["confidence"])
                result.rule_ids.append(f"spot-fn-{method.lower()}")
                if kb_row["notes"]:
                    result.annotations.append(f"OVER {method}: {kb_row['notes']}")
            else:
                result.dax = f"/* OVER ({method}) */ {inner}"
                result.confidence = min(result.confidence, 0.4)
                result.annotations.append(f"OVER navigation method {method} has no mapping; manual rewrite")
            if isinstance(nav, FuncCall) and nav.name.lower() == "intersect":
                result.confidence = min(result.confidence, 0.60)
                result.rule_ids.append("spot-over-intersect-custom")
                result.annotations.append("OVER Intersect with hierarchies: mandatory review")
        else:
            # plain OVER ([Column]) == group-by that column
            axis = result.merge(self._emit(nav))
            result.dax = f"CALCULATE({inner}, ALLEXCEPT('{self.table_hint}', {axis}))"
            result.confidence = min(result.confidence, 0.7)
        return result

    def _emit_thenexpr(self, node: ThenExpr) -> EmitResult:
        result = EmitResult(dax="")
        stage_exprs = [result.merge(self._emit(s)) for s in node.stages]
        vars_block = "\n".join(
            f"VAR _stage{i} = {expr.replace('[Value]', f'_stage{i - 1}') if i else expr}"
            for i, expr in enumerate(stage_exprs)
        )
        result.dax = f"{vars_block}\nRETURN _stage{len(stage_exprs) - 1}"
        result.confidence = min(result.confidence, 0.60)
        result.rule_ids.append("spot-then")
        result.annotations.append("THEN chain emitted as VAR stages; [Value] refs rewired — review")
        return result

    # -- function calls -----------------------------------------------------------

    def _emit_funccall(self, node: FuncCall) -> EmitResult:
        result = EmitResult(dax="")
        name_lower = node.name.lower()

        if name_lower == "aggr" and self.source_tool in ("qlikview", "qliksense"):
            return self._emit_aggr(node)
        if (
            name_lower in ("datediff", "dateadd", "datename", "datepart", "datetrunc")
            and self.source_tool == "tableau"
        ):
            return self._emit_tableau_datefunc(node)

        args = [result.merge(self._emit(a)) for a in node.args]

        kb_tool = "qlikview" if self.source_tool == "qliksense" else self.source_tool
        kb_row = self.kb.lookup_function(kb_tool, node.name)

        if node.distinct and name_lower == "count":
            core = f"DISTINCTCOUNT({args[0]})"
            result.confidence = min(result.confidence, 0.95)
            result.rule_ids.append("qlik-fn-count")
        elif kb_row is None:
            core = f"{node.name}({', '.join(args)})"
            result.confidence = 0.0
            result.annotations.append(f"function {node.name} not in knowledge base — unsupported, MANUAL")
        elif not kb_row["dax_template"] and not kb_row["dax_equivalent"]:
            core = f"/* {node.name}({', '.join(args)}) */"
            result.confidence = min(result.confidence, kb_row["confidence"])
            result.annotations.append(
                f"{node.name}: no native DAX equivalent — {kb_row['notes'] or 'see fallback'}"
            )
            result.rule_ids.append(f"{kb_tool[:4]}-fn-{name_lower}")
        else:
            template = kb_row["dax_template"] or f"{kb_row['dax_equivalent']}({{0}})"
            core = self._fill_template(template, args, result, node.name)
            result.confidence = min(result.confidence, kb_row["confidence"])
            result.rule_ids.append(f"{kb_tool[:4]}-fn-{name_lower}")
            if kb_row["notes"]:
                result.annotations.append(f"{node.name}: {kb_row['notes']}")
            if kb_row["dax_equivalent"] and not self.kb.is_valid_target_function(
                "dax", kb_row["dax_equivalent"]
            ):
                result.confidence = min(result.confidence, 0.3)
                result.annotations.append(
                    f"emitter validation: {kb_row['dax_equivalent']} not in DAX reference"
                )

        # Qlik aggregation prologue wrappers
        filters: list[str] = []
        if node.set_expr is not None:
            set_result = self._set_to_filters(node.set_expr)
            result.confidence = min(result.confidence, set_result.confidence)
            result.annotations.extend(set_result.annotations)
            result.rule_ids.extend(set_result.rule_ids)
            filters.extend(f for f in set_result.dax.split("\x00") if f)
        if node.total:
            filters.append("ALLSELECTED()")
            result.rule_ids.append("qlik-total-qualifier")
            result.confidence = min(result.confidence, 0.82)
        if filters:
            core = f"CALCULATE({core}, {', '.join(filters)})"
        result.dax = core
        return result

    @staticmethod
    def _fill_template(template: str, args: list[str], result: EmitResult, fn: str) -> str:
        out = template
        for i in range(8):
            placeholder = f"{{{i}}}"
            if placeholder in out:
                if i < len(args):
                    out = out.replace(placeholder, args[i])
                else:
                    # optional argument missing: drop trailing ", {i}" if present
                    out = out.replace(f", {placeholder}", "").replace(placeholder, "")
                    result.annotations.append(
                        f"{fn}: optional argument {i + 1} absent; template arg dropped — verify"
                    )
                    result.confidence = min(result.confidence, 0.8)
        return out

    def _emit_aggr(self, node: FuncCall) -> EmitResult:
        result = EmitResult(dax="")
        if not node.args:
            return EmitResult(dax="/* empty Aggr */", confidence=0.0)
        from bimigrate.convert.ast_nodes import walk

        inner = result.merge(self._emit(node.args[0]))
        dims = [result.merge(self._emit(a)) for a in node.args[1:]]
        nested = any(isinstance(d, FuncCall) and d.name.lower() == "aggr" for d in walk(node.args[0]))
        dims_part = ", ".join(dims)
        result.dax = f"SUMMARIZE('{self.table_hint}', {dims_part}, " f'"@value", CALCULATE({inner}))'
        result.rule_ids.append("qlik-aggr")
        result.confidence = min(result.confidence, 0.30 if nested else 0.72)
        result.annotations.append(
            "Aggr -> SUMMARIZE table expression: wrap in an iterator (AVERAGEX/MAXX...) "
            "matching the outer aggregation"
        )
        if nested:
            result.annotations.append("nested Aggr: MANUAL tier")
        return result

    def _emit_tableau_datefunc(self, node: FuncCall) -> EmitResult:
        result = EmitResult(dax="")
        part_node = node.args[0] if node.args else None
        part = part_node.value.lower() if isinstance(part_node, Str) else ""
        rest = [result.merge(self._emit(a)) for a in node.args[1:]]
        name = node.name.lower()

        if name == "datediff" and len(rest) == 2:
            interval = _DATEPART_TO_DAX_INTERVAL.get(part)
            if interval:
                result.dax = f"DATEDIFF({rest[0]}, {rest[1]}, {interval})"
                result.confidence = min(result.confidence, 0.92)
                result.rule_ids.append("tabl-fn-datediff")
                return result
        if name == "dateadd" and len(rest) == 2:
            if part == "month":
                result.dax = f"EDATE({rest[1]}, {rest[0]})"
                result.confidence = min(result.confidence, 0.9)
            elif part == "year":
                result.dax = f"EDATE({rest[1]}, 12 * ({rest[0]}))"
                result.confidence = min(result.confidence, 0.88)
            elif part == "day":
                result.dax = f"{rest[1]} + ({rest[0]})"
                result.confidence = min(result.confidence, 0.9)
            else:
                result.dax = f"/* DATEADD {part} */ {rest[1]}"
                result.confidence = 0.4
                result.annotations.append(f"DATEADD with part '{part}': manual rewrite")
            result.rule_ids.append("tabl-fn-dateadd")
            return result
        if name == "datepart" and len(rest) == 1:
            extractor = _DATEPART_EXTRACTOR.get(part)
            if extractor:
                result.dax = f"{extractor}({rest[0]})"
                result.confidence = min(result.confidence, 0.92)
                result.rule_ids.append("tabl-fn-datepart")
                return result
        if name == "datename" and len(rest) == 1:
            fmt = {"month": "MMMM", "weekday": "dddd", "year": "yyyy"}.get(part)
            if fmt:
                result.dax = f'FORMAT({rest[0]}, "{fmt}")'
                result.confidence = min(result.confidence, 0.88)
                result.rule_ids.append("tabl-fn-datename")
                return result
        if name == "datetrunc" and len(rest) == 1:
            trunc = {
                "year": f"DATE(YEAR({rest[0]}), 1, 1)",
                "quarter": f"DATE(YEAR({rest[0]}), 3 * QUARTER({rest[0]}) - 2, 1)",
                "month": f"DATE(YEAR({rest[0]}), MONTH({rest[0]}), 1)",
                "day": rest[0],
            }.get(part)
            if trunc:
                result.dax = trunc
                result.confidence = min(result.confidence, 0.85)
                result.rule_ids.append("tabl-fn-datetrunc")
                return result
        result.dax = f"/* {node.name}('{part}', ...) */"
        result.confidence = 0.4
        result.annotations.append(f"{node.name} with date part '{part}': manual rewrite")
        return result

    # -- Qlik set analysis ---------------------------------------------------------

    def _set_to_filters(self, set_expr: SetExpression) -> EmitResult:
        """Returns filter arguments joined by NUL in .dax (caller splits)."""
        result = EmitResult(dax="")
        filters: list[str] = []

        if set_expr.identifier == "1":
            filters.append("REMOVEFILTERS()")
            result.rule_ids.append("qlik-set-all")
            result.confidence = min(result.confidence, 0.88)
            result.annotations.append(
                "{1}: REMOVEFILTERS() ignores all filters — "
                "consider ALLSELECTED if page filters should persist"
            )
        elif set_expr.identifier not in ("$",):
            result.confidence = min(result.confidence, 0.30)
            result.rule_ids.append("qlik-alternate-state")
            result.annotations.append(
                f"alternate state '{set_expr.identifier}': no DAX equivalent — "
                "redesign with bookmarks / field parameters (MANUAL)"
            )

        for mod in set_expr.modifiers:
            col = self.resolve(mod.field_name)
            if mod.op == "clear":
                filters.append(f"REMOVEFILTERS({col})")
                result.rule_ids.append("qlik-set-clear-field")
                result.confidence = min(result.confidence, 0.88)
            elif mod.is_element_func:
                result.confidence = min(result.confidence, 0.62)
                result.rule_ids.append("qlik-set-element-func")
                filters.append(f"/* P()/E() element set on {col}: CALCULATETABLE pattern */")
                result.annotations.append(
                    f"element function on {mod.field_name}: rewrite as "
                    "CALCULATETABLE(VALUES(...)) filter — review"
                )
            elif mod.is_dollar:
                result.confidence = min(result.confidence, 0.65)
                result.rule_ids.append("qlik-set-dollar-expansion")
                filters.append(f"{col} = [{mod.field_name} Selected Value]")
                result.annotations.append(
                    f"dollar expansion in set modifier on {mod.field_name}: map the variable "
                    "to a measure/what-if parameter"
                )
            elif mod.is_search:
                result.confidence = min(result.confidence, 0.55)
                filters.append(f"/* search-string filter on {col} */")
                result.annotations.append(
                    f"search-string elements on {mod.field_name} need manual FILTER rewrite"
                )
            else:
                values = ", ".join(self._lit(v) for v in mod.elements)
                if mod.op == "=":
                    if len(mod.elements) == 1:
                        filters.append(f"{col} = {self._lit(mod.elements[0])}")
                    else:
                        filters.append(f"{col} IN {{{values}}}")
                    result.rule_ids.append("qlik-set-field-value")
                    result.confidence = min(result.confidence, 0.85)
                elif mod.op == "-=":
                    if len(mod.elements) == 1:
                        filters.append(f"{col} <> {self._lit(mod.elements[0])}")
                    else:
                        filters.append(f"NOT {col} IN {{{values}}}")
                    result.rule_ids.append("qlik-set-exclude")
                    result.confidence = min(result.confidence, 0.82)
                else:  # += / *= union & intersect modes
                    filters.append(f"/* {mod.op} on {col}: union/intersect selection mode */")
                    result.confidence = min(result.confidence, 0.5)
                    result.annotations.append(
                        f"{mod.op} selection mode on {mod.field_name} has no direct "
                        "CALCULATE analogue — review"
                    )
        result.dax = "\x00".join(filters)
        return result

    @staticmethod
    def _lit(value: str) -> str:
        try:
            float(value)
            return value
        except ValueError:
            escaped = value.replace('"', '""')
            return f'"{escaped}"'
