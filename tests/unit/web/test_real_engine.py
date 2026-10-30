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
from fakes import MODEL, OTHER_MODEL, PERSONAS_DIR, FakeClient, answer, tool_request
from sensai.core.config import AgentConfig, Config, DatabaseConfig, McpConfig
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
        agent=AgentConfig(personas_dir=PERSONAS_DIR),
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
    options = engine.engine.chat_options()
    assert options == {"temperature": 0.5, "num_ctx": 4096, "num_predict": 8192}
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


async def test_a_session_starts_with_the_profile_of_the_configuration(
    started: RealBackend,
) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    assert engine.persona == {
        "id": "analyste",
        "name": "Contrôle de gestion",
        "role": "Copilote de Claire, contrôleuse de gestion",
    }
    _servers, tools = engine.describe()
    names = [tool["name"] for tool in tools]
    assert "file_read" in names and "file_write" not in names  # Claire only reads
    await engine.aclose()


async def test_set_persona_changes_the_tools_and_keeps_the_padlocks(started: RealBackend) -> None:
    engine = started.new_engine("s-1", WebConfirmationBroker())
    assert engine.set_persona("admin") is True
    padlocks = {tool["name"]: tool["requires_confirmation"] for tool in engine.describe()[1]}
    assert padlocks["file_write"] is True  # allowed by the profile, still confirmed
    assert padlocks["memory_delete"] is True
    assert engine.set_persona("compta") is False  # unknown: nothing changes
    assert engine.persona is not None and engine.persona["id"] == "admin"
    await engine.aclose()


async def test_personas_lists_every_profile_with_its_tools(started: RealBackend) -> None:
    current, profiles = started.personas()
    assert current == "analyste"
    assert [profile["id"] for profile in profiles] == ["admin", "adv", "analyste"]
    tools = {profile["id"]: profile["tools"] for profile in profiles}
    assert "file_write" in tools["admin"] and "file_write" not in tools["analyste"]
    assert "memory_search" in tools["adv"]


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


def test_a_profile_change_reaches_the_model(web: Callable[..., TestClient]) -> None:
    script = Clients(answer("42 jours."), answer("Oui."))
    with web(script) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
        assert ready["persona"]["id"] == "analyste"
        assert [profile["id"] for profile in ready["personas"]] == ["admin", "adv", "analyste"]
        ws.send_json({"type": "user_message", "content": "Délai moyen ?"})
        until(ws, of_type("message_done"))
        ws.send_json({"type": "set_persona", "id": "c-1", "persona": "admin"})
        changed = until(ws, of_type("persona_changed"))[-1]
        assert "file_write" in [tool["name"] for tool in changed["tools"]]
        ws.send_json({"type": "user_message", "content": "Et maintenant ?"})
        until(ws, of_type("message_done"))
    session_client = script.created[-1]  # the first one serves the REST routes
    messages = session_client.calls[1][1]
    assert "# Profil actif : Admin" in messages[0]["content"]
    note = {"role": "system", "content": "Profil actif : Admin (Copilote d'Inès, administratrice)"}
    assert messages[-2:] == [note, {"role": "user", "content": "Et maintenant ?"}]
    tools = [tool["function"]["name"] for tool in session_client.tools_sent[1] or []]
    assert "file_write" in tools


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


PERIOD = {
    "question": "Le délai moyen de paiement doit-il porter sur ce trimestre ou sur tout l'historique ?",
    "reason": "Les deux périodes donnent des délais très différents.",
    "options": [
        {"id": "trimestre", "label": "Ce trimestre"},
        {"id": "historique", "label": "Tout l'historique"},
    ],
}


def ask_period(ws: WebSocketTestSession) -> Message:
    ws.receive_json()
    ws.send_json({"type": "user_message", "content": "Quel est le délai moyen de paiement ?"})
    return until(ws, of_type("question_asked"))[-1]


