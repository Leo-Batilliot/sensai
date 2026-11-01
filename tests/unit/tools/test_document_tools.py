"""Committee note tools (ARTIF-01): contract definitions, results, errors, use by the Engine."""

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, FakeTool, FakeToolRegistry, answer, tool_request
from sensai.core import events
from sensai.core.config import Config, HitlConfig
from sensai.core.confirm import Decision, PendingAction
from sensai.core.engine import Engine
from sensai.core.prompts import SystemPrompt
from sensai.documents.store import DocumentStore
from sensai.memory.schema import open_database
from sensai.tools.base import Tool, ToolInfo, ToolResult
from sensai.tools.document_tools import document_tools as make_tools
from sensai.tools.document_tools import load_definitions
from sensai.tools.policy import evaluate


@pytest.fixture
def note() -> Iterator[DocumentStore]:
    with DocumentStore(open_database(":memory:")) as store:
        yield store


@pytest.fixture
def tools(note: DocumentStore) -> dict[str, Tool]:
    return {tool.name: tool for tool in make_tools(note, session_id="s-1")}


async def call(tools: dict[str, Tool], tool: str, /, **arguments: Any) -> ToolResult:
    return await tools[tool].call(arguments)


def info(tool: Tool) -> ToolInfo:
    """A native tool as the registry sees it: no server, no prefix, trusted."""
    return ToolInfo(
        name=tool.name,
        server=None,
        original_name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        annotations=tool.annotations,
        trusted=True,
    )


# --- definitions ------------------------------------------------------------------------


def test_the_model_gets_three_tools_and_the_interface_four(note: DocumentStore) -> None:
    definitions = load_definitions()
    model = make_tools(note)
    interface = make_tools(note, actor="user")

    assert [tool.name for tool in model] == ["document_read", "document_patch", "document_delete_section"]  # fmt: skip
    assert [tool.name for tool in interface] == list(definitions)  # document_restore: ui only
    for tool in interface:
        assert tool.description == definitions[tool.name]["description"]
        assert tool.input_schema == definitions[tool.name]["inputSchema"]


def test_only_the_deletion_asks_for_a_confirmation(tools: dict[str, Tool]) -> None:
    # memory.md §7.3: patch is reversible and versioned, delete is destructive
    actions = {name: evaluate(info(tool), {}, HitlConfig()).action for name, tool in tools.items()}
    assert actions == {
        "document_read": "allow",
        "document_patch": "allow",
        "document_delete_section": "confirm",
    }


# --- results ----------------------------------------------------------------------------


async def test_read_the_plan_then_a_section(tools: dict[str, Tool]) -> None:
    plan = await call(tools, "document_read")
    section = await call(tools, "document_read", section_id="synthese")

    assert plan.ok and plan.summary == "Plan de la note, révision 0"
    assert json.loads(plan.text)["sections"][0]["section_id"] == "synthese"
    assert section.summary == "Section « Synthèse » lue"
    assert plan.document_change is None and section.document_change is None


async def test_a_patch_carries_its_change(tools: dict[str, Tool]) -> None:
    result = await call(
        tools,
        "document_patch",
        section_id="rentabilite",
        op="replace",
        content="Top 5.",
        base_revision=0,
    )

    assert result.ok
    assert result.data == {
        "doc_id": "note-comite",
        "revision": 1,
        "op": "replace",
        "section_id": "rentabilite",
        "summary": "Section « Rentabilité » mise à jour (révision 1)",
    }
    assert result.document_change is not None
    assert (result.document_change.revision, result.document_change.op) == (1, "replace")


async def test_a_write_by_the_interface_is_recorded_as_the_user(note: DocumentStore) -> None:
    patch = next(
        tool for tool in make_tools(note, "s-2", actor="user") if tool.name == "document_patch"
    )

    await patch.call({"section_id": "synthese", "op": "append", "content": "x", "base_revision": 0})

    assert note.revisions()[0]["actor"] == "user"


async def test_line_breaks_escaped_twice_by_the_model_are_restored(note: DocumentStore) -> None:
    # Acceptance test of 01/11: the table of act 2 reached the note on one line
    table = "| Client | Marge |\\n|---|---|\\n| Boutique Marie | 37,7 % |"
    model = {tool.name: tool for tool in make_tools(note, "s-1")}
    user = {tool.name: tool for tool in make_tools(note, "s-1", actor="user")}

    await call(model, "document_patch", section_id="rentabilite", op="replace", content=table, base_revision=0)  # fmt: skip
    await call(user, "document_patch", section_id="synthese", op="replace", content=table, base_revision=1)  # fmt: skip

    rentabilite = note.read("rentabilite")["content"]
    assert rentabilite == "| Client | Marge |\n|---|---|\n| Boutique Marie | 37,7 % |"
    assert note.read("synthese")["content"] == table  # typed by the user: kept as is


