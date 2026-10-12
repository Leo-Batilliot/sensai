"""Memory context in the Engine (MEM-04): context_injected and the system prompt.

The task's end criterion ("a glossary rule changes the generated SQL") needs a real
model: see tests/integration/test_memory_rule.py (marker ollama). Here, the fake model
proves the rule learned in one session reaches the model in the next one.
"""

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from event_checks import check_turn
from fakes import FakeClient, FakeTool, FakeToolRegistry, answer, tool_request
from sensai.core import events
from sensai.core.config import Config
from sensai.core.engine import MEMORY_HEADING, Engine
from sensai.core.prompts import system_prompt_template
from sensai.memory.context import MemoryContext
from sensai.memory.store import MemoryStore
from sensai.tools.base import ToolInfo
from sensai.tools.memory_tools import memory_tools

RULE = "Marge brute > 25 % du CA HT sur la période"
HINT = "SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) > 0.25 * SUM(ol.quantity * ol.unit_price)"
QUESTION = "Quels sont nos clients rentables ce trimestre ?"


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    with MemoryStore.open(":memory:") as memory:
        yield memory


async def run(engine: Engine, message: str) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        return [event async for event in turn]


def system_sent(client: FakeClient, call: int = 0) -> str:
    _, messages, _ = client.calls[call]
    assert messages[0]["role"] == "system"
    return messages[0]["content"]


def native_registry(store: MemoryStore) -> FakeToolRegistry:
    def info(tool: object) -> ToolInfo:
        return ToolInfo(
            name=tool.name,
            server=None,
            original_name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            annotations=tool.annotations,
            trusted=True,
        )

    return FakeToolRegistry(*(FakeTool(info(tool), tool.call) for tool in memory_tools(store)))


async def test_context_injected_right_after_turn_started(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE, sql_hint=HINT)
    client = FakeClient(answer("Voici la liste."))
    engine = Engine(Config(), client, memory=MemoryContext(store), session_id="s-1")

    emitted = await run(engine, QUESTION)

    check_turn(emitted)
    assert [event.type for event in emitted[:2]] == ["turn_started", "context_injected"]
    injected = emitted[1]
    assert isinstance(injected, events.ContextInjected)
    assert injected.turn_id == emitted[0].turn_id
    assert injected.refs == [events.MemoryRef("glossary", "client rentable", "client rentable")]
    assert HINT in system_sent(client)


async def test_no_entry_no_event_and_the_prompt_is_unchanged(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE, sql_hint=HINT)
    client = FakeClient(answer("Il fait beau."))
    engine = Engine(
        Config(), client, system_prompt="Prompt.", memory=MemoryContext(store), session_id="s-1"
    )

    emitted = await run(engine, "Quelle est la météo ?")

    assert "context_injected" not in [event.type for event in emitted]
    assert system_sent(client) == "Prompt."


async def test_a_text_prompt_gets_the_memory_section_appended(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE)
    client = FakeClient(answer("Voici."))
    engine = Engine(Config(), client, system_prompt="Prompt.", memory=MemoryContext(store))

    await run(engine, QUESTION)

    assert system_sent(client).startswith(f"Prompt.\n\n{MEMORY_HEADING}\n")


async def test_the_template_gets_the_memory_block_of_each_turn(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE, sql_hint=HINT)
    blocks: list[str] = []

    def template(memory: str) -> str:
        blocks.append(memory)
        return f"Prompt avec mémoire : {memory or 'aucun'}"

    client = FakeClient(answer("Un."), answer("Deux."))
    engine = Engine(Config(), client, system_prompt=template, memory=MemoryContext(store))

    await run(engine, QUESTION)
    await run(engine, "Et la météo ?")

    assert HINT in blocks[0] and blocks[1] == ""
    assert system_sent(client, 1) == "Prompt avec mémoire : aucun"


async def test_the_sql_prompt_template_holds_the_rule(tmp_path: Path, store: MemoryStore) -> None:
    store.define_term("client rentable", RULE, sql_hint=HINT)
    db = tmp_path / "business.db"
    db.touch()
    client = FakeClient(answer("Voici."))
    template = system_prompt_template(db, ["sql_toolbox__run_select"])
    engine = Engine(Config(), client, system_prompt=template, memory=MemoryContext(store))

    await run(engine, QUESTION)

    prompt = system_sent(client)
    section = prompt[prompt.index(MEMORY_HEADING) :]
    assert HINT in section and "aucun" not in section


async def test_a_rule_learned_in_a_session_reaches_the_model_in_the_next(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with MemoryStore.open(path) as first_store:
        first = Engine(
            Config(),
            FakeClient(
                tool_request(
                    "glossary_define", term="client rentable", definition=RULE, sql_hint=HINT
                ),
                answer("C'est noté."),
            ),
            registry=native_registry(first_store),
            memory=MemoryContext(first_store),
        )
        learned = await run(first, "Retiens qu'un client rentable a une marge brute > 25 %.")
    assert "memory_updated" in [event.type for event in learned]

    with MemoryStore.open(path) as second_store:  # a new session, after a restart
        client = FakeClient(answer("Voici la liste."))
        second = Engine(Config(), client, memory=MemoryContext(second_store))
        emitted = await run(second, QUESTION)

    assert any(isinstance(event, events.ContextInjected) for event in emitted)
    assert HINT in system_sent(client)
