import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from sensai.data.schema import connect, create_schema
from sensai.mcp_servers.sql_toolbox.guard import (
    FORBIDDEN,
    READ,
    WRITE,
    QueryTimeout,
    SqlGuard,
    first_keyword,
    json_value,
    open_readonly,
)

INFINITE = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.execute(
        "INSERT INTO clients (id, name, sector, city, segment, created_at)"
        " VALUES (1, 'Brasserie Lemaire', 'Restauration', 'Lille', 'PME', '2024-01-01'),"
        " (2, 'Atelier Morel', 'Artisanat', 'Arras', 'TPE', '2024-02-01')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def guard(db_path: Path) -> Iterator[SqlGuard]:
    guard = SqlGuard(db_path, timeout_ms=200)
    yield guard
    guard.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM clients",
        "  -- top clients\n SELECT name FROM clients;",
        "/* comment */ SELECT COUNT(*) FROM clients",
        "WITH c AS (SELECT * FROM clients) SELECT name FROM c",
        "VALUES (1, 2)",
        "SELECT date('now'), julianday('2026-10-01'), ROUND(1.234, 1)",
        "SELECT * FROM pragma_table_info('clients')",
    ],
)
def test_reads(guard: SqlGuard, sql: str) -> None:
    assert guard.classify(sql).kind == READ


@pytest.mark.parametrize(
    ("sql", "operation"),
    [
        ("INSERT INTO sales_reps VALUES (1, 'A', 'a@x.fr', 'Lille', '2020-01-01')", "INSERT"),
        ("UPDATE clients SET status = 'inactive' WHERE id = 1", "UPDATE"),
        ("DELETE FROM clients WHERE id = 2", "DELETE"),
        (
            "REPLACE INTO clients (id, name, sector, city, segment, created_at)"
            " SELECT id, name, sector, city, segment, created_at FROM clients",
            "INSERT",
        ),
        ("INSERT INTO contacts (client_id, full_name) SELECT id, name FROM clients", "INSERT"),
        (
            "INSERT INTO clients (id, name, sector, city, segment, created_at)"
            " VALUES (1, 'X', 'S', 'C', 'PME', '2024-01-01')"
            " ON CONFLICT (id) DO UPDATE SET name = excluded.name",
            "UPDATE",
        ),
        ("WITH old AS (SELECT id FROM clients) DELETE FROM clients WHERE id IN old", "DELETE"),
    ],
)
def test_writes(guard: SqlGuard, sql: str, operation: str) -> None:
    classification = guard.classify(sql)
    assert classification.kind == WRITE
    assert classification.operation == operation


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH 'other.db' AS other",
        "DETACH other",
        "PRAGMA writable_schema = 1",
        "PRAGMA foreign_keys = OFF",
        "CREATE TABLE z (a)",
        "DROP TABLE clients",
        "ALTER TABLE clients ADD COLUMN x",
        "CREATE VIEW v AS SELECT 1",
        "BEGIN",
        "SAVEPOINT s",
        "SELECT load_extension('evil')",
        "SELECT 1; DELETE FROM clients",
        "VACUUM",
        "VACUUM INTO 'copy.db'",
        "",
        "   -- only a comment",
    ],
)
def test_forbidden(guard: SqlGuard, sql: str) -> None:
    classification = guard.classify(sql)
    assert classification.kind == FORBIDDEN
    assert classification.reason


def test_syntax_errors_raise(guard: SqlGuard) -> None:
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        guard.classify("SELECT * FROM nowhere")
    with pytest.raises(sqlite3.OperationalError):
        guard.classify("SELEC name FROM clients")


def test_classifying_a_write_does_not_run_it(guard: SqlGuard, db_path: Path) -> None:
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert guard.classify("DELETE FROM clients").kind == WRITE
    assert guard.classify("DROP TABLE clients").kind == FORBIDDEN
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM clients").fetchone() == (2,)
    conn.close()


def test_classification_lists_tables(guard: SqlGuard) -> None:
    classification = guard.classify(
        "SELECT c.name FROM clients c JOIN contacts ct ON ct.client_id = c.id"
    )
    assert classification.tables == ("clients", "contacts")


def test_run_read_truncates(guard: SqlGuard) -> None:
    columns, rows, truncated = guard.run_read("SELECT id, name FROM clients ORDER BY id", 1)
    assert columns == ["id", "name"]
    assert rows == [[1, "Brasserie Lemaire"]]
    assert truncated
    _, rows, truncated = guard.run_read("SELECT id FROM clients", 2)
    assert len(rows) == 2 and not truncated


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM clients",
        "UPDATE clients SET name = 'X'",
        "ATTACH 'other.db' AS other",
        "PRAGMA query_only = OFF",
    ],
)
def test_the_read_connection_refuses_anything_else(
    guard: SqlGuard, db_path: Path, sql: str
) -> None:
    # Even if the classification were bypassed, the reader cannot write
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.DatabaseError):
        guard.run_read(sql, 10)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_long_queries_are_interrupted(guard: SqlGuard) -> None:
    with pytest.raises(QueryTimeout):
        guard.run_read(INFINITE, 10)
    # The time limit is reset: the next query runs normally
    assert guard.run_read("SELECT COUNT(*) FROM clients", 10)[1] == [[2]]


def test_open_readonly_refuses_writes(db_path: Path) -> None:
    conn = open_readonly(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM clients")
    conn.close()


def test_open_readonly_accepts_paths_with_spaces_and_accents(tmp_path: Path) -> None:
    path = tmp_path / "3eme année" / "business.db"
    path.parent.mkdir()
    sqlite3.connect(path).close()
    open_readonly(path).close()


@pytest.mark.parametrize(
    ("sql", "keyword"),
    [
        ("select 1", "SELECT"),
        ("  -- note\n/* block */ WITH x AS (SELECT 1) SELECT * FROM x", "WITH"),
        ("-- only a comment", ""),
        ("(SELECT 1)", ""),
    ],
)
def test_first_keyword(sql: str, keyword: str) -> None:
    assert first_keyword(sql) == keyword


def test_json_value() -> None:
    assert json_value(b"\x00\x01") == "<BLOB 2 octets>"
    assert json_value(float("inf")) is None
    assert json_value(float("nan")) is None
    assert json_value(1.5) == 1.5
    assert json_value("é") == "é"
    assert json_value(None) is None
