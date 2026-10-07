import hashlib
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from sensai.__main__ import main
from sensai.data.schema import connect
from sensai.data.seed import MANIFEST_NAME, TRAP_CLIENT_NAME, add_months, generate

# Evaluation date of the golden set
TODAY = date(2026, 10, 20)
TOP_NAMES = [
    "Brasserie Lemaire",
    "Imprimerie Vasseur",
    "Cabinet Roussel",
    "Garage Delmas",
    "Atelier Morel",
]
GOLDEN_PATH = Path(__file__).resolve().parents[3] / "eval/golden.jsonl"

TOP_SQL = """
WITH margin AS (
  SELECT o.client_id, SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) AS marge
  FROM orders o JOIN order_lines ol ON ol.order_id = o.id
  WHERE o.order_date >= :q_start AND o.status <> 'cancelled'
  GROUP BY o.client_id
),
delay AS (
  SELECT i.client_id, AVG(julianday(p.paid_on) - julianday(i.issued_on)) AS delai
  FROM invoices i JOIN payments p ON p.invoice_id = i.id
  GROUP BY i.client_id
)
SELECT c.name, m.marge, d.delai
FROM margin m JOIN clients c ON c.id = m.client_id
LEFT JOIN delay d ON d.client_id = m.client_id
ORDER BY m.marge DESC
LIMIT 6
"""


