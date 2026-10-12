"""Memory tools (MEM-03): contract definitions, results, errors, and use by the Engine."""

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, FakeTool, FakeToolRegistry, answer, tool_request
from sensai.core import events
from sensai.core.config import Config
from sensai.core.engine import Engine
from sensai.memory.store import MemoryStore
from sensai.tools.base import Tool, ToolInfo, ToolResult
from sensai.tools.memory_tools import load_definitions, memory_tools

RULE = "Marge brute > 25 % du CA HT sur la période"
HINT = "SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) > 0.25 * SUM(ol.quantity * ol.unit_price)"


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    with MemoryStore.open(":memory:") as memory:
        yield memory


@pytest.fixture
def tools(store: MemoryStore) -> dict[str, Tool]:
    return {tool.name: tool for tool in memory_tools(store, session_id="s-1")}


async def call(tools: dict[str, Tool], tool: str, /, **arguments: Any) -> ToolResult:
    return await tools[tool].call(arguments)


def native(tool: Tool) -> FakeTool:
    """A native tool as the registry sees it (tools.md §3.2): no server, no prefix, trusted."""
    info = ToolInfo(
        name=tool.name,
        server=None,
        original_name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        annotations=tool.annotations,
        trusted=True,
    )
    return FakeTool(info, tool.call)


# --- definitions come from the contract ----------------------------------------------------


def test_the_seven_tools_of_the_contract(tools: dict[str, Tool]) -> None:
    definitions = load_definitions()
    assert list(tools) == list(definitions)
    for name, tool in tools.items():
        assert tool.description == definitions[name]["description"]
        assert tool.input_schema == definitions[name]["inputSchema"]


def test_annotations_are_explicit_and_only_delete_is_destructive(tools: dict[str, Tool]) -> None:
    for tool in tools.values():
        hints = tool.annotations
        assert None not in (hints.read_only, hints.destructive, hints.idempotent, hints.open_world)
        assert hints.destructive is (tool.name == "memory_delete")
        assert hints.read_only is (tool.name == "memory_search")


# --- writes --------------------------------------------------------------------------------


async def test_glossary_define_writes_and_reports_the_change(
    tools: dict[str, Tool], store: MemoryStore
) -> None:
    result = await call(tools, "glossary_define", term="client rentable", definition=RULE)

    assert result.ok
    assert json.loads(result.text) == result.data
    assert result.data == {
        "op": "create",
        "kind": "glossary",
        "ref": "client rentable",
        "summary": "Terme « client rentable » défini",
    }
    assert result.summary == "Terme « client rentable » défini"
    change = result.memory_change
    assert change is not None
    assert (change.op, change.kind, change.ref) == ("create", "glossary", "client rentable")
    assert store.get("glossary", "client rentable")["definition"] == RULE


async def test_writes_are_audited_as_the_agent_of_the_session(
    tools: dict[str, Tool], store: MemoryStore
) -> None:
    await call(tools, "glossary_define", term="client rentable", definition=RULE)

    (row,) = store.audit_log()
    assert (row["op"], row["actor"], row["session_id"]) == ("create", "agent", "s-1")


async def test_same_definition_twice_is_unchanged_without_memory_change(
    tools: dict[str, Tool],
) -> None:
    await call(tools, "glossary_define", term="client rentable", definition=RULE)
    again = await call(tools, "glossary_define", term="Client Rentable", definition=RULE)

    assert again.ok
    assert again.data is not None and again.data["op"] == "unchanged"
    assert again.memory_change is None


async def test_entity_relation_fact_and_update(tools: dict[str, Tool], store: MemoryStore) -> None:
    lemaire = await call(
        tools,
        "memory_upsert_entity",
        type="client",
        name="Brasserie Lemaire",
        aliases=["Lemaire", "BL"],
        external_ref="clients:42",
    )
    await call(tools, "memory_upsert_entity", type="person", name="Julien Marchand")
    relation = await call(
        tools, "memory_relate", src="Lemaire", type="managed_by", dst="Julien Marchand"
    )
    fact = await call(
        tools, "memory_add_fact", content="Compte stratégique", category="note", entity="BL"
    )
    update = await call(
        tools, "memory_update", kind="fact", ref=fact.data["ref"], patch={"category": "preference"}
    )

    assert all(r.ok for r in (lemaire, relation, fact, update))
    assert relation.data["op"] == "create" and relation.memory_change is not None
    assert update.memory_change is not None and update.memory_change.op == "update"
    stored = store.get("fact", fact.data["ref"])
    assert (stored["entity"], stored["category"], stored["source"]) == (
        lemaire.data["ref"],
        "preference",
        "agent",
    )


