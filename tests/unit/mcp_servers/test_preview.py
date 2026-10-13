"""preview_write and execute_write on a seeded database (25 purgeable, 15 protected clients)."""

import hashlib
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sensai.data.seed import generate
from sensai.mcp import jsonrpc
from sensai.mcp_servers.sql_toolbox.preview import SqlWriter
from sensai.mcp_servers.sql_toolbox.server import SqlToolboxServer

PURGE = "DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM orders)"
NAIVE_PURGE = "DELETE FROM clients WHERE status = 'inactive'"
INFINITE_UPDATE = (
    "UPDATE products SET name = name WHERE id IN ("
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r)"
)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("seeded") / "business.db"
    generate(path, seed=42, today=date(2026, 10, 20))
    return path


@pytest.fixture
def db_path(seeded: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "business.db"
    shutil.copy(seeded, copy)
    return copy


@pytest.fixture
def server(db_path: Path) -> Iterator[SqlToolboxServer]:
    server = SqlToolboxServer(db_path, timeout_ms=300)
    yield server
    server.close()


def dump_hash(path: Path) -> str:
    conn = sqlite3.connect(path)
    digest = hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()
    conn.close()
    return digest


def count(path: Path, sql: str) -> int:
    conn = sqlite3.connect(path)
    value = conn.execute(sql).fetchone()[0]
    conn.close()
    return int(value)


def call(server: SqlToolboxServer, name: str, sql: str) -> dict[str, Any]:
    answer = server.handle(
        jsonrpc.request(1, "tools/call", {"name": name, "arguments": {"sql": sql}})
    )
    assert answer is not None
    return answer["result"]


def test_preview_is_exact_and_leaves_no_trace(server: SqlToolboxServer, db_path: Path) -> None:
    contacts = count(
        db_path,
        "SELECT COUNT(*) FROM contacts WHERE client_id IN"
        " (SELECT id FROM clients WHERE status = 'inactive' AND id NOT IN"
        " (SELECT client_id FROM orders))",
    )
    before = dump_hash(db_path)
    result = call(server, "preview_write", PURGE)
    assert result["structuredContent"] == {
        "operation": "DELETE",
        "rows_affected": 25,
        "per_table": {"clients": -25, "contacts": -contacts},  # cascade included
        "sample": [],
        "blocked": [],
    }
    assert dump_hash(db_path) == before


def test_preview_of_a_blocked_delete(server: SqlToolboxServer, db_path: Path) -> None:
    before = dump_hash(db_path)
    impact = call(server, "preview_write", NAIVE_PURGE)["structuredContent"]
    assert impact["rows_affected"] == 0 and impact["per_table"] == {}
    (blocked,) = impact["blocked"]
    assert "conservées" in blocked["reason"] and blocked["count"] is None
    assert dump_hash(db_path) == before


def test_execute_writes_and_audits(server: SqlToolboxServer, db_path: Path) -> None:
    result = call(server, "execute_write", PURGE)
    assert not result["isError"]
    data = result["structuredContent"]
    assert data["operation"] == "DELETE" and data["rows_affected"] == 25
    assert data["per_table"]["clients"] == -25
    assert count(db_path, "SELECT COUNT(*) FROM clients WHERE status = 'inactive'") == 15
    conn = sqlite3.connect(db_path)
    audit = conn.execute("SELECT id, sql, rows_affected, approved_by FROM write_audit").fetchall()
    conn.close()
    assert audit == [(data["audit_id"], PURGE, 25, "confirmation SENSAI")]


def test_a_blocked_execute_changes_nothing(server: SqlToolboxServer, db_path: Path) -> None:
    before = dump_hash(db_path)
    result = call(server, "execute_write", NAIVE_PURGE)
    assert result["isError"] and result["structuredContent"]["code"] == "SQL_ERROR"
    assert "rien n'a été modifié" in result["structuredContent"]["message"]
    assert dump_hash(db_path) == before


@pytest.mark.parametrize("tool", ["preview_write", "execute_write"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM clients",
        "DROP TABLE clients",
        "CREATE TABLE x (a)",
        "DELETE FROM clients; DELETE FROM contacts",
        "DELETE FROM write_audit",
        "PRAGMA foreign_keys = OFF",
    ],
)
def test_only_plain_writes_are_accepted(
    server: SqlToolboxServer, db_path: Path, tool: str, sql: str
) -> None:
    before = dump_hash(db_path)
    result = call(server, tool, sql)
    assert result["isError"] and result["structuredContent"]["code"] == "SQL_FORBIDDEN"
    assert dump_hash(db_path) == before


@pytest.mark.parametrize("tool", ["preview_write", "execute_write"])
def test_long_writes_are_interrupted(server: SqlToolboxServer, db_path: Path, tool: str) -> None:
    before = dump_hash(db_path)
    result = call(server, tool, INFINITE_UPDATE)
    assert result["isError"] and result["structuredContent"]["code"] == "TOOL_TIMEOUT"
    assert dump_hash(db_path) == before
    # The connection is usable again afterwards
    assert call(server, "preview_write", PURGE)["structuredContent"]["rows_affected"] == 25


def test_the_writer_refuses_what_the_guard_would_refuse(db_path: Path) -> None:
    # Second barrier: even called directly, the write connection keeps its authorizer
    writer = SqlWriter(db_path, timeout_ms=1000)
    try:
        for sql in ("DELETE FROM write_audit", "DROP TABLE clients", "ATTACH 'x.db' AS x"):
            with pytest.raises(sqlite3.DatabaseError):
                writer.execute(sql, "DELETE")
    finally:
        writer.close()
