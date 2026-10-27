"""Writes on business.db: preview them in a transaction that is always rolled back, or
run them for good, with an entry in write_audit.

The preview shows what a decision needs: rows per table (cascades included), up to five
of the targeted rows, and, when the database refuses a deletion because some rows still
have records that must be kept (ON DELETE RESTRICT), how many and how to exclude them.

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
    json_value,
)
from sensai.mcp_servers.sql_toolbox.target import Target, simple_target

AUDIT_TABLE = "write_audit"
# Who approved a write: the server cannot know the person, only that SENSAI asked for
# a confirmation before calling execute_write (confirmation policy of the Engine)
APPROVED_BY = "confirmation SENSAI"
SAMPLE_SIZE = 5
SAMPLE_TEXT_LENGTH = 60
# What a refused deletion protects, by child table; other tables get a generic wording
PROTECTED_LABELS = {
    "invoices": "factures à conserver",
    "orders": "commandes à conserver",
    "payments": "paiements à conserver",
}
# Delete rules that make SQLite refuse the deletion of a parent that still has children
BLOCKING_RULES = {"RESTRICT", "NO ACTION"}


class WriteBlocked(Exception):
    """A constraint of the database rejected the whole statement (ON DELETE RESTRICT...)."""

    def __init__(self, blocked: list[dict[str, Any]], hint: str = "") -> None:
        reason = "; ".join(
            f"{entry['count']} ligne(s) visée(s) : {entry['reason']}"
            if entry["count"] is not None
            else entry["reason"]
            for entry in blocked
        )
        super().__init__(reason)
        self.blocked = blocked
        self.reason = reason
        self.hint = hint


class SqlWriter:
    """Previews and runs writes on one database."""

    def __init__(self, path: str | Path, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms
        # isolation_level=None: no implicit transaction, BEGIN and ROLLBACK are explicit
        self._conn = sqlite3.connect(Path(path), isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")  # cascades and RESTRICT apply
        self._internal = False  # set while our own statements run (see _ours)
        self._conn.set_authorizer(self._authorize)
        self._deadline = math.inf
        self._conn.set_progress_handler(self._past_deadline, PROGRESS_STEPS)

    def close(self) -> None:
        self._conn.close()

    def preview(self, sql: str, operation: str) -> dict[str, Any]:
        """Impact of the write, measured then rolled back: the database is left unchanged.

        A refused deletion has no rows affected; its blocked entries say how many targeted
        rows are protected, and "hint" gives the condition that excludes them.
        """
        target = simple_target(sql)
        with self._time_limit():
            self._conn.execute("BEGIN")
            try:
                before = self._counts()
                sample = self._sample(target)  # before the write: the rows as they are now
                try:
                    rows_affected = self._run(sql)
                except sqlite3.IntegrityError as exc:
                    blocked, hint = self._blocked(target, exc)
                    impact = _impact(operation, 0, {}, blocked, sample)
                    return impact | {"hint": hint} if hint else impact
                per_table = _differences(before, self._counts())
            finally:
                self._rollback()
        return _impact(operation, rows_affected, per_table, [], sample)

    def execute(self, sql: str, operation: str, session_id: str | None = None) -> dict[str, Any]:
        """Run the write and record it in write_audit, in one transaction."""
        with self._time_limit():
            self._conn.execute("BEGIN")
            try:
                before = self._counts()
                try:
                    rows_affected = self._run(sql)
                except sqlite3.IntegrityError as exc:
                    raise WriteBlocked(*self._blocked(simple_target(sql), exc)) from exc
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

    def _run(self, sql: str) -> int:
        """Run the write and return the rows it changed directly (cascades excluded).

        cursor.rowcount stays -1 for a write that starts with WITH: Python only counts
        statements that start with INSERT, UPDATE or DELETE. SQLite's changes() does not
        care how the statement is written.
        """
        self._conn.execute(sql)
        return int(self._conn.execute("SELECT changes()").fetchone()[0])

    def _sample(self, target: Target | None) -> list[dict[str, Any]]:
        """Up to five targeted rows, read before the write; [] for a form we do not parse."""
        if target is None:
            return []
        try:
            cursor = self._conn.execute(target.select() + f" LIMIT {SAMPLE_SIZE}")
        except sqlite3.Error:
            return []
        columns = [description[0] for description in cursor.description or ()]
        return [
            {column: _short(value) for column, value in zip(columns, row, strict=True)}
            for row in cursor.fetchall()
        ]

    def _blocked(
        self, target: Target | None, error: sqlite3.IntegrityError
    ) -> tuple[list[dict[str, Any]], str]:
        """Why a write was refused: for a plain DELETE, the targeted rows that still have
        children which forbid it, counted per child table, and the condition excluding them.
        """
        generic = [{"reason": _reason(error), "count": None}]
        if target is None or target.operation != "DELETE":
            return generic, ""
        blocked: list[dict[str, Any]] = []
        exclusions: list[str] = []
        try:
            with self._ours():
                children = self._blocking_children(target.table)
            for child, column, parent_column in children:
                exists = (
                    f'EXISTS (SELECT 1 FROM "{child}" WHERE "{child}"."{column}"'
                    f' = {target.table_sql}."{parent_column}")'
                )
                count = self._conn.execute(target.select("COUNT(*)", exists)).fetchone()[0]
                if count:
                    label = PROTECTED_LABELS.get(child, f"lignes liées dans {child} à conserver")
                    blocked.append({"reason": label, "count": count})
                    exclusions.append(f"NOT {exists}")
        except sqlite3.Error:
            return generic, ""
        if not blocked:
            return generic, ""
        conditions = ([f"({target.where})"] if target.where else []) + exclusions
        corrected = f"DELETE FROM {target.table_sql} WHERE " + " AND ".join(conditions)
        hint = f"Requête qui exclut les lignes protégées : {corrected}"
        return blocked, hint

    def _blocking_children(self, table: str) -> list[tuple[str, str, str]]:
        """(child table, child column, parent column) of the foreign keys that forbid
        deleting a row of `table` while it has children. Composite keys are left out."""
        tables = [
            name
            for (name,) in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        primary = [
            name
            for name, is_pk in self._conn.execute(
                "SELECT name, pk FROM pragma_table_info(?) ORDER BY pk", (table,)
            )
            if is_pk
        ]
        children: list[tuple[str, str, str]] = []
        for child in tables:
            keys: dict[int, list[tuple[str, str, str | None, str]]] = {}
            for key_id, parent, column, parent_column, on_delete in self._conn.execute(
                'SELECT id, "table", "from", "to", on_delete FROM pragma_foreign_key_list(?)',
                (child,),
            ):
                keys.setdefault(key_id, []).append((parent, column, parent_column, on_delete))
            for columns in keys.values():
                if len(columns) != 1:
                    continue
                parent, column, parent_column, on_delete = columns[0]
                if parent.lower() != table.lower() or on_delete.upper() not in BLOCKING_RULES:
                    continue
                referenced = parent_column or (primary[0] if len(primary) == 1 else "rowid")
                children.append((child, column, referenced))
        return children

    def _rollback(self) -> None:
        # An interrupted write already rolled the whole transaction back (SQLite does it)
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def _audit(self, sql: str, rows_affected: int, session_id: str | None) -> int:
        with self._ours():
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

    @contextmanager
    def _ours(self) -> Iterator[None]:
        """Our own statements (audit, analysis of a refused deletion) pass the authorizer:
        they write write_audit and read the schema with pragma functions."""
        self._internal = True
        try:
            yield
        finally:
            self._internal = False

    def _authorize(
        self, action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None
    ) -> int:
        if self._internal:
            return sqlite3.SQLITE_OK
        if action in READ_ACTIONS or action == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            forbidden = (arg2 or "").lower() in FORBIDDEN_FUNCTIONS
            return sqlite3.SQLITE_DENY if forbidden else sqlite3.SQLITE_OK
        if action in WRITE_ACTIONS:
            table = (arg1 or "").lower()
            if table.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            if table == AUDIT_TABLE:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY


def _impact(
    operation: str,
    rows_affected: int,
    per_table: dict[str, int],
    blocked: list[dict[str, Any]],
    sample: list[dict[str, Any]],
) -> dict[str, Any]:
    """The shape of confirmation_required.impact."""
    return {
        "operation": operation,
        "rows_affected": rows_affected,
        "per_table": per_table,
        "sample": sample,
        "blocked": blocked,
    }


def _short(value: Any) -> Any:
    """A sample value as JSON, long texts cut so that the confirmation stays readable."""
    value = json_value(value)
    if isinstance(value, str) and len(value) > SAMPLE_TEXT_LENGTH:
        return value[: SAMPLE_TEXT_LENGTH - 1] + "…"
    return value


def _differences(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Signed change of each table that changed: -25 for 25 deleted rows."""
    return {table: after[table] - count for table, count in before.items() if after[table] != count}


def _reason(exc: sqlite3.IntegrityError) -> str:
    message = str(exc)
    if "FOREIGN KEY" in message:
        return "des lignes liées doivent être conservées (par exemple des factures)"
    return f"contrainte de la base non respectée ({message})"
