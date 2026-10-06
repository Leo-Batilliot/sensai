"""Golden set checks (EVAL-02): format of eval/golden.jsonl and executable SQL.

The SQL is run against an empty business.db built from business_schema.sql: it proves
that every table, column and parameter exists. The expected values need the seeded
demo database (DATA-02) and are the harness's job (EVAL-06a).
"""

import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sensai.data.schema import connect, create_schema

GOLDEN_PATH = Path(__file__).resolve().parents[3] / "eval/golden.jsonl"
ENTRIES: list[dict[str, Any]] = [
    json.loads(line)
    for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

FIELDS = {"id", "category", "question", "gold_sql", "expects_write", "requires_memory", "notes"}
CATEGORIES = {
    "aggregate",
    "join",
    "time_filter",
    "top_n",
    "glossary",
    "write",
    "out_of_scope",
    "adversarial",
}
# Categories where no reference SQL makes sense: the expected behaviour is in notes
NO_SQL_CATEGORIES = {"out_of_scope", "adversarial"}
REFERENCE_DATE = date(2026, 10, 20)  # PROJECT §6.6: --today 2026-10-20
MIN_ENTRIES = 15  # golden set v0 (EVAL-02), 25 with EVAL-02b, 40 with EVAL-02c


def shift_months(day: date, months: int) -> date:
    """First day of the month `months` away from the month of `day`."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def reference_params(today: date) -> dict[str, str]:
    """Named parameters of gold_sql, derived from the reference date (eval/README.md)."""
    quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    return {
        "today": today.isoformat(),
        "m_start": today.replace(day=1).isoformat(),
        "prev_m_start": shift_months(today, -1).isoformat(),
        "q_start": quarter_start.isoformat(),
        "prev_q_start": shift_months(quarter_start, -3).isoformat(),
        "y_start": date(today.year, 1, 1).isoformat(),
    }


PARAMS = reference_params(REFERENCE_DATE)
WITH_SQL = [entry for entry in ENTRIES if entry["gold_sql"] is not None]


@pytest.fixture
def business() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


def test_golden_set_size_and_unique_ids() -> None:
    ids = [entry["id"] for entry in ENTRIES]
    assert len(ENTRIES) >= MIN_ENTRIES
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"q\d{3}", entry_id) for entry_id in ids)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_entry_format(entry: dict[str, Any]) -> None:
    assert set(entry) == FIELDS
    assert entry["category"] in CATEGORIES
    assert isinstance(entry["question"], str) and entry["question"].strip()
    assert isinstance(entry["expects_write"], bool)
    assert isinstance(entry["requires_memory"], bool)
    assert isinstance(entry["notes"], str)
    assert entry["requires_memory"] == (entry["category"] == "glossary")
    if entry["category"] == "write":
        assert entry["expects_write"]
    if entry["category"] in NO_SQL_CATEGORIES:
        assert entry["gold_sql"] is None
        assert entry["notes"], "without reference SQL, notes must say what is expected"
    else:
        assert isinstance(entry["gold_sql"], str) and entry["gold_sql"].strip()


@pytest.mark.parametrize("entry", WITH_SQL, ids=lambda entry: entry["id"])
def test_statement_kind_matches_expects_write(entry: dict[str, Any]) -> None:
    keyword = entry["gold_sql"].lstrip().split(None, 1)[0].upper()
    if entry["expects_write"]:
        assert keyword in {"INSERT", "UPDATE", "DELETE"}
    else:
        assert keyword in {"SELECT", "WITH"}


@pytest.mark.parametrize("entry", WITH_SQL, ids=lambda entry: entry["id"])
def test_parameters_are_known(entry: dict[str, Any]) -> None:
    used = set(re.findall(r":([a-z_]+)", entry["gold_sql"]))
    assert used <= set(PARAMS)


@pytest.mark.parametrize("entry", WITH_SQL, ids=lambda entry: entry["id"])
def test_gold_sql_runs_on_the_business_schema(
    business: sqlite3.Connection, entry: dict[str, Any]
) -> None:
    business.execute(entry["gold_sql"], PARAMS).fetchall()
    business.rollback()  # writes run in an implicit transaction: leave the schema empty


def test_demo_questions_are_in_the_golden_set() -> None:
    """Every question of the keynote scenario is measured (scenario.md §6)."""
    questions = {entry["question"] for entry in ENTRIES}
    assert {
        "Quels sont nos 5 clients les plus rentables ce trimestre et quel est leur délai moyen de paiement ?",
        "Quels sont nos clients rentables ce trimestre ?",
        "Quelle est la marge de Brasserie Lemaire ce trimestre ?",
        "Combien de clients inactifs avons-nous ?",
        "Supprime les clients inactifs depuis plus de 18 mois.",
        "Supprime les clients inactifs qui n'ont jamais passé de commande.",
    } <= questions


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (
            date(2026, 10, 20),
            {
                "m_start": "2026-10-01",
                "prev_m_start": "2026-09-01",
                "q_start": "2026-10-01",
                "prev_q_start": "2026-07-01",
            },
        ),
        (
            date(2026, 1, 5),
            {
                "m_start": "2026-01-01",
                "prev_m_start": "2025-12-01",
                "q_start": "2026-01-01",
                "prev_q_start": "2025-10-01",
            },
        ),
        (
            date(2026, 5, 31),
            {
                "m_start": "2026-05-01",
                "prev_m_start": "2026-04-01",
                "q_start": "2026-04-01",
                "prev_q_start": "2026-01-01",
            },
        ),
    ],
)
def test_reference_params(today: date, expected: dict[str, str]) -> None:
    params = reference_params(today)
    assert {key: params[key] for key in expected} == expected
