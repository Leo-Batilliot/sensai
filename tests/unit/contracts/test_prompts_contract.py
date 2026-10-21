"""Contract checks on docs/contracts/prompts.md and schemas/example.v1.json (A4, A7)."""

import contextlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from sensai.data.seed import generate
from sensai.eval.golden import load_golden, reference_params

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/contracts/prompts.md"
SCHEMA = json.loads((ROOT / "docs/contracts/schemas/example.v1.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
TODAY = date(2026, 10, 20)


def doc_examples() -> list[dict[str, Any]]:
    """The JSON examples of prompts.md §2 (the bank lines shown in the contract)."""
    text = CONTRACT.read_text(encoding="utf-8")
    section = text[text.index("## 2. Exemples dynamiques") : text.index("## 3. Points")]
    blocks = re.findall(r"```json\n(.*?)```", section, flags=re.DOTALL)
    return [json.loads(line) for block in blocks for line in block.splitlines() if line.strip()]


EXAMPLE = {"id": "ex-01", "question": "Combien de clients actifs ?", "sql": "SELECT COUNT(*) FROM clients WHERE status = 'active'", "tags": ["comptage"]}  # fmt: skip


def test_the_schema_is_valid() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_the_contract_shows_an_example() -> None:
    assert doc_examples()


@pytest.mark.parametrize("example", doc_examples(), ids=lambda example: example["id"])
def test_contract_examples_follow_the_schema(example: dict[str, Any]) -> None:
    assert not list(VALIDATOR.iter_errors(example))


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"id": "7"}, "id sans préfixe ex-"),
        ({"sql": "DELETE FROM clients"}, "pas un SELECT"),
        ({"sql": "SELECT 1; DROP TABLE clients"}, "deux instructions"),
        (
            {"sql": "SELECT * FROM orders WHERE order_date >= date('now', '-1 month')"},
            "date('now')",
        ),
        ({"tags": []}, "aucun tag"),
        ({"tags": ["Filtre temporel"]}, "tag pas en snake_case"),
        ({"score": 1.0}, "champ inconnu"),
    ],
)
def test_the_schema_refuses(change: dict[str, Any], why: str) -> None:
    assert list(VALIDATOR.iter_errors({**EXAMPLE, **change})), why


def test_a_minimal_example_is_accepted() -> None:
    assert not list(VALIDATOR.iter_errors(EXAMPLE))
    assert not list(VALIDATOR.iter_errors({**EXAMPLE, "sql": "WITH t AS (SELECT 1) SELECT * FROM t"}))  # fmt: skip


def test_contract_examples_run_on_the_demo_data(tmp_path: Path) -> None:
    db = tmp_path / "business.db"
    generate(db, seed=42, today=TODAY)
    with contextlib.closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        for example in doc_examples():
            assert conn.execute(example["sql"], reference_params(TODAY)).fetchall(), example["id"]


def test_contract_examples_do_not_leak_the_golden_set() -> None:
    golden = load_golden(ROOT / "eval/golden.jsonl")

    def normal(text: str) -> str:
        return " ".join(text.lower().split())

    questions = {normal(entry.question) for entry in golden}
    gold_sql = {normal(entry.gold_sql) for entry in golden if entry.gold_sql}
    for example in doc_examples():
        assert normal(example["question"]) not in questions
        assert normal(example["sql"]) not in gold_sql
