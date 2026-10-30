"""Policy and confirmation in the tool loop of the Engine (HITL-01/02, tools.md §4)."""

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, FakeTool, FakeToolRegistry, answer, tool_request
from sensai.core import events
from sensai.core.config import Config, HitlConfig
from sensai.core.confirm import Decision, PendingAction
from sensai.core.engine import Engine
from sensai.mcp.client import ToolAnnotations
from sensai.mcp_servers.sql_toolbox.guard import FORBIDDEN, READ, WRITE, Classification
from sensai.memory.store import MemoryStore
from sensai.tools.base import ToolInfo, ToolResult
from sensai.tools.memory_tools import memory_tools
from sensai.tools.registry import Registry

DELETE = "memory_delete"
RUN_SELECT = "sql_toolbox__run_select"


class ScriptedBroker:
    """Answers every request with the same decision, and remembers what it was asked."""

    def __init__(self, decision: Decision | None) -> None:
        self.decision = decision  # None: never answers
        self.requests: list[PendingAction] = []

    async def request(self, action: PendingAction) -> Decision:
        self.requests.append(action)
        if self.decision is None:
            await asyncio.Event().wait()
        assert self.decision is not None
        return self.decision


def recording_tool(
    name: str, annotations: ToolAnnotations, *, server: str | None
) -> tuple[FakeTool, list[dict[str, Any]]]:
    """A tool that records its calls: the list stays empty when nothing ran."""
    calls: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any]) -> ToolResult:
        calls.append(arguments)
        return ToolResult(ok=True, text="fait", summary="fait")

    info = ToolInfo(
        name=name,
        server=server,
        original_name=name.split("__", 1)[1] if server else name,
        description="",
        input_schema={"type": "object"},
        annotations=annotations,
        trusted=True,
    )
    return FakeTool(info, handler), calls


def classify(sql: str) -> Classification:
    kind = WRITE if sql.lstrip().upper().startswith("DELETE") else READ
    if sql.startswith("ATTACH"):
        kind = FORBIDDEN
    return Classification(kind, ("DELETE",) if kind == WRITE else (), (), "attach")


def engine(
    *replies: list,
    tools: list[FakeTool],
    broker: ScriptedBroker | None = None,
    hitl: HitlConfig | None = None,
) -> Engine:
    return Engine(
        Config(hitl=hitl or HitlConfig()),
        FakeClient(*replies),
        registry=FakeToolRegistry(*tools),
        broker=broker,
        session_id="s-1",
        sql_classifier=classify,
    )


async def run(engine: Engine) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn("Question")) as turn:
        emitted = [event async for event in turn]
    check_turn(emitted)
    return emitted


def of_type(emitted: list[events.Event], kind: type) -> list[Any]:
    return [event for event in emitted if isinstance(event, kind)]


DESTRUCTIVE = ToolAnnotations(read_only=False, destructive=True, idempotent=True, open_world=False)


async def test_rejected_action_never_runs() -> None:
    tool, calls = recording_tool(DELETE, DESTRUCTIVE, server=None)
    broker = ScriptedBroker(Decision("reject", "user"))
    eng = engine(
        tool_request(DELETE, kind="fact", ref="17"),
        answer("Rien supprimé."),
        tools=[tool],
        broker=broker,
    )
    emitted = await run(eng)

    assert calls == []
    (required,) = of_type(emitted, events.ConfirmationRequired)
    (resolved,) = of_type(emitted, events.ConfirmationResolved)
    (result,) = of_type(emitted, events.ToolResult)
    assert required.tool == DELETE and required.action_id == resolved.action_id
    assert (resolved.decision, resolved.reason) == ("reject", "user")
    assert result.error.code == "ACTION_REJECTED"
    # The model is told not to try again
    assert isinstance(eng.client, FakeClient)
    tool_message = eng.client.calls[1][1][-1]
    assert "Ne la retente pas" in tool_message["content"]


async def test_approved_action_runs() -> None:
    tool, calls = recording_tool(DELETE, DESTRUCTIVE, server=None)
    broker = ScriptedBroker(Decision("approve", "user"))
    emitted = await run(
        engine(
            tool_request(DELETE, kind="fact", ref="17"),
            answer("Supprimé."),
            tools=[tool],
            broker=broker,
        )
    )
    assert calls == [{"kind": "fact", "ref": "17"}]
    (result,) = of_type(emitted, events.ToolResult)
    assert result.ok
    # Order: confirmation before the result
    kinds = [event.type for event in emitted]
    assert kinds.index("confirmation_resolved") < kinds.index("tool_result")


