"""Export and read-only sharing of a conversation (X3, websocket.md §4.10 and §9).

A share is a frozen snapshot written to `[web] share_dir/<token>.json`: what comes after
in the conversation never shows up in it. Anyone with the link sees the snapshot, without
logging in (definition of X3). The link uses the address the browser used to reach the
server. The server listens on 127.0.0.1 by default: the link then only opens on this
machine (`local_only`), until `[web] host` opens it to the network.

The export and the snapshot carry the events of `session_ready.history` (no token, no
thinking), so that the front replays them with the same reducer as a live conversation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")  # secrets.token_urlsafe(16): 22 characters
EVERYWHERE = {"", "0.0.0.0", "::"}  # [web] host values that accept the network
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}  # this machine only

Event = dict[str, Any]


class ShareStore:
    """The snapshots of one server, one JSON file per token."""

    def __init__(self, directory: Path, host: str, port: int) -> None:
        self.directory = directory
        self.host = host  # where the server listens ([web] host and port)
        self.port = port

    @property
    def local_only(self) -> bool:
        """True when no other machine can reach the server, so neither the link."""
        return self.host in LOOPBACK

    def url(self, token: str, host_header: str | None = None) -> str:
        return f"{self.base(host_header)}/share/{token}"

    def base(self, host_header: str | None) -> str:
        """Where a browser reaches this server: the Host the user's browser sent. On a
        server open to the network, a loopback Host becomes the address on the network,
        since 127.0.0.1 on another machine is that other machine."""
        name, port = _split_host(host_header or f"{self.host}:{self.port}")
        if name in EVERYWHERE or (name in LOOPBACK and self.host in EVERYWHERE):
            name = network_address() or "127.0.0.1"
        return f"http://{name}{port}"

    def create(self, snapshot: dict[str, Any], host_header: str | None = None) -> dict[str, Any]:
        """Freeze the snapshot under a new token; returns {token, url, created_at}."""
        token = secrets.token_urlsafe(16)
        created_at = time.time()
        self.directory.mkdir(parents=True, exist_ok=True)
        content = json.dumps({"token": token, "created_at": created_at, **snapshot})
        # Written next to its final name, then renamed: a reader never sees half a file
        fd, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
            os.replace(temporary, self._path(token))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return {"token": token, "url": self.url(token, host_header), "created_at": created_at}

    def get(self, token: str) -> dict[str, Any] | None:
        path = self._path(token)
        if path is None or not path.is_file():
            return None
        snapshot: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return snapshot

    def list(self, host_header: str | None = None) -> list[dict[str, Any]]:
        """Active links, newest first."""
        if not self.directory.is_dir():
            return []
        shares = []
        for path in self.directory.glob("*.json"):
            if TOKEN.fullmatch(path.stem):
                created_at = json.loads(path.read_text(encoding="utf-8"))["created_at"]
                url = self.url(path.stem, host_header)
                shares.append({"token": path.stem, "url": url, "created_at": created_at})
        return sorted(shares, key=lambda share: share["created_at"], reverse=True)

    def revoke(self, token: str) -> bool:
        """False for an unknown or already revoked token (SHARE_NOT_FOUND)."""
        path = self._path(token)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True

    def _path(self, token: str) -> Path | None:
        # The token comes from a URL or a message: never a path outside the directory
        return self.directory / f"{token}.json" if TOKEN.fullmatch(token) else None


def _split_host(host: str) -> tuple[str, str]:
    """ "192.168.1.20:8000" -> ("192.168.1.20", ":8000"); IPv6 stays in its brackets."""
    if host.startswith("["):
        end = host.find("]") + 1
        return host[:end], host[end:]
    name, colon, port = host.partition(":")
    return name, colon + port


def network_address() -> str | None:
    """This machine's address on its network, or None (no network)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # UDP: no packet is sent, only the route is chosen
            address: str = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


# --- conversation export -------------------------------------------------------------------

TABLE_ROWS = 20  # rows of each result table in the Markdown export (US-X3-01)

BADGES = {
    "verified": "Vérifiée",
    "needs_review": "À vérifier",
    "contradicted": "Contredite",
    "unavailable": "Vérification indisponible",
}


