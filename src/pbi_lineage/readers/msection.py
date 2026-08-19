"""Parse a DataMashup `Formulas/Section1.m` into its shared queries.

This is where a large class of PBIX files keeps the *only* copy of their
Power Query. In those files the model's partition carries an M-engine
wrapper — `SELECT * FROM [Sales]` — and nothing else, so a reader that
looks only at partitions sees a model with no upstream at all: no data
source, no column lineage, no impact analysis. The real query is here.

Splitting on `;` needs a scanner rather than a regex: a semicolon inside
a string literal, a comment, or a quoted identifier does not end a query,
and M string literals escape a quote by doubling it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SharedQuery:
    """One `shared <name> = <expression>;` from the section document."""

    name: str
    expression: str
    is_parameter: bool = False
    is_function: bool = False


def _strip_meta(expression: str) -> str:
    """Drop a trailing `meta [...]` record — it carries the parameter's UI
    metadata, never anything that reads a data source."""
    marker = expression.rfind("meta")
    if marker <= 0 or not expression[marker:].lstrip("meta").lstrip().startswith("["):
        return expression
    before = expression[marker - 1]
    return expression[:marker].rstrip() if before.isspace() else expression


def split_section(text: str) -> list[str]:
    """The top-level `;`-separated statements of a section document."""
    statements: list[str] = []
    start = 0
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            index += 1
            while index < length:
                if text[index] == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        index += 2
                        continue
                    break
                index += 1
        elif char == "#" and text.startswith('#"', index):
            index += 2
            while index < length:
                if text[index] == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        index += 2
                        continue
                    break
                index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close < 0 else close + 1
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == ";" and depth <= 0:
            statements.append(text[start:index])
            start = index + 1
        index += 1
    tail = text[start:]
    if tail.strip():
        statements.append(tail)
    return statements


def _strip_leading_trivia(text: str) -> str:
    """Whitespace and comments before a declaration.

    A query is commonly preceded by the comment describing it, and the
    comment belongs to the statement that follows the previous `;`. Without
    this, `// what this does` on the line above `shared Sales = …` hides the
    query entirely — the parser looks for `shared` and finds a slash.
    """
    body = text.lstrip()
    while True:
        if body.startswith("//"):
            newline = body.find("\n")
            if newline < 0:
                return ""
            body = body[newline + 1 :].lstrip()
        elif body.startswith("/*"):
            close = body.find("*/")
            if close < 0:
                return ""
            body = body[close + 2 :].lstrip()
        else:
            return body


def _unquote_name(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('#"') and raw.endswith('"'):
        return raw[2:-1].replace('""', '"')
    return raw


def parse_section(text: str) -> list[SharedQuery]:
    """Every `shared` query in a section document, in file order."""
    queries: list[SharedQuery] = []
    for statement in split_section(text):
        body = _strip_leading_trivia(statement)
        if not body:
            continue
        # an attribute block may precede the declaration: [Description="…"]
        while body.startswith("["):
            close = _matching_bracket(body)
            if close < 0:
                break
            body = _strip_leading_trivia(body[close + 1 :])
        for keyword in ("shared ", "shared\n", "shared\t"):
            if body.startswith(keyword):
                body = body[len(keyword) :].lstrip()
                break
        else:
            continue  # `section Section1` and anything else that is not a query
        equals = _top_level_equals(body)
        if equals < 0:
            continue
        name = _unquote_name(body[:equals])
        expression = _strip_meta(body[equals + 1 :].strip())
        if not name or not expression:
            continue
        queries.append(
            SharedQuery(
                name=name,
                expression=expression,
                # A parameter is a bare literal with metadata, not a `let`;
                # a function declares its arguments before `=>`.
                is_parameter=not expression.lstrip().lower().startswith("let")
                and "=>" not in expression.split("\n", 1)[0],
                is_function="=>" in expression,
            )
        )
    return queries


def _matching_bracket(text: str) -> int:
    depth = 0
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _top_level_equals(text: str) -> int:
    """The `=` that separates the query name from its body — not one inside
    a quoted name, and not the `=` of `=>` or a comparison."""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "#" and text.startswith('#"', index):
            index += 2
            while index < length and text[index] != '"':
                index += 1
        elif char == "=":
            if index + 1 < length and text[index + 1] in "=>":
                index += 2
                continue
            return index
        elif char in "([{":
            return -1  # a function's argument list: not a plain assignment
        index += 1
    return -1