async def test_no_answer_in_time_is_a_refusal(caplog: pytest.LogCaptureFixture) -> None:
    tool, calls = recording_tool(DELETE, DESTRUCTIVE, server=None)
    eng = engine(
        tool_request(DELETE, kind="fact", ref="17"),
        answer("Délai dépassé."),
        tools=[tool],
        broker=ScriptedBroker(None),
        hitl=HitlConfig(confirmation_timeout_s=0.05),
    )
    emitted = await run(eng)
    (resolved,) = of_type(emitted, events.ConfirmationResolved)
    assert (resolved.decision, resolved.reason) == ("timeout", "timeout")
    assert calls == []
    # Logged (CORE-11), without the arguments: they may hold business data
    (record,) = [r for r in caplog.records if "not given in" in r.getMessage()]
    assert record.levelname == "WARNING" and DELETE in record.getMessage()
    assert "17" not in record.getMessage().replace(resolved.action_id, "")


async def test_read_only_tools_need_no_confirmation() -> None:
    tool, calls = recording_tool(RUN_SELECT, ToolAnnotations(read_only=True), server="sql_toolbox")
    broker = ScriptedBroker(Decision("reject", "user"))
    emitted = await run(
        engine(tool_request(RUN_SELECT, sql="SELECT 1"), answer("1"), tools=[tool], broker=broker)
    )
    assert broker.requests == []
    assert of_type(emitted, events.ConfirmationRequired) == []
    assert calls == [{"sql": "SELECT 1"}]


@pytest.mark.parametrize(
    ("sql", "hitl", "code"),
    [
        ("DELETE FROM clients", HitlConfig(), "SQL_FORBIDDEN"),
        ("SELECT 1", HitlConfig(deny=(RUN_SELECT,)), "TOOL_DENIED"),
    ],
)
async def test_denied_calls_never_run(sql: str, hitl: HitlConfig, code: str) -> None:
    tool, calls = recording_tool(RUN_SELECT, ToolAnnotations(read_only=True), server="sql_toolbox")
    broker = ScriptedBroker(Decision("approve", "user"))
    emitted = await run(
        engine(
            tool_request(RUN_SELECT, sql=sql),
            answer("Non."),
            tools=[tool],
            broker=broker,
            hitl=hitl,
        )
    )
    (result,) = of_type(emitted, events.ToolResult)
    assert result.error.code == code
    assert broker.requests == [] and calls == []


async def test_impact_without_preview_has_empty_figures() -> None:
    tool, calls = recording_tool(DELETE, DESTRUCTIVE, server=None)
    broker = ScriptedBroker(Decision("reject", "user"))
    emitted = await run(
        engine(tool_request(DELETE, kind="fact", ref="1"), answer("."), tools=[tool], broker=broker)
    )
    (required,) = of_type(emitted, events.ConfirmationRequired)
    assert required.impact == {
        "operation": "OTHER",
        "rows_affected": 0,
        "per_table": {},
        "sample": [],
        "blocked": [],
    }
    assert broker.requests[0].impact == required.impact


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    memory = MemoryStore.open(":memory:")
    yield memory


async def test_memory_delete_with_its_preview(store: MemoryStore) -> None:
    """The demo fallback for the confirmation: forgetting a fact, with its real preview."""
    fact = store.add_fact("Claire veut les montants en k€", "preference")
    ref = fact.ref
    registry = Registry(
        native_tools=memory_tools(store, session_id="s-1"),
        previewers={DELETE: lambda args: store.delete_impact(args["kind"], args["ref"])},
    )

    async def turn(decision: Decision) -> list[events.Event]:
        eng = Engine(
            Config(),
            FakeClient(tool_request(DELETE, kind="fact", ref=ref), answer("OK.")),
            registry=registry,
            broker=ScriptedBroker(decision),
            session_id="s-1",
            sql_classifier=classify,
        )
        return await run(eng)

    refused = await turn(Decision("reject", "user"))
    (required,) = of_type(refused, events.ConfirmationRequired)
    assert required.impact["per_table"] == {"facts": -1}
    assert required.impact["sample"][0]["ref"] == ref
    assert store.delete_impact("fact", ref)  # still there

    accepted = await turn(Decision("approve", "user"))
    assert of_type(accepted, events.MemoryUpdated)[0].op == "delete"
