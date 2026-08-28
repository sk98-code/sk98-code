"""A DAX tokenizer that extracts column and measure references.

This is the *fallback* path, used only for workspaces with no XMLA endpoint
(Pro-only), where we cannot ask the engine via DISCOVER_CALC_DEPENDENCY.
Everything it produces is tagged `heuristic`.

It deliberately stops at tokenizing. It does not build a DAX AST and it does
not try to evaluate context transitions — it answers exactly one question:
"which model objects does this expression mention?". Getting that right needs
correct handling of strings, comments, VAR names and function names, which is
most of what this module is.

Reference forms recognised:

    'Sales Table'[Amount]     quoted table + column
    Sales[Amount]             bare table + column
    [Total Sales]             unqualified — a measure, or a column of the
                              home table; resolved later against the schema
    'Sales Table'             a bare quoted table name (e.g. inside ALL())
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator

__all__ = [
    "DaxReference",
    "DaxRefKind",
    "Token",
    "TokenKind",
    "extract_dax_references",
    "extract_declared_variables",
    "tokenize_dax",
]


class TokenKind(StrEnum):
    IDENT = "ident"  # bare identifier: table name, function name, keyword
    QUOTED_IDENT = "quoted_ident"  # 'Table Name'
    BRACKET = "bracket"  # [Column Or Measure]
    STRING = "string"  # "literal"
    NUMBER = "number"
    OPERATOR = "operator"
    PUNCT = "punct"  # ( ) , etc.
    COMMENT = "comment"
    WHITESPACE = "whitespace"


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    value: str
    position: int

    @property
    def is_significant(self) -> bool:
        return self.kind not in (TokenKind.WHITESPACE, TokenKind.COMMENT)


class DaxRefKind(StrEnum):
    COLUMN = "column"  # qualified: we know the table
    MEASURE = "measure"  # resolved to a known measure
    TABLE = "table"  # bare table reference
    AMBIGUOUS = "ambiguous"  # bare [Name]: measure or home-table column


@dataclass(frozen=True, slots=True)
class DaxReference:
    kind: DaxRefKind
    name: str
    table: str = ""
    position: int = 0

    def qualified(self) -> str:
        if self.table and self.kind in (DaxRefKind.COLUMN, DaxRefKind.MEASURE):
            return f"'{self.table}'[{self.name}]"
        if self.kind == DaxRefKind.TABLE:
            return f"'{self.name}'"
        return f"[{self.name}]"


# DAX keywords that may appear where an identifier would, and are never objects.
DAX_KEYWORDS = frozenset(
    {
        "var",
        "return",
        "evaluate",
        "define",
        "measure",
        "column",
        "table",
        "order",
        "by",
        "start",
        "at",
        "asc",
        "desc",
        "true",
        "false",
        "not",
        "in",
        "and",
        "or",
        "blank",
    }
)

_OPERATOR_CHARS = frozenset("+-*/^&<>=:")
_NUMBER_RE = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?")


def tokenize_dax(text: str) -> list[Token]:
    """Tokenize a DAX expression. Never raises on malformed input."""
    return list(_scan(text or ""))


def _scan(text: str) -> Iterator[Token]:
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        start = i

        if ch in " \t\r\n":
            while i < n and text[i] in " \t\r\n":
                i += 1
            yield Token(TokenKind.WHITESPACE, text[start:i], start)
            continue

        # comments: // ... , -- ... , /* ... */
        if text.startswith("//", i) or text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            yield Token(TokenKind.COMMENT, text[start:i], start)
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            yield Token(TokenKind.COMMENT, text[start:i], start)
            continue

        # "string literal" with "" escape
        if ch == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            yield Token(TokenKind.STRING, text[start:i], start)
            continue

        # 'quoted identifier' with '' escape
        if ch == "'":
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            yield Token(TokenKind.QUOTED_IDENT, "".join(buf), start)
            continue

        # [bracketed identifier] — column or measure
        if ch == "[":
            i += 1
            buf = []
            while i < n:
                if text[i] == "]":
                    if i + 1 < n and text[i + 1] == "]":
                        buf.append("]")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            yield Token(TokenKind.BRACKET, "".join(buf), start)
            continue

        if ch.isdigit():
            match = _NUMBER_RE.match(text, i)
            i = match.end() if match else i + 1
            yield Token(TokenKind.NUMBER, text[start:i], start)
            continue

        if ch.isalpha() or ch == "_":
            while i < n and (text[i].isalnum() or text[i] in "_."):
                i += 1
            yield Token(TokenKind.IDENT, text[start:i], start)
            continue

        if ch in _OPERATOR_CHARS:
            while i < n and text[i] in _OPERATOR_CHARS:
                i += 1
            yield Token(TokenKind.OPERATOR, text[start:i], start)
            continue

        i += 1
        yield Token(TokenKind.PUNCT, ch, start)


def extract_declared_variables(tokens: list[Token]) -> set[str]:
    """Names introduced by `VAR <name> =`, which are locals, not model objects."""
    names: set[str] = set()
    significant = [t for t in tokens if t.is_significant]
    for idx, token in enumerate(significant):
        if token.kind is TokenKind.IDENT and token.value.lower() == "var":
            if idx + 1 < len(significant) and significant[idx + 1].kind is TokenKind.IDENT:
                names.add(significant[idx + 1].value.lower())
    return names


def extract_dax_references(expression: str) -> list[DaxReference]:
    """Extract every model-object reference mentioned by `expression`.

    Results are de-duplicated, in first-appearance order. Function names,
    keywords and `VAR` locals are excluded; a bare identifier is only reported
    as a table when it is not immediately followed by `(` (which would make it
    a function call).
    """
    tokens = tokenize_dax(expression)
    variables = extract_declared_variables(tokens)
    significant = [t for t in tokens if t.is_significant]

    seen: set[tuple[str, str, str]] = set()
    refs: list[DaxReference] = []

    def emit(ref: DaxReference) -> None:
        key = (ref.kind.value, ref.table.lower(), ref.name.lower())
        if key not in seen:
            seen.add(key)
            refs.append(ref)

    idx = 0
    while idx < len(significant):
        token = significant[idx]
        nxt = significant[idx + 1] if idx + 1 < len(significant) else None

        if token.kind is TokenKind.BRACKET:
            # A bracket not preceded by a table name: measure or home column.
            emit(DaxReference(DaxRefKind.AMBIGUOUS, token.value, position=token.position))
            idx += 1
            continue

        if token.kind in (TokenKind.IDENT, TokenKind.QUOTED_IDENT):
            lowered = token.value.lower()
            is_call = nxt is not None and nxt.kind is TokenKind.PUNCT and nxt.value == "("

            if nxt is not None and nxt.kind is TokenKind.BRACKET:
                # Table[Column] / 'Table'[Column]
                emit(
                    DaxReference(
                        DaxRefKind.COLUMN,
                        nxt.value,
                        table=token.value,
                        position=token.position,
                    )
                )
                idx += 2
                continue

            if token.kind is TokenKind.QUOTED_IDENT:
                # A quoted identifier is always a table name in DAX.
                emit(DaxReference(DaxRefKind.TABLE, token.value, position=token.position))
                idx += 1
                continue

            if not is_call and lowered not in DAX_KEYWORDS and lowered not in variables:
                # Bare identifier, not a call: most likely an unquoted table name.
                emit(DaxReference(DaxRefKind.TABLE, token.value, position=token.position))

        idx += 1

    return refs
