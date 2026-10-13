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


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_destructive_action_waits_for_the_decision(client: TestClient, decision: str) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "content": ACT_4})
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
