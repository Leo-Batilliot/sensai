"""SQL guard: classify a statement without running it, and run reads on a locked connection.

The classification never relies on the model, nor on keywords alone: SQLite prepares the
statement behind EXPLAIN (nothing runs) and an authorizer collects every action the
statement would perform. The allowed actions are a whitelist: anything not known to be a
read or a plain write is forbidden.

Reads then run on a second connection that refuses everything else, three times over:
read-only file (mode=ro), PRAGMA query_only, and an authorizer that denies any action
outside the read whitelist.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

READ = "read"
WRITE = "write"
FORBIDDEN = "forbidden"

SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)

READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, SQLITE_RECURSIVE}
WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT: "INSERT",
    sqlite3.SQLITE_UPDATE: "UPDATE",
    sqlite3.SQLITE_DELETE: "DELETE",
}

FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}

# VACUUM INTO writes a file without calling the authorizer: the first keyword is checked too
READ_KEYWORDS = {"SELECT", "WITH", "VALUES"}
WRITE_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "WITH"}
_LEADING_COMMENTS = re.compile(r"^(\s+|--[^\n]*(\n|$)|/\*.*?\*/)*", re.DOTALL)
# Checked between batches of SQLite virtual machine instructions
PROGRESS_STEPS = 1000


@dataclass(frozen=True)
class Classification:
    kind: str  # READ, WRITE or FORBIDDEN
    operations: tuple[str, ...]  # INSERT, UPDATE, DELETE for a write
    tables: tuple[str, ...]  # tables read or written
    reason: str  # why the statement is forbidden, in French; "" otherwise

    @property
    def operation(self) -> str | None:
        """The main write operation, for the impact preview: DELETE beats UPDATE beats INSERT."""
        for name in ("DELETE", "UPDATE", "INSERT"):
            if name in self.operations:
                return name
        return None


class QueryTimeout(Exception):
    """The statement ran longer than its time limit and was interrupted."""


def readonly_uri(path: str | Path) -> str:
    # as_uri() percent-encodes spaces and accents ("3eme année")
    return Path(path).resolve().as_uri() + "?mode=ro"


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """Read-only connection without authorizer, for the server's own queries."""
    conn = sqlite3.connect(readonly_uri(path), uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def strip_leading_comments(sql: str) -> str:
    """The statement without the spaces and comments that come before it."""
    comments = _LEADING_COMMENTS.match(sql)  # always matches, possibly empty
    return sql[comments.end() :] if comments else sql


def first_keyword(sql: str) -> str:
    """First word of the statement, upper-case, after leading spaces and comments."""
    match = re.match(r"[A-Za-z]+", strip_leading_comments(sql))
    return match.group(0).upper() if match else ""


class SqlGuard:
    """Classifies statements and runs reads on one database, with a time limit."""

    def __init__(self, path: str | Path, timeout_ms: int) -> None:
        self.path = path
        self.timeout_ms = timeout_ms
        self._actions: list[tuple[int, str | None, str | None]] = []
        self._reader = open_readonly(path)
        self._reader.set_authorizer(_allow_reads_only)
        self._deadline = math.inf
        self._reader.set_progress_handler(self._past_deadline, PROGRESS_STEPS)

    def close(self) -> None:
        self._reader.close()

    def classify(self, sql: str) -> Classification:
        """Classify one statement. A syntax or schema error raises sqlite3.Error."""
        keyword = first_keyword(sql)
        if not keyword:
            return _forbidden("requête vide")
        self._actions.clear()
        # A fresh connection each time: a connection caches its prepared statements, and
        # the authorizer is only called when a statement is prepared. With a cache, the
        # same SQL classified twice would show no action the second time (and
        # cached_statements=0 does not disable the cache before Python 3.12).
        explain = open_readonly(self.path)
        try:
            explain.set_authorizer(self._collect)
            explain.execute("EXPLAIN " + sql)
        except (sqlite3.ProgrammingError, sqlite3.Warning) as exc:
            # "You can only execute one statement at a time": ProgrammingError from
            # Python 3.12, sqlite3.Warning before
            if "one statement" in str(exc):
                return _forbidden("une seule instruction par requête")
            raise
        finally:
            explain.close()
        return _classify_actions(keyword, self._actions)

    def run_read(self, sql: str, max_rows: int) -> tuple[list[str], list[list[Any]], bool]:
        """Run a read and return (columns, rows, truncated), at most max_rows rows."""
        with self._time_limit():
            cursor = self._reader.execute(sql)
            columns = [description[0] for description in cursor.description or ()]
            rows = cursor.fetchmany(max_rows + 1)
            cursor.close()
        truncated = len(rows) > max_rows
        return columns, [[json_value(v) for v in row] for row in rows[:max_rows]], truncated

    @contextmanager
    def _time_limit(self) -> Iterator[None]:
        self._deadline = time.monotonic() + self.timeout_ms / 1000
        try:
            yield
        except sqlite3.OperationalError as exc:
            if str(exc) == "interrupted":
                raise QueryTimeout from exc
            raise
        finally:
            self._deadline = math.inf

    def _past_deadline(self) -> int:
        # A non-zero return value interrupts the statement
        return 1 if time.monotonic() > self._deadline else 0

    def _collect(
        self, action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
    ) -> int:
        self._actions.append((action, arg1, arg2))
        return sqlite3.SQLITE_OK


def _classify_actions(
    keyword: str, actions: list[tuple[int, str | None, str | None]]
) -> Classification:
    operations: set[str] = set()
    tables: set[str] = set()
    for action, arg1, arg2 in actions:
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in FORBIDDEN_FUNCTIONS:
                return _forbidden(f"fonction interdite : {arg2}")
        elif action in READ_ACTIONS:
            if action == sqlite3.SQLITE_READ and arg1:
                tables.add(arg1)
        elif action in WRITE_ACTIONS:
            if arg1 is None or arg1.lower().startswith("sqlite_"):
                return _forbidden("modification du schéma")
            operations.add(WRITE_ACTIONS[action])
            tables.add(arg1)
        else:
            # ATTACH, PRAGMA, CREATE, DROP, ALTER, BEGIN, SAVEPOINT, unknown codes...
            return _forbidden(_ACTION_NAMES.get(action, f"action SQLite {action}"))

    if operations:
        if keyword not in WRITE_KEYWORDS:
            return _forbidden(f"instruction {keyword}")
        return Classification(WRITE, tuple(sorted(operations)), tuple(sorted(tables)), "")
    if keyword not in READ_KEYWORDS:
        return _forbidden(f"instruction {keyword}")
    return Classification(READ, (), tuple(sorted(tables)), "")


def _forbidden(reason: str) -> Classification:
    return Classification(FORBIDDEN, (), (), reason)


def _allow_reads_only(
    action: int, _arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
) -> int:
    if action in READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in FORBIDDEN_FUNCTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def json_value(value: Any) -> Any:
    """A SQLite value as a JSON scalar: BLOBs become a short text, NaN and infinities null."""
    if isinstance(value, bytes):
        return f"<BLOB {len(value)} octets>"
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _action_names() -> dict[int, str]:
    # sqlite3 exports error codes and limits with the same numbers: keep the action codes
    codes = (
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "TRANSACTION",
        "SAVEPOINT",
        "ALTER_TABLE",
        "REINDEX",
        "ANALYZE",
        "CREATE_INDEX",
        "CREATE_TABLE",
        "CREATE_TEMP_INDEX",
        "CREATE_TEMP_TABLE",
        "CREATE_TEMP_TRIGGER",
        "CREATE_TEMP_VIEW",
        "CREATE_TRIGGER",
        "CREATE_VIEW",
        "CREATE_VTABLE",
        "DROP_INDEX",
        "DROP_TABLE",
        "DROP_TEMP_INDEX",
        "DROP_TEMP_TABLE",
        "DROP_TEMP_TRIGGER",
        "DROP_TEMP_VIEW",
        "DROP_TRIGGER",
        "DROP_VIEW",
        "DROP_VTABLE",
    )
    return {
        getattr(sqlite3, f"SQLITE_{code}"): code.replace("_", " ")
        for code in codes
        if hasattr(sqlite3, f"SQLITE_{code}")
    }


_ACTION_NAMES: dict[int, str] = _action_names()
