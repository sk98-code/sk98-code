"""AST node types shared by the Tableau / Qlik / Spotfire expression parsers."""

from __future__ import annotations

from dataclasses import dataclass, field


class Node:
    """Base class for expression AST nodes."""


@dataclass
class Num(Node):
    value: str


@dataclass
class Str(Node):
    value: str  # without quotes


@dataclass
class Bool(Node):
    value: bool


@dataclass
class NullLit(Node):
    pass


@dataclass
class FieldRef(Node):
    name: str


@dataclass
class Ident(Node):
    name: str


@dataclass
class DollarExpansion(Node):
    """Qlik $(variable) expansion."""

    name: str


@dataclass
class UnaryOp(Node):
    op: str
    operand: Node


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class SetModifier(Node):
    """One field modifier inside Qlik set analysis: Field={...} / Field-= / Field=."""

    field_name: str
    op: str  # '=', '-=', '+=', '*=', 'clear'
    elements: list[str] = field(default_factory=list)
    raw: str = ""
    is_dollar: bool = False  # element list contains $(...) expansion
    is_search: bool = False  # element list contains search strings ("*x*", ">5")
    is_element_func: bool = False  # P(...) / E(...)


@dataclass
class SetExpression(Node):
    """Qlik set expression {state<modifiers>}."""

    identifier: str = "$"  # '$', '1', or an alternate state name
    modifiers: list[SetModifier] = field(default_factory=list)
    raw: str = ""


@dataclass
class FuncCall(Node):
    name: str
    args: list[Node] = field(default_factory=list)
    set_expr: SetExpression | None = None  # Qlik aggregation set analysis
    total: bool = False  # Qlik TOTAL qualifier
    total_dims: list[str] = field(default_factory=list)
    distinct: bool = False  # Count(DISTINCT x)


@dataclass
class IfExpr(Node):
    """Tableau IF..THEN..ELSEIF..ELSE..END (list of (cond, value) branches)."""

    branches: list[tuple[Node, Node]]
    else_value: Node | None = None


@dataclass
class CaseExpr(Node):
    """CASE [subject] WHEN a THEN b ... ELSE c END."""

    subject: Node | None
    whens: list[tuple[Node, Node]]
    else_value: Node | None = None


@dataclass
class LodExpr(Node):
    """Tableau {FIXED|INCLUDE|EXCLUDE dims : expr}."""

    kind: str  # fixed | include | exclude
    dims: list[str]
    expr: Node


@dataclass
class OverExpr(Node):
    """Spotfire <agg> OVER (navigation)."""

    expr: Node
    navigation: Node  # FuncCall like AllPrevious([Axis.X]) or FieldRef


@dataclass
class ThenExpr(Node):
    """Spotfire multi-stage 'expr THEN stage' chain."""

    stages: list[Node]


def walk(node: Node):
    """Yield node and all descendants."""
    yield node
    for attr in vars(node).values() if hasattr(node, "__dict__") else []:
        if isinstance(attr, Node):
            yield from walk(attr)
        elif isinstance(attr, list):
            for item in attr:
                if isinstance(item, Node):
                    yield from walk(item)
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, Node):
                            yield from walk(sub)
