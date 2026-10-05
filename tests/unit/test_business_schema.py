import sqlite3
from collections.abc import Iterator

import pytest

from sensai.data.schema import connect, create_schema

TABLES = {
    "sales_reps",
    "clients",
    "contacts",
    "products",
    "orders",
    "order_lines",
    "invoices",
    "payments",
    "write_audit",
}

TOP_CLIENTS_QUERY = """
WITH margin AS (
  SELECT o.client_id,
         SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) AS marge
  FROM orders o
  JOIN order_lines ol ON ol.order_id = o.id
  WHERE o.order_date >= :q_start AND o.status <> 'cancelled'
  GROUP BY o.client_id
),
delay AS (
  SELECT i.client_id,
         AVG(julianday(p.paid_on) - julianday(i.issued_on)) AS delai_moyen_j
  FROM invoices i
  JOIN payments p ON p.invoice_id = i.id
  GROUP BY i.client_id
)
SELECT c.name,
       ROUND(m.marge, 2)          AS marge_eur,
       ROUND(d.delai_moyen_j, 1)  AS delai_moyen_jours
FROM margin m
JOIN clients c ON c.id = m.client_id
LEFT JOIN delay d ON d.client_id = m.client_id
ORDER BY m.marge DESC
LIMIT 5
"""


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


def add_client(db: sqlite3.Connection, client_id: int, status: str = "active") -> None:
    db.execute(
        "INSERT INTO clients (id, name, sector, city, segment, status, created_at)"
        " VALUES (?, ?, 'Restauration', 'Lille', 'PME', ?, '2024-10-05')",
        (client_id, f"Client {client_id}", status),
    )


def add_invoiced_order(db: sqlite3.Connection, client_id: int, order_id: int) -> None:
    db.execute("INSERT OR IGNORE INTO products VALUES (1, 'SKU-1', 'Ramette', 'Papier', 5, 3, 1)")
    db.execute("INSERT INTO orders VALUES (?, ?, '2026-09-01', 'delivered')", (order_id, client_id))
    db.execute("INSERT INTO order_lines VALUES (?, ?, 1, 10, 5.0, 3.0)", (order_id, order_id))
    db.execute(
        "INSERT INTO invoices VALUES (?, ?, ?, '2026-09-02', '2026-10-02', 50, 60, 'paid')",
        (order_id, order_id, client_id),
    )
    db.execute(
        "INSERT INTO payments VALUES (?, ?, '2026-09-20', 60, 'virement')", (order_id, order_id)
    )


def test_all_tables_created(db: sqlite3.Connection) -> None:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert {name for (name,) in rows} == TABLES


def test_connect_enforces_foreign_keys(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO orders VALUES (1, 999, '2026-09-01', 'pending')")


def test_client_with_invoices_is_protected(db: sqlite3.Connection) -> None:
    add_client(db, 1, status="inactive")
    add_invoiced_order(db, client_id=1, order_id=1)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM clients WHERE id = 1")


def test_client_without_orders_deletes_its_contacts(db: sqlite3.Connection) -> None:
    add_client(db, 1, status="inactive")
    db.execute("INSERT INTO contacts (client_id, full_name) VALUES (1, 'A'), (1, 'B')")
    db.execute("DELETE FROM clients WHERE id = 1")
    assert db.execute("SELECT COUNT(*) FROM contacts").fetchone() == (0,)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("segment", "XXL"),
        ("status", "deleted"),
        ("created_at", "05/10/2026"),
        ("created_at", "2026-02-30"),
    ],
)
def test_clients_reject_invalid_values(db: sqlite3.Connection, column: str, value: str) -> None:
    row = {
        "name": "X",
        "sector": "S",
        "city": "C",
        "segment": "PME",
        "status": "active",
        "created_at": "2026-10-05",
    }
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO clients (name, sector, city, segment, status, created_at)"
            " VALUES (:name, :sector, :city, :segment, :status, :created_at)",
            row,
        )


def test_invoice_cannot_be_due_before_issue(db: sqlite3.Connection) -> None:
    add_client(db, 1)
    db.execute("INSERT INTO orders VALUES (1, 1, '2026-09-01', 'delivered')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO invoices VALUES (1, 1, 1, '2026-09-02', '2026-09-01', 50, 60, 'issued')"
        )


def test_top_clients_query_runs(db: sqlite3.Connection) -> None:
    add_client(db, 1)
    add_invoiced_order(db, client_id=1, order_id=1)
    rows = db.execute(TOP_CLIENTS_QUERY, {"q_start": "2026-07-01"}).fetchall()
    assert rows == [("Client 1", 20.0, 18.0)]
