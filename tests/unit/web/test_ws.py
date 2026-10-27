"""End-to-end tests of WS /ws/{session_id} with the MockEngine (websocket.md)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from contract_rules import SERVER, check_event, check_turns
from sensai.core.config import Config
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.server import create_app
from sensai.web.websocket.messages import MessageHandler

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)
# Slow enough for a message to arrive while a turn is still running
SLOW = Pacing(token=0.02, tool=0.01, judge=0.3, other=0.01)

SESSION = "3f2b9c1e-0000-4000-8000-000000000001"
ACT_1 = "Quels sont nos 5 clients les plus rentables ce trimestre ?"
ACT_4 = "Supprime les clients inactifs depuis plus de 18 mois."

Message = dict[str, Any]


def make_client(pacing: Pacing = INSTANT, config: Config | None = None) -> TestClient:
    config = config or Config()
    return TestClient(create_app(config, MockBackend(config, pacing=pacing)))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as test_client:
        yield test_client


def until(
    ws: WebSocketTestSession, stop: Callable[[Message], bool], limit: int = 500
) -> list[Message]:
    """Receive messages up to and including the first one that matches `stop`."""
    received: list[Message] = []
    for _ in range(limit):
        message = ws.receive_json()
        received.append(message)
        if stop(message):
            return received
    raise AssertionError(f"message attendu jamais reçu ; reçus : {[m['type'] for m in received]}")


def of_type(kind: str) -> Callable[[Message], bool]:
    return lambda message: message["type"] == kind


def connect(client: TestClient, session_id: str = SESSION) -> tuple[WebSocketTestSession, Message]:
    ws = client.websocket_connect(f"/ws/{session_id}").__enter__()
    return ws, ws.receive_json()


def engine_events(messages: list[Message]) -> list[Message]:
    return [m for m in messages if "turn_id" in m]


# --- connection ---------------------------------------------------------------------------


def test_session_ready_comes_first_and_follows_the_schema(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
    SERVER.validate(ready)
    assert ready["type"] == "session_ready"
    assert ready["resumed"] is False and ready["history"] == []
    assert ready["model"] == Config().ollama.model
    assert ready["ctx_max"] == ready["config"]["num_ctx"]
    assert any(tool["requires_confirmation"] for tool in ready["tools"])


def test_invalid_session_id_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/ws/bad!id") as ws:
        ws.receive_json()
    assert closed.value.code == 4400


def test_a_second_connection_replaces_the_first(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as first:
        first.receive_json()
        with client.websocket_connect(f"/ws/{SESSION}") as second:
            assert second.receive_json()["resumed"] is True
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_json()
    assert closed.value.code == 4409


# --- turns --------------------------------------------------------------------------------


def test_a_turn_replays_contract_events(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "id": "c-1", "content": ACT_1})
        events = until(ws, of_type("judge_report"))
    for event in events:
        check_event(event)
        assert event["session_id"] == SESSION
    check_turns(events)
    assert events[0]["user_message"] == ACT_1
    assert events[-1]["badge"] == "verified"


ACT_5 = "Exporte la note du comité dans reports/, en Markdown."


@pytest.mark.parametrize("question", [ACT_4, ACT_5], ids=["sql", "file"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_destructive_action_waits_for_the_decision(
    client: TestClient, decision: str, question: str
) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": question})
        pause = until(ws, of_type("confirmation_required"))
        action = pause[-1]["action_id"]
        ws.send_json({"type": "confirm", "action_id": action, "decision": decision})
        rest = until(ws, of_type("message_done"))
    check_turns(pause + rest)
    resolved = next(m for m in rest if m["type"] == "confirmation_resolved")
    assert (resolved["action_id"], resolved["decision"], resolved["reason"]) == (
        action,
        decision,
        "user",
    )
    result = next(m for m in rest if m["type"] == "tool_result")
    assert result["ok"] is (decision == "approve")


ACT_1_QUESTION = (
    "Quels sont nos 5 clients les plus rentables ce trimestre et quel est leur délai moyen"
    " de paiement ?"
)


def ask_act_1(ws: WebSocketTestSession) -> Message:
    ws.receive_json()
    ws.send_json({"type": "user_message", "content": ACT_1_QUESTION})
    return until(ws, of_type("question_asked"))[-1]


@pytest.mark.parametrize(
    ("answer", "summary"),
    [
        ({"option_id": "historique"}, "Réponse : Tout l'historique"),
        ({"text": "Les 12 derniers mois"}, "Réponse : Les 12 derniers mois"),
    ],
    ids=["option", "free-text"],
)
def test_the_clarifying_question_waits_for_the_answer(
    client: TestClient, answer: Message, summary: str
) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_act_1(ws)
        ws.send_json({"type": "answer", "question_id": asked["question_id"], **answer})
        rest = until(ws, of_type("message_done"))
    answered = next(m for m in rest if m["type"] == "question_answered")
    assert (answered["question_id"], answered["status"]) == (asked["question_id"], "answered")
    assert answered["option_id"] == answer.get("option_id")
    assert answered["text"] == answer.get("text")
    result = next(
        m for m in rest if m["type"] == "tool_result" and m["call_id"] == asked["call_id"]
    )
    assert result["summary"] == summary
    for message in [asked, *rest]:
        check_event(message)


def test_a_wrong_answer_leaves_the_question_open(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_act_1(ws)
        ws.send_json(
            {"type": "answer", "id": "c-1", "question_id": asked["question_id"], "option_id": "x"}
        )
        error = ws.receive_json()
        ws.send_json(
            {"type": "answer", "question_id": asked["question_id"], "option_id": "trimestre"}
        )
        rest = until(ws, of_type("message_done"))
    assert (error["code"], error["reply_to"]) == ("INVALID_MESSAGE", "c-1")
    assert next(m for m in rest if m["type"] == "question_answered")["option_id"] == "trimestre"


def test_an_unanswered_question_times_out_and_the_turn_goes_on() -> None:
    config = Config.model_validate({"agent": {"question_timeout_s": 0.05}})
    with make_client(config=config) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        asked = ask_act_1(ws)
        rest = until(ws, of_type("message_done"))
    assert asked["expires_in_s"] == 0.05
    assert next(m for m in rest if m["type"] == "question_answered")["status"] == "timeout"
    assert rest[-1]["interrupted"] is False  # the model makes an explicit assumption


def test_cancel_during_a_question_cancels_it(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ask_act_1(ws)
        ws.send_json({"type": "cancel"})
        rest = until(ws, of_type("message_done"))
    assert next(m for m in rest if m["type"] == "question_answered")["status"] == "cancelled"
    assert rest[-1]["interrupted"] is True


def test_confirmation_without_answer_times_out() -> None:
    config = Config.model_validate({"hitl": {"confirmation_timeout_s": 0.05}})
    with make_client(config=config) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_4})
        events = until(ws, of_type("message_done"))
    check_turns(events)
    pause = next(m for m in events if m["type"] == "confirmation_required")
    resolved = next(m for m in events if m["type"] == "confirmation_resolved")
    assert pause["expires_in_s"] == 0.05
    assert (resolved["decision"], resolved["reason"]) == ("timeout", "timeout")


def test_second_message_during_a_turn_is_busy() -> None:
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        ws.send_json({"type": "user_message", "id": "c-2", "content": ACT_1})
        error = until(ws, lambda m: m.get("reply_to") == "c-2")[-1]
    check_event(error)
    assert (error["code"], error["turn_id"]) == ("BUSY", None)


def test_judge_does_not_keep_the_session_busy() -> None:
    # The judge comes after message_done: a new question is accepted meanwhile (§6.1)
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, of_type("message_done"))
        ws.send_json({"type": "user_message", "id": "c-2", "content": ACT_1})
        events = until(ws, lambda m: m["type"] == "turn_started" or m.get("reply_to") == "c-2")
        assert events[-1]["type"] == "turn_started"
        until(ws, of_type("message_done"))


def test_cancel_interrupts_the_answer() -> None:
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        events = until(ws, of_type("token"))
        ws.send_json({"type": "cancel"})
        events += until(ws, of_type("message_done"))
        ws.send_json({"type": "reset", "id": "c-r"})
        after = ws.receive_json()
    check_turns(events)
    done = events[-1]
    assert done["interrupted"] is True and done["usage"] is None
    assert after["type"] == "session_reset"  # no judge for an interrupted turn


def test_cancel_works_while_the_previous_judge_is_still_running() -> None:
    # Regression: the judge of turn 1 ending during turn 2 made turn 2 uncancellable
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, of_type("message_done"))  # turn 1 answered, its judge still runs
        ws.send_json({"type": "user_message", "content": ACT_1})
        second = until(ws, lambda m: m["type"] == "turn_started")[-1]["turn_id"]
        until(ws, lambda m: m["type"] == "token" and m["turn_id"] == second)
        ws.send_json({"type": "cancel"})
        done = until(ws, lambda m: m["type"] == "message_done" and m["turn_id"] == second)[-1]
    assert done["interrupted"] is True


def test_cancel_refuses_a_pending_confirmation(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_4})
        events = until(ws, of_type("confirmation_required"))
        ws.send_json({"type": "cancel"})
        events += until(ws, of_type("message_done"))
    check_turns(events)
    resolved = next(m for m in events if m["type"] == "confirmation_resolved")
    assert (resolved["decision"], resolved["reason"]) == ("reject", "cancelled")
    assert events[-1]["interrupted"] is True


def test_ui_action_deletes_after_confirmation(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        arguments = {"kind": "fact", "ref": "17"}
        ws.send_json({"type": "ui_action", "tool": "memory_delete", "arguments": arguments})
        events = until(ws, of_type("confirmation_required"))
        ws.send_json(
            {"type": "confirm", "action_id": events[-1]["action_id"], "decision": "approve"}
        )
        events += until(ws, of_type("message_done"))
    for event in events:
        check_event(event)
    check_turns(events)
    assert events[0]["origin"] == "ui" and events[0]["user_message"] is None
    updated = next(m for m in events if m["type"] == "memory_updated")
    assert (updated["op"], updated["kind"], updated["ref"]) == ("delete", "fact", "17")
    assert events[-1]["usage"] is None


def test_the_memory_panel_reads_what_the_agent_remembers(client: TestClient) -> None:
    # Act 2: the mock replays glossary_define and keeps its memory in step (GET /api/memory)
    assert client.get("/api/memory").json()["glossary"] == []
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": "Un client rentable, c'est… Retiens-le."})
        until(ws, of_type("message_done"))
    memory = client.get("/api/memory").json()
    assert [entry["ref"] for entry in memory["glossary"]] == ["client rentable"]
    assert set(memory) == {"glossary", "entities", "relations", "facts"}


def test_deleting_from_the_panel_shows_the_cascade_and_empties_the_memory(
    client: TestClient,
) -> None:
    entity = client.get("/api/memory").json()["entities"][0]
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        arguments = {"kind": "entity", "ref": entity["ref"]}
        ws.send_json({"type": "ui_action", "tool": "memory_delete", "arguments": arguments})
        required = until(ws, of_type("confirmation_required"))[-1]
        # The real preview of the store: the entity takes its attached fact with it
        assert required["impact"]["per_table"] == {"entities": -1, "facts": -1}
        ws.send_json({"type": "confirm", "action_id": required["action_id"], "decision": "approve"})
        until(ws, of_type("message_done"))
    memory = client.get("/api/memory").json()
    assert memory["entities"] == []
    assert [fact["content"] for fact in memory["facts"]] == ["Claire veut les montants en k€"]


# --- protocol errors ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ({"type": "user_message", "id": "c-9", "content": "   "}, "INVALID_MESSAGE"),
        ({"type": "launch_missiles", "id": "c-9"}, "INVALID_MESSAGE"),
        (
            {"type": "confirm", "id": "c-9", "action_id": "nope", "decision": "approve"},
            "UNKNOWN_ACTION",
        ),
        (
            {"type": "answer", "id": "c-9", "question_id": "nope", "option_id": "trimestre"},
            "UNKNOWN_QUESTION",
        ),
        ({"type": "answer", "id": "c-9", "question_id": "q-1"}, "INVALID_MESSAGE"),
        ({"type": "set_model", "id": "c-9", "model": "absent:1b"}, "MODEL_NOT_FOUND"),
        ({"type": "set_config", "id": "c-9", "patch": {"row_limit": 5}}, "INVALID_CONFIG"),
        ({"type": "set_config", "id": "c-9", "patch": {"num_ctx": 100}}, "INVALID_CONFIG"),
        (
            {"type": "set_config", "id": "c-9", "patch": {"judge_model": "absent:1b"}},
            "MODEL_NOT_FOUND",
        ),
        (
            {
                "type": "ui_action",
                "id": "c-9",
                "tool": "sql_toolbox__execute_write",
                "arguments": {},
            },
            "UI_ACTION_FORBIDDEN",
        ),
    ],
)
def test_protocol_errors_keep_the_connection_open(
    client: TestClient, message: Message, code: str
) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json(message)
        error = ws.receive_json()
        ws.send_json({"type": "reset", "id": "c-r"})
        assert ws.receive_json()["type"] == "session_reset"  # still open
    check_event(error)
    assert (error["code"], error["reply_to"], error["recoverable"]) == (code, "c-9", True)


V1_1_MESSAGES: list[Message] = [
    {"type": "answer", "id": "c-9", "question_id": "q-1", "option_id": "trimestre"},
    {"type": "set_persona", "id": "c-9", "persona": "adv"},
    {"type": "share", "id": "c-9"},
    {"type": "unshare", "id": "c-9", "token": "kq3Vb0wLr8nT2xYc5HdJ1A"},
    {"type": "run_schedule", "id": "c-9", "schedule_id": "point-lundi"},
]


@pytest.mark.parametrize(
    "message",
    [m for m in V1_1_MESSAGES if not hasattr(MessageHandler, f"_on_{m['type']}")],
    ids=lambda m: m["type"],
)
def test_a_v1_1_message_not_handled_yet_is_refused_politely(
    client: TestClient, message: Message
) -> None:
    # websocket.md §4: valid against the schema, but this server does not handle it yet
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json(message)
        error = ws.receive_json()
        ws.send_json({"type": "reset", "id": "c-r"})
        assert ws.receive_json()["type"] == "session_reset"  # still open
    assert (error["code"], error["reply_to"]) == ("INVALID_MESSAGE", "c-9")
    assert "pas encore pris en charge" in error["message"]


def test_unreadable_json_is_an_invalid_message(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_text("{not json")
        error = ws.receive_json()
    assert error["code"] == "INVALID_MESSAGE" and "reply_to" not in error


# --- settings, reset, reconnection --------------------------------------------------------


def test_set_model_and_set_config_are_acknowledged(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_model", "id": "c-1", "model": "gemma2:9b"})
        changed_model = ws.receive_json()
        patch = {"temperature": 0.2, "judge_enabled": False}
        ws.send_json({"type": "set_config", "id": "c-2", "patch": patch})
        updated = ws.receive_json()
    SERVER.validate(changed_model)
    SERVER.validate(updated)
    assert (changed_model["model"], changed_model["supports_tools"]) == ("gemma2:9b", False)
    assert changed_model["reply_to"] == "c-1"
    assert sorted(updated["changed"]) == ["judge_enabled", "temperature"]
    assert updated["config"]["temperature"] == 0.2


# --- profiles (A5, WEB-15) ----------------------------------------------------------------


def tool_names(tools: list[Message]) -> set[str]:
    return {tool["name"] for tool in tools}


def test_session_ready_shows_the_profile_and_only_its_tools(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
    assert ready["persona"]["id"] == "analyste"
    assert [p["id"] for p in ready["personas"]] == ["analyste", "adv", "admin"]
    # Contrôle de gestion reads only (US-A5-02): no SQL write is shown to the model
    assert "sql_toolbox__execute_write" not in tool_names(ready["tools"])
    assert "sql_toolbox__run_select" in tool_names(ready["tools"])


def test_set_persona_changes_the_tools_and_keeps_the_padlocks(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_persona", "id": "c-1", "persona": "adv"})
        changed = ws.receive_json()
    SERVER.validate(changed)
    assert (changed["persona"]["id"], changed["reply_to"]) == ("adv", "c-1")
    write = next(t for t in changed["tools"] if t["name"] == "sql_toolbox__execute_write")
    assert write["requires_confirmation"] is True  # a profile never lifts a confirmation


def test_unknown_persona_is_refused(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_persona", "id": "c-1", "persona": "pirate"})
        error = ws.receive_json()
    assert (error["code"], error["reply_to"], error["turn_id"]) == (
        "PERSONA_NOT_FOUND",
        "c-1",
        None,
    )
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        assert ws.receive_json()["persona"]["id"] == "analyste"  # unchanged


def test_set_persona_is_accepted_during_a_turn() -> None:
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, of_type("turn_started"))
        ws.send_json({"type": "set_persona", "id": "c-1", "persona": "adv"})
        received = until(ws, of_type("persona_changed"))
        rest = until(ws, of_type("message_done"))
    assert not any(m["type"] == "error" for m in received + rest)


def test_the_profile_change_is_replayed_in_place_after_a_reload(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, of_type("judge_report"))
        ws.send_json({"type": "set_persona", "id": "c-1", "persona": "adv"})
        until(ws, of_type("persona_changed"))
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
    SERVER.validate(ready)
    assert ready["persona"]["id"] == "adv"
    kinds = [m["type"] for m in ready["history"]]
    assert kinds[-2:] == ["judge_report", "persona_changed"]  # after the turn, where it happened
    assert "reply_to" not in ready["history"][-1]


def test_rest_lists_the_profiles_and_their_tools(client: TestClient) -> None:
    body = client.get("/api/personas").json()
    assert body["current"] == "analyste"
    adv = next(p for p in body["personas"] if p["id"] == "adv")
    assert adv["name"] == "ADV" and "sql_toolbox__execute_write" in adv["tools"]
    analyste = next(p for p in body["personas"] if p["id"] == "analyste")
    assert "sql_toolbox__execute_write" not in analyste["tools"]


def test_reconnection_returns_the_history_without_tokens(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        live = until(ws, of_type("judge_report"))
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
    SERVER.validate(ready)
    assert ready["resumed"] is True
    history = ready["history"]
    assert [m["type"] for m in history] == [
        m["type"] for m in live if m["type"] not in ("token", "thinking")
    ]


def test_reset_empties_the_history(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, of_type("judge_report"))
        ws.send_json({"type": "reset", "id": "c-r"})
        reset = ws.receive_json()
    SERVER.validate(reset)
    assert reset["reply_to"] == "c-r"
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        assert ws.receive_json()["history"] == []


def test_disconnection_refuses_a_pending_confirmation(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_4})
        until(ws, of_type("confirmation_required"))
    # The end of the turn is either already in the history or still streaming
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
        events = list(ready["history"])
        if not any(m["type"] == "message_done" for m in events):
            events += until(ws, of_type("message_done"))
    resolved = next(m for m in events if m["type"] == "confirmation_resolved")
    assert (resolved["decision"], resolved["reason"]) == ("reject", "disconnected")
