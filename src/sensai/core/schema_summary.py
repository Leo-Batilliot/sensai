"""Compact description of business.db for the system prompt (CORE-08).

Every word of the prompt costs context (num_ctx 8192), so the schema is written in a
dense, SQL-like form the model reads well: one line per table with its columns, types,
keys, links and allowed values, then the column notes and a couple of example rows.

Everything is read from the database itself: PRAGMA table_info and foreign_key_list,
plus the CHECK constraints and `--` comments kept in sqlite_master.sql (DATA-01 writes
them on purpose to be shown to the model). The file is opened read-only.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path
from typing import Any

# Internal log of confirmed writes: never queried by the agent
EXCLUDED_TABLES = frozenset({"write_audit"})
SAMPLE_ROWS = 2
MAX_SAMPLE_TEXT = 30  # characters of a text value in the examples

_IN_LIST = re.compile(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)\s*\)", re.IGNORECASE)
_DATE_CHECK = re.compile(r"date\((\w+)\)\s+IS\s+\1", re.IGNORECASE)
_COMMENT = re.compile(r"^\s*(\w+)\b.*?--\s*(.+?)\s*$")


def compact_schema(db_path: str | Path, sample_rows: int = SAMPLE_ROWS) -> str:
    """The schema of the database at db_path, as text for the system prompt."""
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()
        return "\n".join(
            _describe_table(conn, name, sql, sample_rows)
            for name, sql in tables
            if name not in EXCLUDED_TABLES
        )


def _describe_table(conn: sqlite3.Connection, table: str, sql: str, sample_rows: int) -> str:
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    links = {
        row[3]: f"{row[2]}.{row[4]}"  # from column -> "table.column"
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
    }
    allowed = {column: _values(values) for column, values in _IN_LIST.findall(sql)}
    dates = set(_DATE_CHECK.findall(sql))
    names = {column[1] for column in columns}
    notes = [
        f"{match.group(1)} = {match.group(2)}"
        for line in sql.splitlines()
        if (match := _COMMENT.match(line)) and match.group(1) in names
    ]

    parts = []
    for _cid, name, type_, _notnull, _default, pk in columns:
        text = f"{name} {type_}"
        if pk:
            text += " PK"
        if name in dates:
            text += " date"
        if name in allowed:
            text += f" IN ({allowed[name]})"
        if name in links:
            text += f" -> {links[name]}"
        parts.append(text)

    count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    lines = [f"{table} ({count} lignes) : {', '.join(parts)}"]
    if notes:
        lines.append(f"  notes : {' ; '.join(notes)}")
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ?', (sample_rows,))
    examples = [f"({', '.join(_sample(value) for value in row)})" for row in rows]
    if examples:
        lines.append(f"  exemples : {', '.join(examples)}")
    return "\n".join(lines)


def _values(raw: str) -> str:
    """Values of a CHECK ... IN (...) list without spaces: 'TPE', 'PME' -> 'TPE','PME'."""
    return ",".join(value.strip() for value in raw.split(","))


def _sample(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        text = value if len(value) <= MAX_SAMPLE_TEXT else value[: MAX_SAMPLE_TEXT - 1] + "…"
        return "'" + text.replace("'", "''") + "'"
    return str(value)