def to_markdown(events: list[Event], model: str, persona: dict[str, Any] | None) -> str:
    """The conversation as a readable Markdown document: questions, answers, SQL, checks."""
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")
    profile = f" · profil {persona['name']}" if persona else ""
    lines = ["# Conversation SENSAI", "", f"Exportée le {now} · modèle {model}{profile}", ""]
    turns: dict[str, list[Event]] = {}
    order: list[str | Event] = []  # a turn id, or a persona_changed in place
    for event in events:
        turn_id = event.get("turn_id")
        if turn_id is None:
            if event["type"] == "persona_changed":
                order.append(event)
            continue
        if turn_id not in turns:
            turns[turn_id] = []
            order.append(turn_id)
        turns[turn_id].append(event)
    number = 0
    for item in order:
        if isinstance(item, dict):
            lines += ["---", "", f"*Profil : {item['persona']['name']}*", ""]
            continue
        turn = turns[item]
        started = next((e for e in turn if e["type"] == "turn_started"), None)
        if started is None:
            continue
        number += 1
        lines += ["---", ""]
        lines += _turn_markdown(number, started, turn)
    return "\n".join(lines).rstrip() + "\n"


def _turn_markdown(number: int, started: Event, turn: list[Event]) -> list[str]:
    of = {kind: [e for e in turn if e["type"] == kind] for kind in {e["type"] for e in turn}}
    if started["origin"] == "ui":
        call = (of.get("tool_call") or [{}])[0]
        result = (of.get("tool_result") or [{}])[0]
        summary = f" · {result['summary']}" if result.get("summary") else ""
        return [f"*Action depuis le panneau Mémoire : `{call.get('tool', 'outil')}`{summary}*", ""]
    lines = [f"## {number}. {started['user_message']}", ""]
    for asked in of.get("question_asked", []):
        answered = next(
            (
                e
                for e in of.get("question_answered", [])
                if e["question_id"] == asked["question_id"]
            ),
            None,
        )
        reply = _answer_text(asked, answered)
        lines += [f"> **Question de l'agent** : {asked['question']}  ", f"> {reply}", ""]
    done = next(iter(of.get("message_done", [])), None)
    if done is not None and done["content"]:
        lines += [done["content"], ""]
    if done is not None and done["interrupted"]:
        lines += ["*Réponse interrompue.*", ""]
    for error in of.get("error", []):
        lines += [f"*Erreur : {error['message']}*", ""]
    results = {e["call_id"]: e for e in of.get("tool_result", [])}
    for sql in of.get("sql_generated", []):
        result = results.get(sql["call_id"])
        if result is not None and result["ok"]:
            lines += ["**Requête SQL**", "", "```sql", sql["sql"], "```", ""]
            lines += _table(result.get("data"))
    for resolved in of.get("confirmation_resolved", []):
        verdict = "confirmée" if resolved["decision"] == "approve" else "non exécutée"
        lines += [f"**Action {verdict}** ({resolved['reason']})", ""]
    for report in of.get("judge_report", []):
        lines += [f"**Vérification** : {BADGES.get(report['badge'], report['badge'])}", ""]
        lines += _report(report.get("report"))
    return lines


def _table(data: Any) -> list[str]:
    """The first rows of a run_select result as a Markdown table (websocket.md §5.1, P-3)."""
    if not isinstance(data, dict) or not data.get("columns") or "rows" not in data:
        return []
    columns, rows = data["columns"], data["rows"]
    lines = [
        "| " + " | ".join(_cell(column) for column in columns) + " |",
        "|" + "---|" * len(columns),
        *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows[:TABLE_ROWS]),
    ]
    shown = min(len(rows), TABLE_ROWS)
    if shown < len(rows) or data.get("truncated"):
        lines += ["", f"*{shown} premières lignes affichées, résultat plus long.*"]
    return [*lines, ""]


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _report(report: Any) -> list[str]:
    """Key figures and assumptions of the judge's report, when there is one."""
    if not isinstance(report, dict):
        return []
    lines: list[str] = []
    figures = report.get("key_figures") or []
    if figures:
        lines += ["**Chiffres clés**", ""]
        lines += [f"- {f['label']} : {f['value']} {f['unit']}".rstrip() for f in figures]
        lines.append("")
    assumptions = report.get("assumptions") or []
    if assumptions:
        lines += ["**Hypothèses**", "", *(f"- {a}" for a in assumptions), ""]
    return lines


def _answer_text(asked: Event, answered: Event | None) -> str:
    if answered is None or answered["status"] != "answered":
        return "Sans réponse : l'agent a fait une hypothèse."
    option = next((o for o in asked["options"] if o["id"] == answered["option_id"]), None)
    return f"Réponse : **{option['label'] if option else answered['text']}**"