async def test_memory_delete_deletes_without_asking(
    tools: dict[str, Tool], store: MemoryStore
) -> None:
    # Asking for a confirmation is the HITL policy's job, before the call (tools.md §4)
    await call(tools, "glossary_define", term="client rentable", definition=RULE)

    result = await call(tools, "memory_delete", kind="glossary", ref="client rentable")

    assert result.ok
    assert result.memory_change is not None and result.memory_change.op == "delete"
    assert store.dump()["glossary"] == []


# --- search --------------------------------------------------------------------------------


async def test_search_finds_a_term_with_its_sql_hint(tools: dict[str, Tool]) -> None:
    await call(tools, "glossary_define", term="client rentable", definition=RULE, sql_hint=HINT)

    result = await call(tools, "memory_search", query="Quels sont nos clients rentables ?")

    assert result.ok and result.memory_change is None
    assert result.data is not None
    (hit,) = result.data["results"]
    assert (hit["kind"], hit["ref"], hit["sql_hint"]) == ("glossary", "client rentable", HINT)
    assert result.summary == "1 entrée(s) trouvée(s)"
    assert json.loads(result.text) == result.data


async def test_search_limit_defaults_to_five(tools: dict[str, Tool]) -> None:
    for index in range(7):
        await call(tools, "memory_add_fact", content=f"Remise client {index}", category="note")

    default = await call(tools, "memory_search", query="remise")
    limited = await call(tools, "memory_search", query="remise", limit=2)

    assert len(default.data["results"]) == 5
    assert len(limited.data["results"]) == 2


async def test_search_without_match(tools: dict[str, Tool]) -> None:
    result = await call(tools, "memory_search", query="météo")

    assert result.ok
    assert result.data == {"results": []}
    assert result.summary == "Rien en mémoire"


# --- errors are failed results, with the codes of memory.md §3.4 -------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("memory_delete", {"kind": "fact", "ref": "99"}, "not_found"),
        ("memory_update", {"kind": "glossary", "ref": "inconnu", "patch": {"sql_hint": "x"}}, "not_found"),
        ("memory_relate", {"src": "Lemaire", "type": "managed_by", "dst": "Personne"}, "entity_not_found"),
        ("memory_add_fact", {"content": "x", "category": "note", "entity": "Martin"}, "ambiguous_entity"),
        ("memory_update", {"kind": "entity", "ref": "1", "patch": {"id": 3}}, "invalid_patch"),
        ("memory_update", {"kind": "entity", "ref": "1", "patch": {"name": "Martin"}}, "conflict"),
    ],
)  # fmt: skip
async def test_store_errors_become_failed_results(
    tools: dict[str, Tool], name: str, arguments: dict[str, Any], code: str
) -> None:
    await call(tools, "memory_upsert_entity", type="client", name="Brasserie Lemaire")
    await call(tools, "memory_upsert_entity", type="client", name="Martin")
    await call(tools, "memory_upsert_entity", type="person", name="Martin")

    result = await call(tools, name, **arguments)

    assert not result.ok
    assert result.error is not None and result.error.code == code
    assert result.text == result.error.message
    assert result.memory_change is None


# --- in the Engine, through a registry -----------------------------------------------------


async def test_engine_emits_memory_updated_after_glossary_define(store: MemoryStore) -> None:
    registry = FakeToolRegistry(*(native(tool) for tool in memory_tools(store, "s-1")))
    engine = Engine(
        Config(),
        FakeClient(
            tool_request("glossary_define", term="client rentable", definition=RULE),
            answer("C'est noté."),
        ),
        registry=registry,
        session_id="s-1",
    )

    async with contextlib.aclosing(engine.run_turn("Retiens que client rentable = …")) as turn:
        emitted = [event async for event in turn]

    check_turn(emitted)
    kinds = [event.type for event in emitted if event.type not in ("token", "thinking")]
    assert kinds == [
        "turn_started",
        "tool_call",
        "tool_result",
        "memory_updated",
        "message_done",
    ]
    call_event = next(e for e in emitted if isinstance(e, events.ToolCall))
    assert (call_event.server, call_event.tool) == (None, "glossary_define")
    updated = next(e for e in emitted if isinstance(e, events.MemoryUpdated))
    assert (updated.op, updated.kind, updated.ref) == ("create", "glossary", "client rentable")
    assert store.get("glossary", "client rentable")["definition"] == RULE


async def test_engine_sends_the_memory_tools_to_the_model(store: MemoryStore) -> None:
    registry = FakeToolRegistry(*(native(tool) for tool in memory_tools(store)))
    client = FakeClient(answer("Bonjour."))
    engine = Engine(Config(), client, registry=registry, session_id="s-1")

    async with contextlib.aclosing(engine.run_turn("Bonjour")) as turn:
        [event async for event in turn]

    (sent,) = client.tools_sent
    assert sent is not None
    assert {tool["function"]["name"] for tool in sent} == set(load_definitions())
