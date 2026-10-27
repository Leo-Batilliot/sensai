"""Export and read-only sharing of a conversation (X3, EXPORT-01, websocket.md §4.10, §9)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from contract_rules import FIXTURES, SERVER, check_event, load_jsonl
from sensai.core.config import Config
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.server import create_app
from sensai.web.shares import ShareStore, to_markdown

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)
SLOW = Pacing(token=0.02, tool=0.01, judge=0.3, other=0.01)
SESSION = "3f2b9c1e-0000-4000-8000-0000000000e1"
ACT_1 = "Quels sont nos 5 clients les plus rentables ce trimestre ?"

Message = dict[str, Any]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>SENSAI</title>", encoding="utf-8")
    config = Config.model_validate({"web": {"share_dir": str(tmp_path / "shares")}})

    def make(pacing: Pacing = INSTANT) -> TestClient:
        return TestClient(create_app(config, MockBackend(config, pacing=pacing), static_dir=dist))

    yield make


def until(ws: WebSocketTestSession, kind: str, limit: int = 500) -> list[Message]:
    received: list[Message] = []
    for _ in range(limit):
        received.append(ws.receive_json())
        if received[-1]["type"] == kind:
            return received
    raise AssertionError(f"{kind} jamais reçu")


def ask(ws: WebSocketTestSession, content: str = ACT_1, end: str = "judge_report") -> None:
    ws.send_json({"type": "user_message", "content": content})
    until(ws, end)


# --- the store ----------------------------------------------------------------------------


def test_a_snapshot_is_frozen_listed_then_revoked(tmp_path: Path) -> None:
    store = ShareStore(tmp_path / "shares", "127.0.0.1", 8000)
    assert store.list() == []
    share = store.create({"model": "qwen3:8b", "events": []})
    assert len(share["token"]) == 22
    assert share["url"] == f"http://127.0.0.1:8000/share/{share['token']}"
    assert store.get(share["token"])["model"] == "qwen3:8b"  # type: ignore[index]
    assert [s["token"] for s in store.list()] == [share["token"]]
    assert store.revoke(share["token"]) is True
    assert store.get(share["token"]) is None
    assert store.revoke(share["token"]) is False  # SHARE_NOT_FOUND


@pytest.mark.parametrize("token", ["../../config", "a/b", "short", "x" * 65, "..%2Fconfig"])
def test_a_token_never_reaches_outside_the_directory(tmp_path: Path, token: str) -> None:
    store = ShareStore(tmp_path / "shares", "127.0.0.1", 8000)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert store.get(token) is None
    assert store.revoke(token) is False


def test_the_link_uses_the_address_the_browser_used(tmp_path: Path) -> None:
    local = ShareStore(tmp_path, "127.0.0.1", 8000)
    assert local.local_only is True
    assert local.url("t", "127.0.0.1:8000") == "http://127.0.0.1:8000/share/t"
    assert local.url("t") == "http://127.0.0.1:8000/share/t"  # no Host: the configuration
    open_server = ShareStore(tmp_path, "0.0.0.0", 8000)
    assert open_server.local_only is False
    assert open_server.url("t", "192.168.1.20:8000") == "http://192.168.1.20:8000/share/t"


def test_an_open_server_never_links_to_127_0_0_1_when_it_has_a_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 127.0.0.1 on another machine is that other machine (ERR_CONNECTION_REFUSED)
    monkeypatch.setattr("sensai.web.shares.network_address", lambda: "192.168.1.20")
    open_server = ShareStore(tmp_path, "0.0.0.0", 8000)
    assert open_server.url("t", "localhost:8000") == "http://192.168.1.20:8000/share/t"
    assert open_server.url("t") == "http://192.168.1.20:8000/share/t"
    monkeypatch.setattr("sensai.web.shares.network_address", lambda: None)
    assert open_server.url("t", "127.0.0.1:8000") == "http://127.0.0.1:8000/share/t"


# --- export -------------------------------------------------------------------------------


def test_markdown_tells_the_conversation() -> None:
    events = [e for e in load_jsonl(FIXTURES / "question.jsonl") if e["type"] != "token"]
    persona = {"id": "analyste", "name": "Contrôle de gestion", "role": "…"}
    adv = {"type": "persona_changed", "persona": {"id": "adv", "name": "ADV", "role": "…"}}
    markdown = to_markdown([*events, adv], "qwen3:8b", persona)
    assert "modèle qwen3:8b · profil Contrôle de gestion" in markdown
    assert "## 1. Quels sont nos 5 clients les plus rentables" in markdown
    assert "**Question de l'agent** : Le délai moyen" in markdown
    assert "Réponse : **Tout l'historique**" in markdown
    assert "```sql\nWITH margin AS" in markdown  # the query that worked, not the failed one
    assert "m.margin" not in markdown
    assert "**Vérification** : Vérifiée" in markdown
    assert "| name | marge_eur | delai_moyen_jours |" in markdown
    assert "| Garage Delmas | 29880.6 | — |" in markdown  # NULL shown as a dash
    assert "**Chiffres clés**" in markdown and "**Hypothèses**" in markdown
    assert markdown.rstrip().endswith("*Profil : ADV*")


def test_export_markdown_and_json(make_client: Callable[..., TestClient]) -> None:
    with make_client() as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ask(ws)
        markdown = client.get(f"/api/sessions/{SESSION}/export?format=md")
        exported = client.get(f"/api/sessions/{SESSION}/export?format=json")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "attachment" in markdown.headers["content-disposition"]
    assert "## 1. Quels sont nos 5 clients" in markdown.text
    body = exported.json()
    assert (body["session_id"], body["model"]) == (SESSION, "qwen3:8b")
    assert body["persona"]["id"] == "analyste"
    assert {e["type"] for e in body["events"]} >= {"turn_started", "message_done", "judge_report"}
    assert not any(e["type"] == "token" for e in body["events"])


def test_export_errors(make_client: Callable[..., TestClient]) -> None:
    with make_client() as client:
        unknown = client.get(f"/api/sessions/{SESSION}/export")
        with client.websocket_connect(f"/ws/{SESSION}") as ws:
            ws.receive_json()
            bad_format = client.get(f"/api/sessions/{SESSION}/export?format=pdf")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"]
    assert bad_format.status_code == 400


# --- sharing over the WebSocket -----------------------------------------------------------


def test_share_freezes_the_conversation_and_unshare_revokes_it(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client() as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ask(ws)
        ws.send_json({"type": "share", "id": "c-1"})
        created = ws.receive_json()
        token = created["token"]
        shared = client.get(f"/api/shares/{token}").json()
        listing = client.get("/api/shares").json()
        listed = listing["shares"]
        page = client.get(f"/share/{token}")
        ask(ws, "Quelle est la marge de Brasserie Lemaire ce trimestre ?")  # after the share
        frozen = client.get(f"/api/shares/{token}").json()
        ws.send_json({"type": "unshare", "id": "c-2", "token": token})
        revoked = ws.receive_json()
        ws.send_json({"type": "unshare", "id": "c-3", "token": token})
        again = ws.receive_json()
        gone = client.get(f"/api/shares/{token}")
    SERVER.validate(created)
    assert (created["reply_to"], created["url"]) == ("c-1", f"http://testserver/share/{token}")
    assert [s["token"] for s in listed] == [token]
    assert listing["local_only"] is True  # [web] host = 127.0.0.1 by default
    turns = {e["turn_id"] for e in shared["events"] if e["type"] == "turn_started"}
    assert len(turns) == 1 and shared["document"] is None
    assert frozen == shared  # the rest of the conversation never reaches the snapshot
    assert page.status_code == 200 and "SENSAI" in page.text
    SERVER.validate(revoked)
    assert (revoked["token"], revoked["reply_to"]) == (token, "c-2")
    check_event(again)
    assert (again["code"], again["reply_to"]) == ("SHARE_NOT_FOUND", "c-3")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "SHARE_NOT_FOUND"


def test_a_share_during_a_turn_leaves_the_running_turn_out(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client(SLOW) as client, client.websocket_connect(f"/ws/{SESSION}") as ws:
        ws.receive_json()
        ask(ws, end="message_done")
        ws.send_json({"type": "user_message", "content": ACT_1})
        until(ws, "tool_call")
        ws.send_json({"type": "share"})
        created = next(m for m in until(ws, "share_created") if m["type"] == "share_created")
        until(ws, "message_done")
        shared = client.get(f"/api/shares/{created['token']}").json()
    assert sum(e["type"] == "turn_started" for e in shared["events"]) == 1
    assert sum(e["type"] == "message_done" for e in shared["events"]) == 1


def test_a_long_table_is_cut_at_20_rows_and_says_so() -> None:
    def event(kind: str, **fields: Any) -> Message:
        return {"type": kind, "session_id": "s", "turn_id": "t-1", "ts": 0.0, **fields}

    rows = [[f"client {i}", i] for i in range(25)]
    events = [
        event("turn_started", origin="user", user_message="Tous les clients ?"),
        event(
            "sql_generated", call_id="k-1", sql="SELECT name, n FROM clients", operation="SELECT"
        ),
        event(
            "tool_result",
            call_id="k-1",
            ok=True,
            summary="25 lignes",
            data={"columns": ["name", "n"], "rows": rows, "row_count": 25, "truncated": False},
        ),
        event("message_done", content="Voici | la liste.", interrupted=False),
    ]
    markdown = to_markdown(events, "qwen3:8b", None)
    assert "| client 19 | 19 |" in markdown and "client 20" not in markdown
    assert "*20 premières lignes affichées, résultat plus long.*" in markdown
    assert "profil" not in markdown.splitlines()[2]  # no persona on this server
