"""Which rows a simple write targets: the table and WHERE clause of `DELETE FROM t WHERE …`
or `UPDATE t SET … WHERE …`, so that the preview can show them before anything runs.

Only the plain forms are understood: one table, no alias, no `UPDATE … FROM`, no
`RETURNING`, `ORDER BY` or `LIMIT`. Anything else returns None and the preview simply
has no sample. The parsing only finds where the clauses start; checking that the SQL is a
valid write is the guard's job, done before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sensai.mcp_servers.sql_toolbox.guard import strip_leading_comments

# A table name: plain, or quoted with double quotes ("" escapes a quote)
_TABLE = r'(?P<table>"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*)'
_DELETE = re.compile(rf"DELETE\s+FROM\s+{_TABLE}", re.IGNORECASE)
_UPDATE = re.compile(rf"UPDATE\s+{_TABLE}\s+SET\b", re.IGNORECASE)
# Clauses that make the statement more than "this table, these rows"
_CHANGES_THE_TARGET = {"FROM", "RETURNING", "ORDER", "LIMIT"}


@dataclass(frozen=True)
class Target:
    operation: str  # "DELETE" or "UPDATE"
    table: str  # unquoted name
    table_sql: str  # as written in the statement, quotes included
    where: str  # condition without the WHERE keyword; "" when every row is targeted

    def select(self, columns: str = "*", extra: str = "") -> str:
        """A SELECT on the targeted rows, with an optional extra condition."""
        conditions = [f"(\n{self.where}\n)"] if self.where else []
        if extra:
            conditions.append(extra)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return f"SELECT {columns} FROM {self.table_sql}{where}"


def simple_target(sql: str) -> Target | None:
    """The table and WHERE clause of a plain DELETE or UPDATE, None for any other form."""
    sql = strip_leading_comments(sql).rstrip().rstrip(";").rstrip()
    matches = [
        (operation, match)
        for operation, pattern in (("DELETE", _DELETE), ("UPDATE", _UPDATE))
        if (match := pattern.match(sql)) is not None
    ]
    if not matches:
        return None
    operation, match = matches[0]
    table_sql = match.group("table")
    words = _top_level_words(sql, match.end())
    where_at = next((pos for pos, word in words if word == "WHERE"), None)
    before = {word for pos, word in words if where_at is None or pos < where_at}
    after = {word for pos, word in words if where_at is not None and pos > where_at}
    if operation == "DELETE" and before:
        return None  # an alias or INDEXED BY between the table and WHERE
    if (before | after) & _CHANGES_THE_TARGET:
        return None
    where = sql[where_at + len("WHERE") :].strip() if where_at is not None else ""
    if where_at is not None and not where:
        return None
    return Target(operation, _unquote(table_sql), table_sql, where)


def _unquote(name: str) -> str:
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def _top_level_words(sql: str, start: int) -> list[tuple[int, str]]:
    """Upper-case keywords outside strings, quoted names, comments and parentheses."""
    words: list[tuple[int, str]] = []
    depth = 0
    i = start
    while i < len(sql):
        char = sql[i]
        if char in "'\"`[":
            closing = "]" if char == "[" else char
            i += 1
            while i < len(sql):
                if sql[i] == closing:
                    # A doubled quote is an escaped quote inside the string
                    if closing != "]" and i + 1 < len(sql) and sql[i + 1] == closing:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = len(sql) if end == -1 else end + 2
        elif char == "(":
            depth += 1
            i += 1
        elif char == ")":
            depth -= 1
            i += 1
        elif char.isalpha() or char == "_":
            end = i
            while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            if depth == 0:
                words.append((i, sql[i:end].upper()))
            i = end
        else:
            i += 1
    return words
