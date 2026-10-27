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
    impact = call(server, "preview_write", PURGE)["structuredContent"]
    assert impact["operation"] == "DELETE"
    assert impact["rows_affected"] == 25
    assert impact["per_table"] == {"clients": -25, "contacts": -contacts}  # cascade included
    assert impact["blocked"] == []
    assert dump_hash(db_path) == before


def test_the_sample_shows_targeted_rows_as_they_are(
    server: SqlToolboxServer, db_path: Path
) -> None:
    sample = call(server, "preview_write", PURGE)["structuredContent"]["sample"]
    assert len(sample) == 5
    purgeable = {
        row
        for (row,) in sqlite3.connect(db_path).execute(
            "SELECT id FROM clients WHERE status = 'inactive'"
            " AND id NOT IN (SELECT client_id FROM orders)"
        )
    }
    assert {row["id"] for row in sample} <= purgeable
    assert all(row["status"] == "inactive" for row in sample)


def test_preview_of_a_blocked_delete_counts_the_protected_rows(
    server: SqlToolboxServer, db_path: Path
) -> None:
    before = dump_hash(db_path)
    impact = call(server, "preview_write", NAIVE_PURGE)["structuredContent"]
    assert impact["rows_affected"] == 0 and impact["per_table"] == {}
    # Same figures as independent control queries
    with_orders = count(
        db_path,
        "SELECT COUNT(*) FROM clients c WHERE status = 'inactive'"
        " AND EXISTS (SELECT 1 FROM orders o WHERE o.client_id = c.id)",
    )
    with_invoices = count(
        db_path,
        "SELECT COUNT(*) FROM clients c WHERE status = 'inactive'"
        " AND EXISTS (SELECT 1 FROM invoices i WHERE i.client_id = c.id)",
    )
    assert impact["blocked"] == [
        {"reason": "commandes à conserver", "count": with_orders},
        {"reason": "factures à conserver", "count": with_invoices},
    ]
    assert with_invoices == 15
    assert len(impact["sample"]) == 5  # the targeted rows are still shown
    assert dump_hash(db_path) == before


def test_the_hint_gives_a_statement_that_works(server: SqlToolboxServer) -> None:
    impact = call(server, "preview_write", NAIVE_PURGE)["structuredContent"]
    corrected = impact["hint"].split(" : ", 1)[1]
    assert corrected.startswith("DELETE FROM clients WHERE (status = 'inactive') AND NOT EXISTS")
    fixed = call(server, "preview_write", corrected)["structuredContent"]
    assert fixed["rows_affected"] == 25 and fixed["blocked"] == []


def test_a_refused_execute_tells_the_model_how_to_fix_it(server: SqlToolboxServer) -> None:
    message = call(server, "execute_write", NAIVE_PURGE)["structuredContent"]["message"]
    assert "15 ligne(s) visée(s) : factures à conserver" in message
    assert "Requête qui exclut les lignes protégées : DELETE FROM clients" in message


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT id FROM clients WHERE status = 'inactive') DELETE FROM contacts"
        " WHERE client_id IN x",
        "INSERT INTO sales_reps (full_name, email, region, hired_on)"
        " VALUES ('A B', 'ab@x.fr', 'Lille', '2020-01-01')",
    ],
)
def test_forms_without_a_sample_still_get_a_preview(server: SqlToolboxServer, sql: str) -> None:
    impact = call(server, "preview_write", sql)["structuredContent"]
    assert impact["sample"] == []
    assert impact["rows_affected"] > 0


def test_long_texts_are_cut_in_the_sample(server: SqlToolboxServer) -> None:
    long_update = "UPDATE clients SET name = name WHERE name LIKE 'SARL Ignore%'"
    (row,) = call(server, "preview_write", long_update)["structuredContent"]["sample"]
    assert len(row["name"]) == 60 and row["name"].endswith("…")


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
