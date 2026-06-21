"""Unit tests for the lint-safe SQL construction helpers.

``build_sql``/``sql_placeholders`` keep dynamically-sized fragments (variable
length ``IN (...)`` lists, optional ``WHERE`` conditions) out of format
expressions while producing fully-parameterized statements. These tests pin the
contract: the assembled strings are exactly what callers expect, only ``?`` bind
markers are interpolated, and the empty-``IN`` guard is enforced.
"""

from __future__ import annotations

import sqlite3

import pytest

from mediarefinery.service._sql import build_sql, sql_placeholders


def test_sql_placeholders_returns_comma_separated_markers() -> None:
    assert sql_placeholders(1) == "?"
    assert sql_placeholders(3) == "?, ?, ?"


def test_sql_placeholders_rejects_non_positive_count() -> None:
    with pytest.raises(ValueError):
        sql_placeholders(0)


def test_build_sql_concatenates_fragments_into_expected_statement() -> None:
    query = build_sql(
        "SELECT id FROM t WHERE x IN (",
        sql_placeholders(2),
        ")",
    )
    assert query == "SELECT id FROM t WHERE x IN (?, ?)"


def test_build_sql_statement_binds_values_not_string_interpolation() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t (x) VALUES (?)", [(1,), (2,), (3,)])
    wanted = [1, 3]
    query = build_sql(
        "SELECT x FROM t WHERE x IN (",
        sql_placeholders(len(wanted)),
        ")",
    )
    rows = sorted(row[0] for row in conn.execute(query, wanted).fetchall())
    assert rows == [1, 3]
