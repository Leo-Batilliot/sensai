"""Dynamic examples (A7, FEWSHOT-01): the bank, its selection and its injection."""

import contextlib
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from event_checks import check_turn
from fakes import FakeClient, answer
from sensai.core import events
from sensai.core.config import Config
from sensai.core.engine import Engine
from sensai.core.fewshot import BANK_FILE, INSTRUCTION, ExampleIndex, examples_block, load_bank
from sensai.core.prompts import SystemPrompt
from sensai.data.seed import generate
from sensai.eval.golden import load_golden, reference_params
from sensai.memory.context import MemoryContext
from sensai.memory.fts import query_words
from sensai.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parents[3]
BANK = load_bank()
GOLDEN = load_golden(ROOT / "eval/golden.jsonl")
SCHEMA = json.loads((ROOT / "docs/contracts/schemas/example.v1.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 20)
MAX_WORDS_IN_COMMON = 0.8  # FEWSHOT-01: a bank question is never that close to a golden one
TEMPLATE = "$memory\n\n$examples"


@pytest.fixture(scope="module")
def index() -> Iterator[ExampleIndex]:
    examples = ExampleIndex(BANK)
    yield examples
    examples.close()


# --- the bank ---------------------------------------------------------------------------


def test_the_bank_is_package_data() -> None:
    assert (ROOT / "src/sensai/data" / BANK_FILE).is_file()
    assert 30 <= len(BANK) <= 40


def test_every_example_follows_the_schema() -> None:
    validator = Draft202012Validator(SCHEMA)
    lines = (ROOT / "src/sensai/data" / BANK_FILE).read_text(encoding="utf-8").splitlines()
    for line in lines:
        assert not list(validator.iter_errors(json.loads(line))), line[:40]
    assert len({example.id for example in BANK}) == len(BANK)


def test_every_example_runs(tmp_path: Path) -> None:
    # US-A7-01: every SQL of the bank gives rows on the demo data, :today bound
    db = tmp_path / "business.db"
    generate(db, seed=42, today=TODAY)
    with contextlib.closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        for example in BANK:
            assert conn.execute(example.sql, reference_params(TODAY)).fetchall(), example.id


def normal_sql(sql: str) -> str:
    """Case, spaces and table aliases do not make two queries different."""
    text = " ".join(sql.lower().split())
    aliases = re.findall(r"\b(?:from|join)\s+(\w+)\s+(?:as\s+)?(\w+)\b", text)
    for table, alias in aliases:
        if alias not in {"on", "where", "join", "group", "order", "left", "inner", "limit"}:
            text = re.sub(rf"\b{alias}\.", f"{table}.", text)
            text = re.sub(rf"\b{table}\s+(?:as\s+)?{alias}\b", table, text)
    return text


def words_in_common(a: str, b: str) -> float:
    first, second = set(query_words(a)), set(query_words(b))
    return len(first & second) / len(first | second)


def test_no_leak_from_the_golden_set() -> None:
    # US-A7-02: the measure of A7 would be inflated by an answer shown as an example
    gold_sql = {normal_sql(entry.gold_sql) for entry in GOLDEN if entry.gold_sql}
    for example in BANK:
        assert normal_sql(example.sql) not in gold_sql, example.id
        for entry in GOLDEN:
            closeness = words_in_common(example.question, entry.question)
            assert closeness <= MAX_WORDS_IN_COMMON, (example.id, entry.id)


def test_the_leak_check_sees_through_aliases() -> None:
    renamed = "SELECT COUNT(*) FROM clients AS k WHERE k.status = 'active'"
    assert normal_sql(renamed) == normal_sql(
        "select count(*) from clients where clients.status = 'active'"
    )


# --- selection --------------------------------------------------------------------------


def test_closest_examples_are_selected(index: ExampleIndex) -> None:
    chosen = index.select("Chiffre d'affaires par commercial sur le mois dernier ?", 3)

    assert chosen[0].example.id == "ex-07"
    assert len(chosen) == 3
    assert [item.score for item in chosen] == sorted((item.score for item in chosen), reverse=True)
    assert all(item.score > 0 for item in chosen)


def test_selection_is_stable(index: ExampleIndex) -> None:
    question = "Quels clients paient en retard ce trimestre ?"
    assert index.select(question, 3) == index.select(question, 3)


@pytest.mark.parametrize(
    "question",
    ["L'ADV a-t-il « clôturé » ?", 'NEAR(ca marge) OR "x', "ca* - marge^2 : (top)", "?!"],
)
def test_raw_question_is_escaped(index: ExampleIndex, question: str) -> None:
    # The question never reaches MATCH raw (memory.md §3.3): FTS5 syntax is harmless
    index.select(question, 3)


def test_no_example_when_nothing_to_search_or_k_is_zero(index: ExampleIndex) -> None:
    assert index.select("Et le ?", 3) == []
    assert index.select("Chiffre d'affaires par commercial", 0) == []


def test_min_score_keeps_only_close_examples(index: ExampleIndex) -> None:
    best = index.select("Chiffre d'affaires par commercial le mois dernier", 3)
    assert index.select("Chiffre d'affaires par commercial le mois dernier", 3, 1000.0) == []
    kept = index.select("Chiffre d'affaires par commercial le mois dernier", 3, best[1].score)
    assert [item.example.id for item in kept] == [item.example.id for item in best[:2]]


def test_the_block_shows_each_question_and_its_sql(index: ExampleIndex) -> None:
    chosen = index.select("Chiffre d'affaires par commercial le mois dernier", 2)
    block = examples_block(chosen)

    assert block.startswith(INSTRUCTION)
    assert f"Question : {chosen[0].example.question}\nSQL : {chosen[0].example.sql}" in block
    assert ":today" in block  # relative dates are left to the model and its date tool


# --- injection by the engine ------------------------------------------------------------


async def run(engine: Engine, message: str) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        return [event async for event in turn]


def system_sent(client: FakeClient) -> str:
    return client.calls[0][1][0]["content"]


async def test_examples_are_injected_and_announced(index: ExampleIndex) -> None:
    # US-A7-01: examples_injected lists what the prompt got, before any token
    client = FakeClient(answer("Voici."))
    template = SystemPrompt(TEMPLATE, {})
    engine = Engine(Config(), client, system_prompt=template, examples=index, session_id="s-1")

    emitted = await run(engine, "Chiffre d'affaires par commercial le mois dernier ?")

    check_turn(emitted)
    assert [event.type for event in emitted[:2]] == ["turn_started", "examples_injected"]
    injected = emitted[1]
    assert isinstance(injected, events.ExamplesInjected)
    assert [ref.id for ref in injected.examples][0] == "ex-07"
    assert len(injected.examples) == Config().fewshot.k
    assert BANK[6].sql in system_sent(client)


async def test_examples_come_after_the_memory_context(index: ExampleIndex) -> None:
    with MemoryStore.open(":memory:") as store:
        store.define_term("commercial", "Celui qui suit le client (clients.sales_rep_id).")
        client = FakeClient(answer("Voici."))
        engine = Engine(
            Config(),
            client,
            system_prompt=SystemPrompt(TEMPLATE, {}),
            memory=MemoryContext(store),
            examples=index,
            session_id="s-1",
        )

        emitted = await run(engine, "Chiffre d'affaires par commercial ?")

    check_turn(emitted)
    assert [event.type for event in emitted[:3]] == [
        "turn_started",
        "context_injected",
        "examples_injected",
    ]


@pytest.mark.parametrize(
    "config",
    [Config(fewshot={"enabled": False}), Config(fewshot={"k": 0})],
    ids=["disabled", "k=0"],
)
async def test_disabled_examples_inject_nothing(index: ExampleIndex, config: Config) -> None:
    # The witness of the comparison with and without examples (EVAL-08)
    client = FakeClient(answer("Voici."))
    engine = Engine(config, client, system_prompt=SystemPrompt(TEMPLATE, {}), examples=index)

    emitted = await run(engine, "Chiffre d'affaires par commercial le mois dernier ?")

    assert "examples_injected" not in [event.type for event in emitted]
    assert system_sent(client) == "aucun\n\naucun"


async def test_a_prompt_without_the_hole_gets_no_example(index: ExampleIndex) -> None:
    # The v1 has no $examples: nothing is selected, no event (prompts.md §2.2)
    client = FakeClient(answer("Voici."))
    engine = Engine(Config(), client, system_prompt=SystemPrompt("$memory", {}), examples=index)

    emitted = await run(engine, "Chiffre d'affaires par commercial le mois dernier ?")

    assert "examples_injected" not in [event.type for event in emitted]


async def test_a_broken_index_never_stops_the_turn() -> None:
    broken = ExampleIndex(BANK)
    broken.close()
    client = FakeClient(answer("Voici."))
    engine = Engine(Config(), client, system_prompt=SystemPrompt(TEMPLATE, {}), examples=broken)

    emitted = await run(engine, "Chiffre d'affaires par commercial ?")

    assert emitted[-1].type == "message_done"
    assert "examples_injected" not in [event.type for event in emitted]
