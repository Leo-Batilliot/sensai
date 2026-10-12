"""Compact schema of business.db for the system prompt (CORE-08)."""

import hashlib
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from sensai.core.schema_summary import MAX_SAMPLE_TEXT, compact_schema
from sensai.data.schema import connect, create_schema
from sensai.data.seed import TRAP_CLIENT_NAME, generate


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The real demo database, as `python -m sensai seed` builds it (0.6 s)."""
    path = tmp_path_factory.mktemp("demo") / "business.db"
    generate(path, seed=42, today=date(2026, 10, 11))
    return path


@pytest.fixture
def small_db(tmp_path: Path) -> Iterator[Path]:
    """The real schema with a few hand-written rows."""
    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.execute(
        "INSERT INTO sales_reps VALUES (1, 'Julien Marchand', 'j@n.fr', 'Artois', '2020-01-06')"
    )
    conn.execute(
        "INSERT INTO clients VALUES (1, 'L''Atelier du papier à en-tête de Roubaix-Tourcoing',"
        " NULL, 'Imprimerie', 'Roubaix', 'PME', 'active', 1, 30, '2024-01-02')"
    )
    conn.execute(
        "INSERT INTO write_audit VALUES (1, '2026-10-11T10:00:00Z', 's-1', 'DELETE x', 1, 'user')"
    )
    conn.commit()
    conn.close()
    yield path


def table_line(schema: str, table: str) -> str:
    return next(line for line in schema.splitlines() if line.startswith(f"{table} ("))


def test_every_business_table_is_described_but_the_audit_log(small_db: Path) -> None:
    schema = compact_schema(small_db)

    described = [line.split(" (")[0] for line in schema.splitlines() if not line.startswith(" ")]
    assert described == [
        "sales_reps",
        "clients",
        "contacts",
        "products",
        "orders",
        "order_lines",
        "invoices",
        "payments",
    ]
    assert "write_audit" not in schema


def test_columns_show_types_keys_links_dates_and_allowed_values(small_db: Path) -> None:
    clients = table_line(compact_schema(small_db), "clients")

    assert clients.startswith("clients (1 lignes) : id INTEGER PK, name TEXT,")
    assert "segment TEXT IN ('TPE','PME','ETI','GE')" in clients
    assert "status TEXT IN ('active','inactive','archived')" in clients
    assert "sales_rep_id INTEGER -> sales_reps.id" in clients
    assert "created_at TEXT date" in clients


def test_column_notes_come_from_the_schema_comments(small_db: Path) -> None:
    schema = compact_schema(small_db)

    assert "margin = quantity * (unit_price - unit_cost)" in schema
    assert "payment delay = julianday(paid_on) - julianday(invoices.issued_on)" in schema


def test_examples_are_short_and_quoted_like_sql(small_db: Path) -> None:
    schema = compact_schema(small_db)

    name = "L'Atelier du papier à en-tête de Roubaix-Tourcoing"
    cut = name[: MAX_SAMPLE_TEXT - 1] + "…"  # MAX_SAMPLE_TEXT characters in all
    examples = next(line for line in schema.splitlines() if "'Imprimerie'" in line)
    assert "'" + cut.replace("'", "''") + "'" in examples  # apostrophe doubled, as in SQL
    assert "NULL" in examples
    assert name not in examples


def test_the_database_is_only_read(small_db: Path) -> None:
    before = hashlib.sha256(small_db.read_bytes()).hexdigest()
    compact_schema(small_db)
    assert hashlib.sha256(small_db.read_bytes()).hexdigest() == before


def test_the_prompt_injection_client_is_never_an_example(demo_db: Path) -> None:
    schema = compact_schema(demo_db)

    assert TRAP_CLIENT_NAME[:20] not in schema
    assert "instructions" not in schema.lower()


def test_the_demo_schema_stays_compact(demo_db: Path) -> None:
    # About 1 000 tokens: the system prompt shares num_ctx (8 192) with the conversation
    assert len(compact_schema(demo_db)) < 3500
