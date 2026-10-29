"""The committee note on the web side (WEB-17, M4, memory.md §7.5): REST routes, the
agent's write replayed by the MockEngine, and the Note panel's ui_action on the mock and
on the real engine."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from contract_rules import SERVER, check_event, check_turns
from fakes import FakeClient, answer
from sensai.core.config import Config, DatabaseConfig, McpConfig
from sensai.data.schema import connect, create_schema
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.engines.real import RealBackend
from sensai.web.server import create_app

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)
SESSION = "3f2b9c1e-0000-4000-8000-0000000000f1"
ACT_2 = "Mets à jour la section Rentabilité de la note du comité."

Message = dict[str, Any]


@pytest.fixture
def mock_client(tmp_path: Path) -> Iterator[TestClient]:
    config = Config.model_validate({"web": {"share_dir": str(tmp_path / "shares")}})
    with TestClient(create_app(config, MockBackend(config, pacing=INSTANT))) as client:
        yield client


@pytest.fixture
def real_client(tmp_path: Path) -> Iterator[TestClient]:
    business = tmp_path / "business.db"
    conn = connect(business)
    create_schema(conn)
    conn.close()
    config = Config(
        database=DatabaseConfig(business_path=business, memory_path=tmp_path / "memory.db"),
        mcp=McpConfig(servers={}),
    )

    def clients(_config: Config) -> FakeClient:
        return FakeClient(answer("OK."))

    with TestClient(create_app(config, RealBackend(config, client_factory=clients))) as client:
        yield client


def turn(ws: WebSocketTestSession, message: Message, limit: int = 200) -> list[Message]:
    """Send a message, then receive until the end of its turn (message_done)."""
    ws.send_json(message)
    received: list[Message] = []
    for _ in range(limit):
        received.append(ws.receive_json())
        if received[-1]["type"] in ("message_done", "confirmation_required"):
            return received
    raise AssertionError(f"tour sans fin : {[m['type'] for m in received]}")


def patch(section_id: str, content: str, base_revision: int) -> Message:
    arguments = {"section_id": section_id, "op": "replace", "content": content}
    return {
        "type": "ui_action",
        "tool": "document_patch",
        "arguments": {**arguments, "base_revision": base_revision},
    }


def sections(client: TestClient) -> dict[str, str]:
    note = client.get("/api/documents/current").json()
    return {section["section_id"]: section["content"] for section in note["sections"]}


# --- REST ---------------------------------------------------------------------------------


def test_the_note_starts_from_its_template(mock_client: TestClient) -> None:
    note = mock_client.get("/api/documents/current").json()
    assert (note["id"], note["title"], note["revision"]) == (
        "note-comite",
        "Note au comité de direction",
        0,
    )
    assert [s["section_id"] for s in note["sections"]][2] == "rentabilite"
    assert mock_client.get("/api/documents/current/revisions").json() == {"revisions": []}
    exported = mock_client.get("/api/documents/current/export?format=md")
    assert exported.headers["content-type"].startswith("text/markdown")
    assert 'filename="note-comite-r0.md"' in exported.headers["content-disposition"]
    assert exported.text.startswith("# Note au comité de direction\n\n## Synthèse")
    assert mock_client.get("/api/documents/current/export?format=pdf").status_code == 400


def test_session_ready_names_the_note(mock_client: TestClient) -> None:
    with mock_client.websocket_connect(f"/ws/{SESSION}") as ws:
        ready = ws.receive_json()
    SERVER.validate(ready)
    assert ready["document_id"] == "note-comite"


# --- the agent writes (MockEngine, act 2) ----------------------------------------------------


def test_the_agent_changes_only_the_profitability_section(mock_client: TestClient) -> None:
    before = sections(mock_client)
    with mock_client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        events = turn(ws, {"type": "user_message", "content": ACT_2})
    check_turns(events)
    updated = next(e for e in events if e["type"] == "document_updated")
    assert (updated["section_id"], updated["op"], updated["revision"]) == (
        "rentabilite",
        "replace",
        1,
    )
    after = sections(mock_client)
    changed = [section for section in after if after[section] != before[section]]
    assert changed == ["rentabilite"]
    assert "Cabinet Roussel : 31,2 %" in after["rentabilite"]
    revisions = mock_client.get("/api/documents/current/revisions").json()["revisions"]
    assert [(r["revision"], r["op"], r["actor"]) for r in revisions] == [(1, "replace", "agent")]


def test_the_agent_writes_on_the_note_as_the_user_left_it(mock_client: TestClient) -> None:
    with mock_client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        turn(ws, patch("synthese", "Trimestre en hausse.", 0))
        events = turn(ws, {"type": "user_message", "content": ACT_2})
    call = next(e for e in events if e["type"] == "tool_call" and e["tool"] == "document_patch")
    assert call["arguments"]["base_revision"] == 1  # not the 0 of the fixture
    assert next(e for e in events if e["type"] == "document_updated")["revision"] == 2
    assert sections(mock_client)["synthese"] == "Trimestre en hausse."


# --- the Note panel (ui_action), on the mock and on the real engine --------------------------


@pytest.fixture(params=["mock", "real"])
def client(request: pytest.FixtureRequest) -> TestClient:
    fixture: TestClient = request.getfixturevalue(f"{request.param}_client")
    return fixture


def test_a_section_edited_in_the_panel(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        events = turn(ws, patch("rentabilite", "Trois clients rentables.", 0))
    for event in events:
        check_event(event)
    check_turns(events)
    assert [e["type"] for e in events] == [
        "turn_started",
        "tool_call",
        "tool_result",
        "document_updated",
        "message_done",
    ]
    assert events[0]["origin"] == "ui" and events[0]["user_message"] is None
    assert events[3]["summary"] == "Section « Rentabilité » mise à jour (révision 1)"
    assert sections(client)["rentabilite"] == "Trois clients rentables."
    revisions = client.get("/api/documents/current/revisions").json()["revisions"]
    assert [(r["op"], r["actor"]) for r in revisions] == [("replace", "user")]


def test_an_edit_on_an_old_revision_is_refused(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        turn(ws, patch("rentabilite", "Première version.", 0))
        events = turn(ws, patch("rentabilite", "Écrite sur la révision 0.", 0))
    result = next(e for e in events if e["type"] == "tool_result")
    assert not result["ok"] and result["error"]["code"] == "REVISION_CONFLICT"
    assert not any(e["type"] == "document_updated" for e in events)
    assert sections(client)["rentabilite"] == "Première version."


@pytest.mark.parametrize(("decision", "kept"), [("reject", True), ("approve", False)])
def test_deleting_a_section_asks_first(client: TestClient, decision: str, kept: bool) -> None:
    delete = {
        "type": "ui_action",
        "tool": "document_delete_section",
        "arguments": {"section_id": "actions", "base_revision": 0},
    }
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        required = turn(ws, delete)[-1]
        assert required["type"] == "confirmation_required"
        assert required["impact"]["sample"][0]["section_id"] == "actions"
        confirm = {"type": "confirm", "action_id": required["action_id"], "decision": decision}
        events = turn(ws, confirm)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is not kept
    assert ("actions" in sections(client)) is kept


def test_going_back_to_a_revision(client: TestClient) -> None:
    restore = {"type": "ui_action", "tool": "document_restore", "arguments": {"revision": 0}}
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        turn(ws, patch("rentabilite", "À annuler.", 0))
        events = turn(ws, restore)
    updated = next(e for e in events if e["type"] == "document_updated")
    assert (updated["op"], updated["section_id"], updated["revision"]) == ("restore", None, 2)
    assert sections(client)["rentabilite"] == ""
    revisions = client.get("/api/documents/current/revisions").json()["revisions"]
    assert [r["op"] for r in revisions] == ["replace", "restore"]  # nothing erased


def test_a_share_freezes_the_note(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        turn(ws, patch("rentabilite", "Version partagée.", 0))
        ws.send_json({"type": "share"})
        token = ws.receive_json()["token"]
        turn(ws, patch("rentabilite", "Version suivante.", 1))
    document = client.get(f"/api/shares/{token}").json()["document"]
    shared = {section["section_id"]: section["content"] for section in document["sections"]}
    assert (document["revision"], shared["rentabilite"]) == (1, "Version partagée.")


def test_the_memory_actions_stay_unplugged_on_the_real_engine(real_client: TestClient) -> None:
    action = {"type": "ui_action", "tool": "memory_delete", "arguments": {}}
    with real_client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json(action)
        error = ws.receive_json()
    assert (error["type"], error["code"]) == ("error", "INTERNAL")


def test_document_read_is_not_a_panel_action(mock_client: TestClient) -> None:
    action = {"type": "ui_action", "tool": "document_read", "arguments": {}}
    with mock_client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ws.send_json(action)
        error = ws.receive_json()
    assert (error["type"], error["code"]) == ("error", "UI_ACTION_FORBIDDEN")
