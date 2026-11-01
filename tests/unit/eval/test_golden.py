"""Golden set checks (EVAL-02, EVAL-02b): format of eval/golden.jsonl and executable SQL.

The SQL is run against an empty business.db built from business_schema.sql: it proves
that every table, column and parameter exists. It is then run on the seeded demo
database (DATA-02) at the evaluation reference date: every reference gives rows, so that
EX never compares two empty results. Comparing with the agent is the harness's job
(EVAL-06a).
"""

import dataclasses
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sensai.data.schema import connect, create_schema
from sensai.data.seed import generate
from sensai.eval.golden import REFERENCE_DATE, GoldenEntry, load_golden, reference_params

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
MIN_ENTRIES = 40  # golden set v0 (EVAL-02) 15, v1 (EVAL-02b) 25, v2 (EVAL-02c) 40


PARAMS = reference_params(REFERENCE_DATE)
WITH_SQL = [entry for entry in ENTRIES if entry["gold_sql"] is not None]


@pytest.fixture
def business() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    """The demo database of the evaluation: seed 42, reference date REFERENCE_DATE."""
    path = tmp_path_factory.mktemp("golden") / "business.db"
    generate(path, seed=42, today=REFERENCE_DATE)
    conn = connect(path)
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


@pytest.mark.parametrize("entry", WITH_SQL, ids=lambda entry: entry["id"])
def test_gold_sql_gives_rows_on_the_demo_data(
    demo: sqlite3.Connection, entry: dict[str, Any]
) -> None:
    cursor = demo.execute(entry["gold_sql"], PARAMS)
    if entry["expects_write"]:
        assert cursor.rowcount > 0  # the impact preview has something to show
        demo.rollback()
    else:
        assert cursor.fetchall()


TOP_N = [entry for entry in WITH_SQL if re.search(r"\bLIMIT\s+\d+\s*$", entry["gold_sql"])]


@pytest.mark.parametrize("entry", TOP_N, ids=lambda entry: entry["id"])
def test_no_tie_at_the_last_rank_of_a_top_n(
    demo: sqlite3.Connection, entry: dict[str, Any]
) -> None:
    """With a tie, the N-th row of the agent could rightly differ from the reference."""
    limit = int(re.search(r"(\d+)\s*$", entry["gold_sql"]).group(1))
    one_more = re.sub(r"\d+\s*$", str(limit + 1), entry["gold_sql"])
    rows = demo.execute(one_more, PARAMS).fetchall()

    def numbers(row: tuple[Any, ...]) -> list[Any]:
        return [value for value in row if isinstance(value, (int, float))]

    assert len(rows) == limit + 1
    assert numbers(rows[limit - 1]) != numbers(rows[limit])


def test_category_mix_of_the_final_set() -> None:
    """PROJECT §11.1: the final set (EVAL-02c, 40 questions) has exactly the planned mix."""
    final_targets = {
        "aggregate": 6,
        "join": 6,
        "time_filter": 6,
        "top_n": 5,
        "glossary": 5,
        "write": 5,
        "out_of_scope": 4,
        "adversarial": 3,
    }
    counts = {category: 0 for category in final_targets}
    for entry in ENTRIES:
        counts[entry["category"]] += 1
    assert counts == final_targets


def test_load_golden_reads_every_entry() -> None:
    entries = load_golden(GOLDEN_PATH)

    assert [dataclasses.asdict(entry) for entry in entries] == ENTRIES
    assert all(isinstance(entry, GoldenEntry) for entry in entries)


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
