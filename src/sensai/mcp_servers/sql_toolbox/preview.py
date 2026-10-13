"""Writes on business.db: preview them in a transaction that is always rolled back, or
run them for good, with an entry in write_audit.

Only statements the guard classified as writes reach this module. The write connection
still has its own barriers: an authorizer that refuses schema changes, ATTACH, PRAGMA and
any write to write_audit or to SQLite's own tables, and a time limit.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sensai.mcp_servers.sql_toolbox.guard import (
    FORBIDDEN_FUNCTIONS,
    PROGRESS_STEPS,
    READ_ACTIONS,
    WRITE_ACTIONS,
    QueryTimeout,
)

AUDIT_TABLE = "write_audit"
# Who approved a write: the server cannot know the person, only that SENSAI asked for
# a confirmation before calling execute_write (confirmation policy of the Engine)
APPROVED_BY = "confirmation SENSAI"


class WriteBlocked(Exception):
    """A constraint of the database rejected the whole statement (ON DELETE RESTRICT...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SqlWriter:
    """Previews and runs writes on one database."""

    def __init__(self, path: str | Path, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms
        # isolation_level=None: no implicit transaction, BEGIN and ROLLBACK are explicit
        self._conn = sqlite3.connect(Path(path), isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")  # cascades and RESTRICT apply
        self._internal = False  # our own statements (audit) may write write_audit
        self._conn.set_authorizer(self._authorize)
        self._deadline = math.inf
        self._conn.set_progress_handler(self._past_deadline, PROGRESS_STEPS)

    def close(self) -> None:
        self._conn.close()

    def preview(self, sql: str, operation: str) -> dict[str, Any]:
        """Impact of the write, measured then rolled back: the database is left unchanged."""
        with self._time_limit():
            self._conn.execute("BEGIN")
            try:
                before = self._counts()
                try:
                    rows_affected = self._conn.execute(sql).rowcount
                except sqlite3.IntegrityError as exc:
                    return _impact(operation, 0, {}, [{"reason": _reason(exc), "count": None}])
                per_table = _differences(before, self._counts())
            finally:
                self._rollback()
        return _impact(operation, max(rows_affected, 0), per_table, [])

    def execute(self, sql: str, operation: str, session_id: str | None = None) -> dict[str, Any]:
        """Run the write and record it in write_audit, in one transaction."""
        with self._time_limit():
            self._conn.execute("BEGIN")
            try:
                before = self._counts()
                try:
                    rows_affected = max(self._conn.execute(sql).rowcount, 0)
                except sqlite3.IntegrityError as exc:
                    raise WriteBlocked(_reason(exc)) from exc
                per_table = _differences(before, self._counts())
                audit_id = self._audit(sql, rows_affected, session_id)
            except BaseException:
                self._rollback()
                raise
            self._conn.execute("COMMIT")
        return {
            "operation": operation,
            "rows_affected": rows_affected,
            "per_table": per_table,
            "audit_id": audit_id,
        }

    def _rollback(self) -> None:
        # An interrupted write already rolled the whole transaction back (SQLite does it)
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def _audit(self, sql: str, rows_affected: int, session_id: str | None) -> int:
        self._internal = True
        try:
            cursor = self._conn.execute(
                f"INSERT INTO {AUDIT_TABLE} (at, session_id, sql, rows_affected, approved_by)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    session_id,
                    sql,
                    rows_affected,
                    APPROVED_BY,
                ),
            )
        finally:
            self._internal = False
        return int(cursor.lastrowid or 0)

    def _counts(self) -> dict[str, int]:
        """Rows per table: rowcount misses the cascades, the counts do not."""
        tables = [
            name
            for (name,) in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                f" AND name NOT LIKE 'sqlite_%' AND name <> '{AUDIT_TABLE}' ORDER BY name"
            )
        ]
        return {
            table: self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }

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
        return 1 if time.monotonic() > self._deadline else 0

    def _authorize(
        self, action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
    ) -> int:
        if action in READ_ACTIONS or action == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            forbidden = (arg2 or "").lower() in FORBIDDEN_FUNCTIONS
            return sqlite3.SQLITE_DENY if forbidden else sqlite3.SQLITE_OK
        if action in WRITE_ACTIONS:
            table = (arg1 or "").lower()
            if table.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            if table == AUDIT_TABLE and not self._internal:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY


def _impact(
    operation: str, rows_affected: int, per_table: dict[str, int], blocked: list[dict[str, Any]]
) -> dict[str, Any]:
    """The shape of confirmation_required.impact; sample stays empty for now."""
    return {
        "operation": operation,
        "rows_affected": rows_affected,
        "per_table": per_table,
        "sample": [],
        "blocked": blocked,
    }


def _differences(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Signed change of each table that changed: -25 for 25 deleted rows."""
    return {table: after[table] - count for table, count in before.items() if after[table] != count}


def _reason(exc: sqlite3.IntegrityError) -> str:
    message = str(exc)
    if "FOREIGN KEY" in message:
        return "des lignes liées doivent être conservées (par exemple des factures)"
    return f"contrainte de la base non respectée ({message})"
