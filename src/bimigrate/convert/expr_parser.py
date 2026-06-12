"""Expression tokenizer + Pratt parser for Tableau, Qlik and Spotfire
expression languages.

One shared core with dialect switches:
* tableau  — IF..THEN..ELSEIF..ELSE..END, CASE..WHEN..END, LOD braces,
             [Field] refs, // comments, '=' equality;
* qlik     — set analysis {<...>} inside aggregations, TOTAL qualifier,
             DISTINCT, $(var) expansions, bare-identifier field refs;
* spotfire — OVER (...) navigation, THEN chains, CASE WHEN, [Field] refs.

The parser is deliberately forgiving: anything it cannot parse raises
ExpressionParseError and the conversion engine falls back to regex rules
or the MANUAL tier — a parse failure must never kill a batch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    SetModifier,
    Str,
    ThenExpr,
    UnaryOp,
)


class ExpressionParseError(Exception):
    pass


@dataclass
class Token:
    kind: str  # NUM STR FIELD IDENT OP LPAREN RPAREN COMMA LBRACE RBRACE COLON DOLLAR SET EOF
    value: str
    pos: int


_KEYWORD_OPS = {"and": "AND", "or": "OR", "not": "NOT"}

_TWO_CHAR_OPS = ("<=", ">=", "<>", "!=", "==", "-=", "+=", "*=")


def tokenize(text: str, dialect: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch == "[":
            end = text.find("]", i)
            if end < 0:
                raise ExpressionParseError(f"unterminated field ref at {i}")
            tokens.append(Token("FIELD", text[i + 1 : end], i))
            i = end + 1
            continue
        if ch == "'" or (ch == '"' and dialect != "qlik"):
            quote = ch
            j = i + 1
            buf = []
            while j < n:
                if text[j] == quote:
                    if j + 1 < n and text[j + 1] == quote:  # doubled-quote escape
                        buf.append(quote)
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            if j >= n:
                raise ExpressionParseError(f"unterminated string at {i}")
            tokens.append(Token("STR", "".join(buf), i))
            i = j + 1
            continue
        if ch == '"' and dialect == "qlik":  # quoted field name in Qlik
            end = text.find('"', i + 1)
            if end < 0:
                raise ExpressionParseError(f"unterminated quoted name at {i}")
            tokens.append(Token("FIELD", text[i + 1 : end], i))
            i = end + 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m = re.match(r"\d*\.?\d+([eE][+-]?\d+)?", text[i:])
            tokens.append(Token("NUM", m.group(0), i))
            i += m.end()
            continue
        if ch == "$" and text.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(Token("DOLLAR", text[i + 2 : j - 1], i))
            i = j
            continue
        if ch == "{":
            # Tableau LOD brace or Qlik set expression: capture balanced content raw
            depth, j = 1, i + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                raise ExpressionParseError(f"unterminated brace at {i}")
            tokens.append(Token("SET", text[i + 1 : j - 1], i))
            i = j
            continue
        if ch.isalpha() or ch == "_" or ch == "#":
            m = re.match(r"[A-Za-z_#][\w.#]*", text[i:])
            word = m.group(0)
            lower = word.lower()
            if lower in _KEYWORD_OPS:
                tokens.append(Token("OP", _KEYWORD_OPS[lower], i))
            elif lower in ("true", "false"):
                tokens.append(Token("BOOL", lower, i))
            elif lower == "null":
                tokens.append(Token("NULL", word, i))
            else:
                tokens.append(Token("IDENT", word, i))
            i += m.end()
            continue
        for op in _TWO_CHAR_OPS:
            if text.startswith(op, i):
                tokens.append(Token("OP", op, i))
                i += 2
                break
        else:
            if ch in "+-*/%^=<>&!":
                tokens.append(Token("OP", ch, i))
            elif ch == "(":
                tokens.append(Token("LPAREN", ch, i))
            elif ch == ")":
                tokens.append(Token("RPAREN", ch, i))
            elif ch == ",":
                tokens.append(Token("COMMA", ch, i))
            elif ch == ":":
                tokens.append(Token("COLON", ch, i))
            elif ch == ";":
                tokens.append(Token("SEMI", ch, i))
            else:
                raise ExpressionParseError(f"unexpected character {ch!r} at {i}")
            i += 1
    tokens.append(Token("EOF", "", n))
    return tokens


_PRECEDENCE = {
    "OR": 1,
    "AND": 2,
    "=": 3,
    "==": 3,
    "<>": 3,
    "!=": 3,
    "<": 3,
    ">": 3,
    "<=": 3,
    ">=": 3,
    "LIKE": 3,
    "&": 4,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
    "^": 6,
}

_STRUCT_KEYWORDS = {"if", "then", "elseif", "else", "end", "case", "when", "over", "total", "distinct"}


class ExpressionParser:
    def __init__(self, text: str, dialect: str):
        self.dialect = dialect
        self.text = text
        self.tokens = tokenize(text, dialect)
        self.pos = 0

    # -- helpers ----------------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def next(self) -> Token:
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, kind: str, value: str | None = None) -> Token:
        tok = self.next()
        if tok.kind != kind or (value is not None and tok.value.lower() != value.lower()):
            raise ExpressionParseError(f"expected {value or kind}, got {tok.value!r} at {tok.pos}")
        return tok

    def at_keyword(self, *words: str) -> bool:
        tok = self.peek()
        return tok.kind == "IDENT" and tok.value.lower() in words

    # -- entry --------------------------------------------------------------

    def parse(self) -> Node:
        node = self.parse_expr()
        if self.dialect == "spotfire" and self.at_keyword("then"):
            stages = [node]
            while self.at_keyword("then"):
                self.next()
                stages.append(self.parse_expr())
            node = ThenExpr(stages=stages)
        if self.peek().kind != "EOF":
            tok = self.peek()
            raise ExpressionParseError(f"trailing input at {tok.pos}: {tok.value!r}")
        return node

    # -- Pratt core ------------------------------------------------------------

    def parse_expr(self, min_prec: int = 0) -> Node:
        left = self.parse_unary()
        while True:
            if self.dialect == "spotfire" and self.at_keyword("over"):
                self.next()
                self.expect("LPAREN")
                nav = self.parse_expr()
                self.expect("RPAREN")
                left = OverExpr(expr=left, navigation=nav)
                continue
            tok = self.peek()
            op = None
            if tok.kind == "OP" and tok.value in _PRECEDENCE:
                op = tok.value
            elif tok.kind == "IDENT" and tok.value.lower() == "like":
                op = "LIKE"
            if op is None or _PRECEDENCE[op] < min_prec:
                break
            self.next()
            right = self.parse_expr(_PRECEDENCE[op] + 1)
            left = BinOp(op=op, left=left, right=right)
        return left

    def parse_unary(self) -> Node:
        tok = self.peek()
        if tok.kind == "OP" and tok.value in ("-", "+", "NOT", "!"):
            self.next()
            return UnaryOp(op="NOT" if tok.value in ("NOT", "!") else tok.value, operand=self.parse_unary())
        return self.parse_primary()

    # -- primaries -----------------------------------------------------------------

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.kind == "NUM":
            self.next()
            return Num(tok.value)
        if tok.kind == "STR":
            self.next()
            return Str(tok.value)
        if tok.kind == "BOOL":
            self.next()
            return Bool(tok.value == "true")
        if tok.kind == "NULL":
            self.next()
            return NullLit()
        if tok.kind == "FIELD":
            self.next()
            return FieldRef(tok.value)
        if tok.kind == "DOLLAR":
            self.next()
            return DollarExpansion(tok.value)
        if tok.kind == "SET":
            return self.parse_brace(self.next())
        if tok.kind == "LPAREN":
            self.next()
            inner = self.parse_expr()
            self.expect("RPAREN")
            return inner
        if tok.kind == "IDENT":
            lower = tok.value.lower()
            if lower == "if" and self.dialect in ("tableau",):
                return self.parse_if_block()
            if lower == "case" and self.dialect in ("tableau", "spotfire"):
                return self.parse_case_block()
            if self.peek(1).kind == "LPAREN":
                return self.parse_func_call()
            self.next()
            if self.dialect == "qlik":
                return FieldRef(tok.value)  # bare identifier == field in Qlik
            return Ident(tok.value)
        raise ExpressionParseError(f"unexpected token {tok.value!r} at {tok.pos}")

    def parse_func_call(self) -> Node:
        name = self.next().value
        self.expect("LPAREN")
        call = FuncCall(name=name)
        # Qlik aggregation prologue: {set}, TOTAL [<dims>], DISTINCT
        if self.dialect == "qlik":
            if self.peek().kind == "SET":
                call.set_expr = parse_set_expression(self.next().value)
            while self.at_keyword("total", "distinct"):
                word = self.next().value.lower()
                if word == "total":
                    call.total = True
                else:
                    call.distinct = True
        if self.peek().kind != "RPAREN":
            call.args.append(self.parse_expr())
            while self.peek().kind == "COMMA":
                self.next()
                call.args.append(self.parse_expr())
        self.expect("RPAREN")
        return call

    def parse_if_block(self) -> Node:
        self.expect("IDENT", "if")
        branches: list[tuple[Node, Node]] = []
        cond = self.parse_expr()
        self._expect_keyword("then")
        value = self.parse_expr()
        branches.append((cond, value))
        else_value: Node | None = None
        while True:
            if self.at_keyword("elseif"):
                self.next()
                cond = self.parse_expr()
                self._expect_keyword("then")
                branches.append((cond, self.parse_expr()))
            elif self.at_keyword("else"):
                self.next()
                else_value = self.parse_expr()
            elif self.at_keyword("end"):
                self.next()
                return IfExpr(branches=branches, else_value=else_value)
            else:
                tok = self.peek()
                raise ExpressionParseError(f"expected ELSEIF/ELSE/END at {tok.pos}")

    def parse_case_block(self) -> Node:
        self.expect("IDENT", "case")
        subject: Node | None = None
        if not self.at_keyword("when"):
            subject = self.parse_expr()
        whens: list[tuple[Node, Node]] = []
        else_value: Node | None = None
        while True:
            if self.at_keyword("when"):
                self.next()
                test = self.parse_expr()
                self._expect_keyword("then")
                whens.append((test, self.parse_expr()))
            elif self.at_keyword("else"):
                self.next()
                else_value = self.parse_expr()
            elif self.at_keyword("end"):
                self.next()
                return CaseExpr(subject=subject, whens=whens, else_value=else_value)
            else:
                tok = self.peek()
                raise ExpressionParseError(f"expected WHEN/ELSE/END at {tok.pos}")

    def _expect_keyword(self, word: str) -> None:
        tok = self.next()
        if tok.kind != "IDENT" or tok.value.lower() != word:
            raise ExpressionParseError(f"expected {word.upper()} at {tok.pos}")

    def parse_brace(self, tok: Token) -> Node:
        """Brace content: Tableau LOD or (in expression position) a Qlik set."""
        content = tok.value.strip()
        m = re.match(r"(?i)^(FIXED|INCLUDE|EXCLUDE)\b(.*?)(?::(.*))?$", content, re.DOTALL)
        if self.dialect == "tableau" and (m or content.startswith(":")):
            if content.startswith(":"):  # {: expr} == table-scoped FIXED
                kind, dims_raw, expr_raw = "fixed", "", content[1:]
            else:
                kind = m.group(1).lower()
                dims_raw = m.group(2) or ""
                expr_raw = m.group(3) or ""
            dims = [d.strip().strip("[]") for d in dims_raw.split(",") if d.strip()]
            inner = ExpressionParser(expr_raw.strip(), self.dialect).parse()
            return LodExpr(kind=kind, dims=dims, expr=inner)
        if self.dialect == "qlik":
            return parse_set_expression(content)
        raise ExpressionParseError(f"unexpected brace expression at {tok.pos}")


# -- Qlik set-analysis sub-parser -------------------------------------------------

_SET_MODIFIER_RE = re.compile(
    r"(?P<field>\[[^\]]+\]|\"[^\"]+\"|[\w.]+)\s*(?P<op>-=|\+=|\*=|=)\s*"
    r"(?P<value>\{[^{}]*(?:\$\([^)]*\)[^{}]*)?\}|P\s*\([^)]*\)|E\s*\([^)]*\)|\$\([^)]*\)|)",
    re.DOTALL,
)


def parse_set_expression(content: str) -> SetExpression:
    content = content.strip()
    raw = content
    identifier = "$"
    mod_part = ""
    m = re.match(r"^([\w$]+)?\s*(?:<(.*)>)?$", content, re.DOTALL)
    if m:
        if m.group(1):
            identifier = m.group(1)
        mod_part = m.group(2) or ""
    elif content.startswith("<") and content.endswith(">"):
        mod_part = content[1:-1]

    modifiers: list[SetModifier] = []
    for mm in _SET_MODIFIER_RE.finditer(mod_part):
        field_name = mm.group("field").strip('[]"')
        op = mm.group("op")
        value = (mm.group("value") or "").strip()
        mod = SetModifier(field_name=field_name, op=op, raw=mm.group(0))
        if not value:
            mod.op = "clear"
        elif value.upper().startswith(("P", "E")) and "(" in value:
            mod.is_element_func = True
        elif value.startswith("$("):
            mod.is_dollar = True
        else:
            elems = value.strip("{}")
            mod.is_dollar = "$(" in elems
            parts = [e.strip() for e in _split_elements(elems) if e.strip()]
            mod.elements = [p.strip("'\"") for p in parts]
            mod.is_search = any(
                p.startswith(('"', "'"))
                and any(c in p for c in "*?><")
                or p.strip("'\"").startswith((">", "<"))
                for p in parts
            )
        modifiers.append(mod)
    return SetExpression(identifier=identifier, modifiers=modifiers, raw=raw)


def _split_elements(text: str) -> list[str]:
    parts, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_expression(text: str, dialect: str) -> Node:
    return ExpressionParser(text, dialect).parse()