async def test_real_line_breaks_keep_their_backslashes(tools: dict[str, Tool]) -> None:
    content = "Export dans C:\\new\\rapports\nà relire."

    await call(tools, "document_patch", section_id="synthese", op="replace", content=content, base_revision=0)  # fmt: skip

    section = await call(tools, "document_read", section_id="synthese")
    assert section.data is not None and section.data["content"] == content


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"section_id": "synthese", "op": "replace", "content": "x", "base_revision": 7}, "REVISION_CONFLICT"),
        ({"section_id": "risques", "op": "replace", "content": "x", "base_revision": 0}, "section_not_found"),
        ({"section_id": "synthese", "op": "insert_after", "content": "x", "base_revision": 0}, "invalid_patch"),
        ({"section_id": "synthese", "op": "replace", "content": "x" * 4001, "base_revision": 0}, "content_too_long"),
    ],
)  # fmt: skip
async def test_errors_are_results_for_the_model(
    tools: dict[str, Tool], arguments: dict[str, Any], code: str
) -> None:
    result = await call(tools, "document_patch", **arguments)

    assert not result.ok
    assert result.error is not None and result.error.code == code
    assert result.document_change is None


async def test_an_unchanged_section_carries_no_change(tools: dict[str, Tool]) -> None:
    result = await call(
        tools, "document_patch", section_id="synthese", op="replace", content="", base_revision=0
    )

    assert result.ok and result.document_change is None


# --- in the Engine ----------------------------------------------------------------------


class Approve:
    async def request(self, action: PendingAction) -> Decision:
        return Decision("approve", "user")


async def run(engine: Engine, message: str) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        return [event async for event in turn]


async def test_a_patch_by_the_model_emits_document_updated(note: DocumentStore) -> None:
    client = FakeClient(
        tool_request(
            "document_patch",
            section_id="rentabilite",
            op="replace",
            content="Top 5.",
            base_revision=0,
        ),
        answer("La section Rentabilité est rédigée."),
    )
    registry = FakeToolRegistry(*(FakeTool(info(tool), tool.call) for tool in make_tools(note)))
    engine = Engine(Config(), client, registry=registry, documents=note, session_id="s-1")

    emitted = await run(engine, "Rédige la section rentabilité.")

    check_turn(emitted)
    updated = [event for event in emitted if isinstance(event, events.DocumentUpdated)]
    assert len(updated) == 1
    assert (updated[0].doc_id, updated[0].revision, updated[0].section_id, updated[0].op) == (
        "note-comite",
        1,
        "rentabilite",
        "replace",
    )
    position = emitted.index(updated[0])
    assert emitted[position - 1].type == "tool_result"


async def test_deleting_a_section_waits_for_a_confirmation(note: DocumentStore) -> None:
    client = FakeClient(
        tool_request("document_delete_section", section_id="actions", base_revision=0),
        answer("Section supprimée."),
    )
    registry = FakeToolRegistry(*(FakeTool(info(tool), tool.call) for tool in make_tools(note)))
    engine = Engine(Config(), client, registry=registry, documents=note, broker=Approve())

    emitted = await run(engine, "Supprime la section des actions.")

    check_turn(emitted)
    kinds = [event.type for event in emitted]
    assert kinds.index("confirmation_required") < kinds.index("document_updated")
    assert "actions" not in [s["section_id"] for s in note.outline()["sections"]]


async def test_without_a_confirmation_the_section_stays(note: DocumentStore) -> None:
    client = FakeClient(
        tool_request("document_delete_section", section_id="actions", base_revision=0),
        answer("Suppression refusée."),
    )
    registry = FakeToolRegistry(*(FakeTool(info(tool), tool.call) for tool in make_tools(note)))
    engine = Engine(Config(), client, registry=registry, documents=note)  # refuses by default

    emitted = await run(engine, "Supprime la section des actions.")

    assert "document_updated" not in [event.type for event in emitted]
    assert note.revision == 0


async def test_the_plan_of_the_note_fills_its_hole(note: DocumentStore) -> None:
    # memory.md §7.3: the model knows section ids and base_revision without a call
    note.patch("synthese", "replace", "Contenu non montré.", 0)
    client = FakeClient(answer("Voici."))
    engine = Engine(
        Config(), client, system_prompt=SystemPrompt("$document_outline", {}), documents=note
    )

    await run(engine, "Où en est la note ?")

    prompt = client.calls[0][1][0]["content"]
    assert prompt == note.outline_text()
    assert "révision 1" in prompt and "Contenu non montré" not in prompt
