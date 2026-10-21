"""The real Engine behind the WebSocket (WEB-08): RealBackend, RealEngine, lifespan.

Ollama is the scripted FakeClient and no MCP server is configured: the registry holds
the memory tools only, which is enough to go through a turn and a HITL confirmation
(memory_delete) end to end.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from contract_rules import check_event, check_turns
from fakes import MODEL, OTHER_MODEL, FakeClient, answer, tool_request
from sensai.core.config import Config, DatabaseConfig, McpConfig
from sensai.core.errors import OllamaUnavailable
from sensai.data.schema import connect, create_schema
from sensai.memory.store import MemoryStore
from sensai.web.engines.real import RealBackend
from sensai.web.server import create_app
from sensai.web.websocket.confirm import WebConfirmationBroker

SESSION = "3f2b9c1e-0000-4000-8000-0000000000a8"
FACT = "Claire veut les montants en k€"

Message = dict[str, Any]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    business = tmp_path / "business.db"
    conn = connect(business)
    create_schema(conn)
    conn.close()
    return Config(
        database=DatabaseConfig(business_path=business, memory_path=tmp_path / "memory.db"),
        mcp=McpConfig(servers={}),
    )


class Clients:
    """Client factory of the backend: every client plays the same script."""

    def __init__(self, *replies: Any, **options: Any) -> None:
        self.replies = replies
        self.options = options
        self.created: list[FakeClient] = []

    def __call__(self, _config: Config) -> FakeClient:
        client = FakeClient(*self.replies, **self.options)
        self.created.append(client)
        return client


def fact_ref(config: Config) -> str:
    with MemoryStore.open(config.database.memory_path) as store:
        return store.add_fact(FACT, "preference").ref


@pytest.fixture
async def started(config: Config) -> Any:
    backend = RealBackend(config, client_factory=Clients(answer("OK.")))
    await backend.start()
    yield backend
    await backend.stop()


def until(
    ws: WebSocketTestSession, stop: Callable[[Message], bool], limit: int = 200
) -> list[Message]:
    received: list[Message] = []
    for _ in range(limit):
        received.append(ws.receive_json())
        if stop(received[-1]):
            return received
    raise AssertionError(f"jamais reçu ; reçus : {[m['type'] for m in received]}")


def of_type(kind: str) -> Callable[[Message], bool]:
    return lambda message: message["type"] == kind


@pytest.fixture
def web(config: Config) -> Iterator[Callable[..., TestClient]]:
    """A TestClient on the real backend; `with` runs the lifespan (start and stop)."""

    def make(clients: Clients) -> TestClient:
        return TestClient(create_app(config, RealBackend(config, client_factory=clients)))

    yield make


# --- RealEngine -------------------------------------------------------------------------


async def test_describe_shows_the_padlock_of_the_hitl_policy(started: RealBackend) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    servers, tools = engine.describe()
    assert servers == []  # no MCP server in this configuration
    padlocks = {tool["name"]: tool["requires_confirmation"] for tool in tools}
    assert padlocks["memory_delete"] is True
    assert padlocks["memory_search"] is False
    assert padlocks["glossary_define"] is False  # additive memory tools never ask
    await engine.aclose()


async def test_session_settings_reach_the_engine(started: RealBackend) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    engine.set_config({"temperature": 0.5, "num_ctx": 4096, "judge_enabled": False})
    assert engine.engine.chat_options() == {"temperature": 0.5, "num_ctx": 4096}
    assert engine.engine.config.eval.judge_enabled is False
    assert engine.session_config["num_ctx"] == 4096
    await engine.aclose()


async def test_the_judge_follows_the_agent_model_unless_it_has_its_own(
    started: RealBackend,
) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    assert engine.engine.judge is not None

    def judge_model() -> str:
        assert engine.engine.judge is not None
        return engine.engine.judge.judge.model

    engine.set_model(OTHER_MODEL)
    assert (engine.model, judge_model()) == (OTHER_MODEL, OTHER_MODEL)
    engine.set_config({"judge_model": MODEL})
    assert judge_model() == MODEL
    engine.set_config({"judge_model": None})  # null = same model as the agent
    assert judge_model() == OTHER_MODEL
    await engine.aclose()


async def test_ui_action_says_it_is_not_plugged_yet(started: RealBackend) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    received = [event async for event in engine.run_ui_action("memory_delete", {})]
    assert len(received) == 1
    error = received[0].to_dict()
    check_event(error)
    assert error["turn_id"] is None
    await engine.aclose()


async def test_no_profile_before_pers_01(started: RealBackend) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    assert engine.persona is None and engine.set_persona("adv") is False
    assert started.personas() == (None, [])
    await engine.aclose()


async def test_new_engine_needs_the_shared_services(config: Config) -> None:
    backend = RealBackend(config, client_factory=Clients())
    with pytest.raises(RuntimeError):
        backend.new_engine("s-1", WebConfirmationBroker())


# --- REST: models and health ------------------------------------------------------------


async def test_models_and_their_tool_support(config: Config) -> None:
    backend = RealBackend(config, client_factory=Clients(models=(MODEL,)))
    assert await backend.list_models() == [{"name": MODEL, "size_bytes": 0, "supports_tools": True}]
    assert await backend.ollama_available() is True


async def test_ollama_down_gives_no_model_instead_of_an_error(config: Config) -> None:
    down = OllamaUnavailable("http://localhost:11434")
    backend = RealBackend(config, client_factory=Clients(show_error=down))
    assert await backend.list_models() == []
    assert await backend.ollama_available() is False


async def test_memory_snapshot_reads_the_store(started: RealBackend, config: Config) -> None:
    assert (await started.memory_snapshot())["facts"] == []
    started._services.store.add_fact(FACT, "preference")
    facts = (await started.memory_snapshot())["facts"]
    assert [fact["content"] for fact in facts] == [FACT]


# --- over the WebSocket -----------------------------------------------------------------


def test_a_turn_on_the_real_engine(web: Callable[..., TestClient]) -> None:
    with (
        web(Clients(answer("Bon", "jour."))) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ready = ws.receive_json()
        assert ready["type"] == "session_ready"
        assert (ready["model"], ready["supports_tools"]) == (MODEL, True)
        assert "memory_delete" in {tool["name"] for tool in ready["tools"]}
        ws.send_json({"type": "user_message", "content": "Bonjour"})
        events = until(ws, of_type("message_done"))
    for event in events:
        check_event(event)
    check_turns(events)
    assert events[-1]["content"] == "Bonjour."


def test_confirmation_approved_in_the_browser(
    web: Callable[..., TestClient], config: Config
) -> None:
    ref = fact_ref(config)
    script = Clients(tool_request("memory_delete", kind="fact", ref=ref), answer("C'est oublié."))
    with web(script) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "Oublie cette préférence."})
        required = until(ws, of_type("confirmation_required"))[-1]
        assert required["impact"]["per_table"] == {"facts": -1}
        ws.send_json({"type": "confirm", "action_id": required["action_id"], "decision": "approve"})
        events = until(ws, of_type("message_done"))
    resolved = next(e for e in events if e["type"] == "confirmation_resolved")
    assert (resolved["decision"], resolved["reason"]) == ("approve", "user")
    assert next(e for e in events if e["type"] == "tool_result")["ok"] is True
    assert next(e for e in events if e["type"] == "memory_updated")["op"] == "delete"
    with MemoryStore.open(config.database.memory_path) as store:
        assert store.dump()["facts"] == []


def test_disconnection_during_a_confirmation_refuses_it(
    web: Callable[..., TestClient], config: Config
) -> None:
    ref = fact_ref(config)
    script = Clients(
        tool_request("memory_delete", kind="fact", ref=ref), answer("Rien n'a changé.")
    )
    with web(script) as client:
        with client.websocket_connect(f"/ws/{SESSION}") as ws:
            ws.receive_json()
            ws.send_json({"type": "user_message", "content": "Oublie cette préférence."})
            until(ws, of_type("confirmation_required"))
        # Back on the same session: the history tells how the turn ended
        with client.websocket_connect(f"/ws/{SESSION}") as ws:
            history = ws.receive_json()["history"]
            while not any(e["type"] == "message_done" for e in history):
                history.append(ws.receive_json())  # the end of the turn, live
    resolved = next(e for e in history if e["type"] == "confirmation_resolved")
    assert (resolved["decision"], resolved["reason"]) == ("reject", "disconnected")
    assert (
        next(e for e in history if e["type"] == "tool_result")["error"]["code"] == "ACTION_REJECTED"
    )
    with MemoryStore.open(config.database.memory_path) as store:
        assert [fact["content"] for fact in store.dump()["facts"]] == [FACT]


def test_shutdown_closes_every_engine(web: Callable[..., TestClient]) -> None:
    clients = Clients(answer("OK."))
    with web(clients) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
    assert len(clients.created) == 2  # the REST client and the session's engine
    assert all(fake.closed for fake in clients.created)
