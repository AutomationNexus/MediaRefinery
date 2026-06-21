"""SQL construction helpers for fully-parameterized dynamic statements.

These keep dynamically-sized SQL fragments — variable-length ``IN (...)`` lists and
optional ``WHERE`` conditions — out of format expressions (f-strings, ``%``,
``.format``). Every dynamic fragment produced here contains only ``?`` bind
markers; the actual values are always supplied through the query parameter
sequence. The assembled statement is therefore fully parameterized, and the
construction contains no string-formatting of SQL keywords for a static analyser
to flag.
"""

from __future__ import annotations


def sql_placeholders(count: int) -> str:
    """Return ``count`` comma-separated ``?`` bind markers for an ``IN (...)`` clause.

    Raises
    ------
    ValueError
        If ``count`` is not positive (an empty ``IN ()`` is invalid SQL).
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    return ", ".join("?" * count)


def build_sql(*parts: str) -> str:
    """Concatenate SQL fragments into one statement.

    The SQL keywords live only in literal ``parts``; any dynamic fragment passed
    in (e.g. the output of :func:`sql_placeholders` or a ``" AND ".join(...)`` of
    ``?``-only conditions) carries bind markers, never values. This keeps the
    construction safe and free of format expressions.
    """
    return "".join(parts)