def quarter_start(day: date) -> str:
    return date(day.year, (day.month - 1) // 3 * 3 + 1, 1).isoformat()


@pytest.fixture(scope="module")
def seeded(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[sqlite3.Connection, date, dict]]:
    today = TODAY
    db_path = tmp_path_factory.mktemp("seed") / "business.db"
    generate(db_path, seed=42, today=today)
    manifest = json.loads((db_path.parent / MANIFEST_NAME).read_text(encoding="utf-8"))
    conn = connect(db_path)
    yield conn, today, manifest
    conn.close()


def scalar(conn: sqlite3.Connection, sql: str, params: object = ()) -> object:
    return conn.execute(sql, params).fetchone()[0]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_seed_and_date_give_the_same_base(tmp_path: Path) -> None:
    first, second, other = tmp_path / "a" / "b.db", tmp_path / "b" / "b.db", tmp_path / "c" / "b.db"
    generate(first, seed=42, today=TODAY)
    generate(second, seed=42, today=TODAY)
    generate(other, seed=7, today=TODAY)
    assert sha256(first) == sha256(second)
    assert sha256(first) != sha256(other)
    manifest = (first.parent / MANIFEST_NAME).read_text(encoding="utf-8")
    assert manifest == (second.parent / MANIFEST_NAME).read_text(encoding="utf-8")


def test_regenerating_replaces_the_base(tmp_path: Path) -> None:
    db_path = tmp_path / "business.db"
    generate(db_path, seed=1, today=TODAY)
    generate(db_path, seed=42, today=TODAY)
    conn = connect(db_path)
    assert scalar(conn, "SELECT COUNT(*) FROM clients") == 300
    conn.close()
    assert not (tmp_path / "business.db.tmp").exists()


def test_seed_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "data" / "business.db"
    assert main(["seed", "--today", "2026-10-20", "--out", str(out)]) == 0
    assert out.is_file() and (out.parent / MANIFEST_NAME).is_file()
    assert "Base générée" in capsys.readouterr().out


def test_seed_command_rejects_a_bad_date(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["seed", "--today", "20/10/2026"])
    assert "AAAA-MM-JJ" in capsys.readouterr().err


def test_volumes(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, _, manifest = seeded
    expected = {
        "sales_reps": (5, 5),
        "clients": (300, 300),
        "products": (120, 120),
        "orders": (3800, 4400),
        "order_lines": (10500, 13000),
        "invoices": (3500, 4200),
        "payments": (3300, 4100),
    }
    for table, (low, high) in expected.items():
        count = scalar(conn, f"SELECT COUNT(*) FROM {table}")
        assert low <= count <= high, table
        assert manifest["volumes"][table] == count


def test_integrity(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, today, _ = seeded
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert scalar(conn, "PRAGMA integrity_check") == "ok"
    for sql in (
        "SELECT MAX(order_date) FROM orders",
        "SELECT MAX(issued_on) FROM invoices",
        "SELECT MAX(paid_on) FROM payments",
    ):
        assert scalar(conn, sql) <= today.isoformat()
    assert scalar(conn, "SELECT MIN(order_date) FROM orders") >= add_months(today, -24).isoformat()


def test_invoices_are_consistent(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, today, _ = seeded
    # Only shipped or delivered orders are invoiced
    assert (
        scalar(
            conn,
            "SELECT COUNT(*) FROM invoices i JOIN orders o ON o.id = i.order_id"
            " WHERE o.status NOT IN ('shipped', 'delivered')",
        )
        == 0
    )
    # Amounts are the sum of the lines, VAT 20 %
    assert (
        scalar(
            conn,
            "SELECT COUNT(*) FROM invoices i WHERE ABS(i.amount_ht - (SELECT ROUND(SUM("
            "ol.quantity * ol.unit_price), 2) FROM order_lines ol WHERE ol.order_id = i.order_id))"
            " > 0.005 OR ABS(i.amount_ttc - ROUND(i.amount_ht * 1.2, 2)) > 0.005",
        )
        == 0
    )
    # The invoice client is the order client; the due date follows the payment terms
    assert (
        scalar(
            conn,
            "SELECT COUNT(*) FROM invoices i JOIN orders o ON o.id = i.order_id"
            " JOIN clients c ON c.id = i.client_id WHERE o.client_id <> i.client_id"
            " OR i.due_on <> date(i.issued_on, '+' || c.payment_terms_days || ' days')",
        )
        == 0
    )
    # Status matches the payments
    statuses = conn.execute(
        "SELECT i.status, i.amount_ttc, COALESCE(SUM(p.amount), 0), i.due_on"
        " FROM invoices i LEFT JOIN payments p ON p.invoice_id = i.id GROUP BY i.id"
    ).fetchall()
    for status, amount, paid, due in statuses:
        if status == "paid":
            assert abs(paid - amount) < 0.01
        elif status == "partially_paid":
            assert 0 < paid < amount
        elif status == "overdue":
            assert paid == 0 and due < today.isoformat()
        elif status == "issued":
            assert paid == 0 and due >= today.isoformat()
        else:
            assert status == "cancelled" and paid == 0


def test_quarter_activity(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, today, manifest = seeded
    count = scalar(
        conn, "SELECT COUNT(*) FROM orders WHERE order_date >= ?", (quarter_start(today),)
    )
    assert count >= 150
    assert manifest["patterns"]["quarter_orders"] == count


def test_top_five_by_margin(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, today, manifest = seeded
    rows = conn.execute(TOP_SQL, {"q_start": quarter_start(today)}).fetchall()
    assert [name for name, _, _ in rows[:5]] == TOP_NAMES
    # Clearly ahead of the sixth client
    assert rows[4][1] >= 1.2 * rows[5][1]
    delays = [delay for _, _, delay in rows[:5]]
    assert delays.count(None) == 1
    paid = [delay for delay in delays if delay is not None]
    assert all(25 <= delay <= 55 for delay in paid)
    assert max(paid) - min(paid) >= 15
    assert [c["name"] for c in manifest["patterns"]["top_margin_clients"]] == TOP_NAMES


def test_top_client_without_payment(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, _, _ = seeded
    invoices, payments = conn.execute(
        "SELECT COUNT(DISTINCT i.id), COUNT(p.id) FROM clients c JOIN invoices i"
        " ON i.client_id = c.id LEFT JOIN payments p ON p.invoice_id = i.id"
        " WHERE c.name = 'Garage Delmas'"
    ).fetchone()
    assert invoices > 0
    assert payments == 0


def test_profitable_in_rate_differs_from_top_in_euros(
    seeded: tuple[sqlite3.Connection, date, dict],
) -> None:
    conn, today, _ = seeded
    profitable = {
        name
        for (name,) in conn.execute(
            "SELECT c.name FROM orders o JOIN order_lines ol ON ol.order_id = o.id"
            " JOIN clients c ON c.id = o.client_id"
            " WHERE o.order_date >= :q_start AND o.status <> 'cancelled'"
            " GROUP BY c.id, c.name"
            " HAVING SUM(ol.quantity * (ol.unit_price - ol.unit_cost))"
            " > 0.25 * SUM(ol.quantity * ol.unit_price)",
            {"q_start": quarter_start(today)},
        )
    }
    assert 3 <= len(profitable) <= 5
    assert len(profitable - set(TOP_NAMES)) >= 2


def test_inactive_clients(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, today, manifest = seeded
    assert scalar(conn, "SELECT COUNT(*) FROM clients WHERE status = 'inactive'") == 40
    # 25 purgeable: no order at all, created more than 18 months ago, with contacts
    purgeable = [
        row[0]
        for row in conn.execute(
            "SELECT c.id FROM clients c WHERE c.status = 'inactive'"
            " AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.client_id = c.id)"
            " AND c.created_at <= date(:today, '-18 months')"
            " AND EXISTS (SELECT 1 FROM contacts ct WHERE ct.client_id = c.id) ORDER BY c.id",
            {"today": today.isoformat()},
        )
    ]
    assert len(purgeable) == 25
    assert purgeable == manifest["patterns"]["purgeable_inactive_clients"]
    # 15 protected: every order invoiced, the last one 18 to 24 months ago
    protected = conn.execute(
        "SELECT c.id, MAX(o.order_date), SUM(i.id IS NULL) FROM clients c"
        " JOIN orders o ON o.client_id = c.id LEFT JOIN invoices i ON i.order_id = o.id"
        " WHERE c.status = 'inactive' GROUP BY c.id ORDER BY c.id"
    ).fetchall()
    assert [row[0] for row in protected] == manifest["patterns"]["protected_inactive_clients"]
    assert len(protected) == 15
    since = add_months(today, -18).isoformat()
    assert all(last < since and uninvoiced == 0 for _, last, uninvoiced in protected)


def test_active_clients_ordered_in_the_last_18_months(
    seeded: tuple[sqlite3.Connection, date, dict],
) -> None:
    conn, today, _ = seeded
    assert (
        scalar(
            conn,
            "SELECT COUNT(*) FROM clients c WHERE c.status = 'active' AND NOT EXISTS ("
            "SELECT 1 FROM orders o WHERE o.client_id = c.id AND o.order_date >= ?)",
            (add_months(today, -18).isoformat(),),
        )
        == 0
    )


@pytest.mark.parametrize(
    "purge",
    [
        "DELETE FROM clients WHERE status = 'inactive'"
        " AND id NOT IN (SELECT client_id FROM orders)",
        "DELETE FROM clients WHERE status = 'inactive'"
        " AND id NOT IN (SELECT client_id FROM invoices)",
    ],
)
def test_purge_touches_exactly_25_clients(
    seeded: tuple[sqlite3.Connection, date, dict], purge: str
) -> None:
    conn, _, _ = seeded
    try:
        assert conn.execute(purge).rowcount == 25
    finally:
        conn.rollback()


def test_naive_purge_is_blocked_by_invoices(
    seeded: tuple[sqlite3.Connection, date, dict],
) -> None:
    conn, _, _ = seeded
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM clients WHERE status = 'inactive'")
    finally:
        conn.rollback()
    assert scalar(conn, "SELECT COUNT(*) FROM clients WHERE status = 'inactive'") == 40


def test_named_clients(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, _, manifest = seeded
    rep = scalar(
        conn,
        "SELECT sr.full_name FROM clients c JOIN sales_reps sr ON sr.id = c.sales_rep_id"
        " WHERE c.name = 'Brasserie Lemaire'",
    )
    assert rep == "Julien Marchand" == manifest["patterns"]["lemaire_sales_rep"]["full_name"]
    assert scalar(conn, "SELECT COUNT(*) FROM clients WHERE name = 'Atelier Morel'") == 1
    status, orders = conn.execute(
        "SELECT c.status, COUNT(o.id) FROM clients c JOIN orders o ON o.client_id = c.id"
        " WHERE c.name = ?",
        (TRAP_CLIENT_NAME,),
    ).fetchone()
    assert status == "active" and orders >= 3


def test_late_payers(seeded: tuple[sqlite3.Connection, date, dict]) -> None:
    conn, _, manifest = seeded
    late = conn.execute(
        "SELECT i.client_id FROM invoices i JOIN payments p ON p.invoice_id = i.id"
        " GROUP BY i.client_id HAVING AVG(julianday(p.paid_on) - julianday(i.issued_on)) > 60"
    ).fetchall()
    overdue = {
        client_id
        for (client_id,) in conn.execute("SELECT client_id FROM invoices WHERE status = 'overdue'")
    }
    late_ids = {client_id for (client_id,) in late}
    assert len(late_ids) >= 5
    assert len(late_ids & overdue) >= 5
    assert {c["id"] for c in manifest["patterns"]["late_payers"]} <= late_ids


def test_reference_questions_run_on_the_seeded_base(
    seeded: tuple[sqlite3.Connection, date, dict],
) -> None:
    conn, today, _ = seeded
    params = {
        "today": today.isoformat(),
        "m_start": today.replace(day=1).isoformat(),
        "prev_m_start": add_months(today.replace(day=1), -1).isoformat(),
        "q_start": quarter_start(today),
        "prev_q_start": add_months(date.fromisoformat(quarter_start(today)), -3).isoformat(),
        "y_start": date(today.year, 1, 1).isoformat(),
    }
    entries = [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()]
    try:
        for entry in entries:
            if entry["gold_sql"] is None:
                continue
            rows = conn.execute(entry["gold_sql"], params).fetchall()
            if entry["id"] == "q001":
                assert len(rows) == 5
    finally:
        conn.rollback()