def test_the_question_is_answered_in_the_browser(web: Callable[..., TestClient]) -> None:
    script = Clients(tool_request("ask_user", **PERIOD), answer("Sur ce trimestre : 41 jours."))
    with web(script) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_period(ws)
        assert asked["options"] == PERIOD["options"] and asked["expires_in_s"] == 120
        ws.send_json(
            {"type": "answer", "question_id": asked["question_id"], "option_id": "trimestre"}
        )
        events = until(ws, of_type("message_done"))
    for event in events:
        check_event(event)
    answered = next(e for e in events if e["type"] == "question_answered")
    assert (answered["status"], answered["option_id"]) == ("answered", "trimestre")
    assert (
        next(e for e in events if e["type"] == "tool_result")["summary"] == "Réponse : Ce trimestre"
    )
    sent = script.created[-1].calls[-1][1][-1]["content"]  # the tool message of the last call
    assert '"label": "Ce trimestre"' in sent


def test_an_invalid_answer_keeps_the_question_pending(web: Callable[..., TestClient]) -> None:
    script = Clients(tool_request("ask_user", **PERIOD), answer("Ok."))
    with web(script) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_period(ws)
        ws.send_json(
            {
                "type": "answer",
                "id": "c-1",
                "question_id": asked["question_id"],
                "option_id": "semestre",
            }
        )
        error = ws.receive_json()
        assert (error["code"], error["reply_to"]) == ("INVALID_MESSAGE", "c-1")
        ws.send_json(
            {"type": "answer", "question_id": asked["question_id"], "text": "Depuis janvier"}
        )
        events = until(ws, of_type("message_done"))
    check_event(error)
    answered = next(e for e in events if e["type"] == "question_answered")
    assert (answered["status"], answered["text"]) == ("answered", "Depuis janvier")


def test_cancel_resolves_a_pending_question(web: Callable[..., TestClient]) -> None:
    script = Clients(tool_request("ask_user", **PERIOD), answer("jamais"))
    with web(script) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_period(ws)
        ws.send_json({"type": "cancel"})
        events = until(ws, of_type("message_done"))
        ws.send_json(
            {
                "type": "answer",
                "id": "c-2",
                "question_id": asked["question_id"],
                "option_id": "trimestre",
            }
        )
        late = ws.receive_json()
    answered = next(e for e in events if e["type"] == "question_answered")
    assert answered["status"] == "cancelled"
    assert events[-1]["interrupted"] is True
    assert (late["code"], late["reply_to"]) == ("UNKNOWN_QUESTION", "c-2")
    check_event(late)


def test_disconnection_during_a_question_cancels_it(web: Callable[..., TestClient]) -> None:
    script = Clients(tool_request("ask_user", **PERIOD), answer("jamais"))
    with web(script) as client:
        with client.websocket_connect(f"/ws/{SESSION}") as ws:
            ask_period(ws)
        with client.websocket_connect(f"/ws/{SESSION}") as ws:
            history = ws.receive_json()["history"]
            while not any(e["type"] == "message_done" for e in history):
                history.append(ws.receive_json())
    check_turns(history)
    answered = next(e for e in history if e["type"] == "question_answered")
    assert answered["status"] == "cancelled"
    assert history[-1]["interrupted"] is True


def test_shutdown_closes_every_engine(web: Callable[..., TestClient]) -> None:
    clients = Clients(answer("OK."))
    with web(clients) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
    assert len(clients.created) == 2  # the REST client and the session's engine
    assert all(fake.closed for fake in clients.created)


# --- scheduled runs (X5) ------------------------------------------------------------------


async def test_a_scheduled_engine_cannot_write(config: Config) -> None:
    # Even when the model asks for a deletion, nothing changes: the tool is not offered,
    # and the Engine refuses a tool it does not know (V-4)
    ref = fact_ref(config)
    clients = Clients(tool_request("memory_delete", kind="fact", ref=ref), answer("Impossible."))
    backend = RealBackend(config, client_factory=clients)
    await backend.start()
    try:
        engine = backend.scheduled_engine("schedule-r-1")
        _, tools = engine.describe()
        names = {tool["name"] for tool in tools}
        assert "memory_search" in names and "ask_user" in names
        assert not names & {"memory_delete", "memory_update", "glossary_define", "file_write"}
        received = [event.to_dict() async for event in engine.run_turn("Oublie ce fait.")]
        await engine.aclose()
    finally:
        await backend.stop()
    kinds = [event["type"] for event in received]
    assert "confirmation_required" not in kinds and "memory_updated" not in kinds
    result = next(event for event in received if event["type"] == "tool_result")
    assert not result["ok"]
    with MemoryStore.open(config.database.memory_path) as store:
        assert any(fact["ref"] == ref for fact in store.dump()["facts"])
