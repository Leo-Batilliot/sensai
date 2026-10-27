"""MockEngine: replays the event fixtures so that the web works without the real engine.

Used by `sensai web --mock` (WEB-04) until the real engine is plugged in (WEB-08). It
follows tests/fixtures/events/README.md:

- one turn per message, picked by keywords (ROUTES below);
- ids, `session_id`, `ts` and the user's message are rewritten; pacing is realistic;
- on `confirmation_required`, it really waits for the decision through the broker, then
  plays the approve or reject branch (hitl_approve.jsonl / hitl_reject.jsonl);
- `cancel()` sets a flag: the turn ends with `message_done {interrupted: true}`.
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sensai.core.config import Config
from sensai.core.confirm import ConfirmationBroker, Decision, PendingAction
from sensai.memory.errors import MemoryStoreError
from sensai.memory.store import AuditContext, MemoryStore
from sensai.web.engines.backend import ModelInfo, Persona, PersonaTools
from sensai.web.paths import FIXTURES_DIR
from sensai.web.websocket.ask import WebQuestionBroker
from sensai.web.websocket.confirm import WebConfirmationBroker
from sensai.web.websocket.settings import session_settings

logger = logging.getLogger(__name__)

Event = dict[str, Any]
Turn = list[Event]

FIXTURES = (
    "nominal",
    "memory",
    "judge_contradiction",
    "hitl_approve",
    "hitl_reject",
    "file_write_approve",
    "file_write_reject",
    "errors",
)

# Scenarios that wait for a confirmation: <name>_approve.jsonl and <name>_reject.jsonl
PAUSED = frozenset({"hitl", "file_write"})


@dataclass(frozen=True)
class Route:
    keywords: tuple[str, ...]
    fixture: str
    turn: int | None  # None: cycle through the turns of the fixture


# First match wins; the user's message is lowercased. Default: nominal.jsonl (act 1).
ROUTES = (
    Route(("supprime",), "hitl", 0),
    Route(("exporte",), "file_write", 0),  # act 5
    Route(("retiens", "mémorise", "memorise"), "memory", 0),
    Route(("clients rentables",), "memory", 1),
    Route(("piège", "piege", "brasserie lemaire"), "judge_contradiction", 0),
    Route(("erreur", "panne"), "errors", None),
)

# A cancelled turn stops before any of these (a tool already called runs to completion)
INTERRUPTIBLE = frozenset({"token", "thinking", "tool_call", "message_done", "error"})

REJECTED_MESSAGES = {
    "cancelled": "Action annulée.",
    "disconnected": "Action annulée : l'interface s'est déconnectée.",
    "timeout": "Délai de confirmation dépassé : action annulée.",
}

MEMORY_TABLES = {
    "glossary": "glossary",
    "entity": "entities",
    "relation": "relations",
    "fact": "facts",
}

MOCK_MODELS: tuple[ModelInfo, ...] = (
    {"name": "qwen3:8b", "size_bytes": 5_200_000_000, "supports_tools": True},
    {"name": "llama3.1:8b", "size_bytes": 4_900_000_000, "supports_tools": True},
    {"name": "gemma2:9b", "size_bytes": 5_400_000_000, "supports_tools": False},
)

MOCK_SERVERS: tuple[dict[str, Any], ...] = (
    {"name": "sql_toolbox", "status": "up", "error": None},
    {"name": "time", "status": "up", "error": None},
    {
        "name": "filesystem",
        "status": "down",
        "error": "Serveur non lancé en mode démo (MockEngine).",
    },
)


def _tool(
    name: str,
    server: str | None,
    description: str,
    read_only: bool | None,
    destructive: bool | None,
    requires_confirmation: bool,
) -> dict[str, Any]:
    return {
        "name": name,
        "server": server,
        "description": description,
        "read_only": read_only,
        "destructive": destructive,
        "requires_confirmation": requires_confirmation,
    }


MOCK_TOOLS = (
    _tool("sql_toolbox__list_tables", "sql_toolbox", "Liste les tables", True, False, False),
    _tool("sql_toolbox__describe_table", "sql_toolbox", "Décrit une table", True, False, False),
    _tool(
        "sql_toolbox__run_select",
        "sql_toolbox",
        "Exécute une requête de lecture",
        True,
        False,
        False,
    ),
    _tool("sql_toolbox__execute_write", "sql_toolbox", "Exécute une écriture", False, True, True),
    _tool("time__get_current_time", "time", "Date et heure actuelles", None, None, False),
    _tool("memory_search", None, "Cherche dans la mémoire", True, False, False),
    _tool("glossary_define", None, "Définit un terme métier", False, False, False),
    _tool("memory_update", None, "Modifie une entrée de la mémoire", False, False, False),
    _tool("memory_delete", None, "Supprime une entrée de la mémoire", False, True, True),
    _tool("file_list", None, "Liste les dossiers autorisés", True, False, False),
    _tool("file_read", None, "Lit un fichier d'un dossier autorisé", True, False, False),
    _tool("file_write", None, "Écrit un fichier dans un dossier autorisé", False, True, True),
)


@dataclass(frozen=True)
class MockPersona:
    """A profile of v1.1-extensions.md §3.7, as PERS-01 will load it from personas/*.toml."""

    id: str
    name: str
    role: str
    allow: tuple[str, ...]  # fnmatch patterns

    def allows(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.allow)

    def info(self) -> Persona:
        return Persona(id=self.id, name=self.name, role=self.role)


READ_ONLY = (
    "time__*",
    "sql_toolbox__list_tables",
    "sql_toolbox__describe_table",
    "sql_toolbox__run_select",
    "memory_*",
    "glossary_define",
    "ask_user",
    "document_*",
    "file_list",
    "file_read",
)

# The three profiles of the scenario (docs/keynote/scenario.md §2): a profile only removes tools
MOCK_PERSONAS = (
    MockPersona(
        "analyste", "Contrôle de gestion", "Copilote de Claire, contrôleuse de gestion", READ_ONLY
    ),
    MockPersona(
        "adv",
        "ADV",
        "Copilote de Karim, responsable ADV",
        (*READ_ONLY, "sql_toolbox__preview_write", "sql_toolbox__execute_write"),
    ),
    MockPersona("admin", "Admin", "Copilote d'Inès, administratrice", (*READ_ONLY, "file_write")),
)
DEFAULT_PERSONA = "analyste"


@dataclass(frozen=True)
class Pacing:
    """Seconds slept before each event; tests use zero or tiny values."""

    token: float = 0.03
    tool: float = 0.4
    judge: float = 1.0
    other: float = 0.05

    def before(self, kind: str) -> float:
        if kind == "token":
            return self.token
        if kind == "tool_result":
            return self.tool
        if kind == "judge_report":
            return self.judge
        return self.other


def load_turns(path: Path) -> list[Turn]:
    """Group the events of a fixture by turn, in order."""
    turns: dict[str, Turn] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            turns.setdefault(event["turn_id"], []).append(event)
    return list(turns.values())


def _new_id() -> str:
    return str(uuid.uuid4())


class MockEngine:
    def __init__(
        self,
        session_id: str,
        config: Config,
        broker: ConfirmationBroker,
        turns: dict[str, list[Turn]],
        pacing: Pacing,
        memory: MemoryStore | None = None,
    ) -> None:
        self.session_id = session_id
        self._memory = memory  # kept in step with the replayed memory_updated events
        self.model = config.ollama.model
        self.session_config = session_settings(config)
        self._broker = broker
        self._turns = turns
        self._pacing = pacing
        self._confirmation_timeout_s = config.hitl.confirmation_timeout_s
        self._cancelled = False
        self._active_turn: object | None = None  # the turn that cancel() would stop
        self._next_error_turn = 0
        self._persona = _persona(DEFAULT_PERSONA)

    # --- WebEngine ----------------------------------------------------------------------

    @property
    def persona(self) -> Persona:
        return self._persona.info()

    def describe(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        tools = [dict(tool) for tool in MOCK_TOOLS if self._persona.allows(tool["name"])]
        return [dict(server) for server in MOCK_SERVERS], tools

    def set_persona(self, persona_id: str) -> bool:
        # The replayed fixtures do not change: only the tools shown follow the profile
        persona = next((p for p in MOCK_PERSONAS if p.id == persona_id), None)
        if persona is None:
            return False
        self._persona = persona
        return True

    def run_turn(self, user_message: str) -> AsyncIterator[Event]:
        prefix, branches = self._pick(user_message)
        return self._replay(prefix, branches, user_message)

    def run_ui_action(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[Event]:
        prefix, branches = _ui_action_turn(tool, arguments)
        if tool == "memory_delete" and branches is not None and self._memory is not None:
            # The real preview of the store, cascades included, as the engine would show it
            try:
                impact = self._memory.delete_impact(arguments["kind"], arguments["ref"])
            except (MemoryStoreError, KeyError):
                impact = None
            if impact is not None:
                prefix = [*prefix[:-1], {**prefix[-1], "impact": impact}]
        return self._replay(prefix, branches, None)

    def cancel(self) -> bool:
        """Same contract as Engine.cancel(): False (ignored) when no turn is running."""
        if self._active_turn is None:
            return False
        self._cancelled = True
        return True

    def reset(self) -> None:
        self._next_error_turn = 0

    def set_model(self, model: str) -> None:
        self.model = model

    def set_config(self, patch: dict[str, Any]) -> None:
        self.session_config.update(patch)

    async def aclose(self) -> None:
        return None  # nothing to release: no client, no connection

    # --- replay -------------------------------------------------------------------------

    def _pick(self, user_message: str) -> tuple[Turn, dict[str, Turn] | None]:
        text = user_message.lower()
        route = next((r for r in ROUTES if any(k in text for k in r.keywords)), None)
        if route is None:
            return self._turns["nominal"][0], None
        if route.fixture in PAUSED:
            approve = self._turns[f"{route.fixture}_approve"][0]
            reject = self._turns[f"{route.fixture}_reject"][0]
            pause = [e["type"] for e in approve].index("confirmation_required") + 1
            return approve[:pause], {"approve": approve[pause:], "reject": reject[pause:]}
        turns = self._turns[route.fixture]
        if route.turn is not None:
            return turns[route.turn], None
        turn = turns[self._next_error_turn % len(turns)]
        self._next_error_turn += 1
        return turn, None

    async def _replay(
        self,
        prefix: Sequence[Event],
        branches: dict[str, Turn] | None,
        user_message: str | None,
    ) -> AsyncIterator[Event]:
        self._cancelled = False
        turn = object()
        self._active_turn = turn
        try:
            async for event in self._play(prefix, branches, user_message):
                if event["type"] in ("message_done", "error"):
                    self._end(turn)  # the judge may follow, but the turn can no longer be cancelled
                yield event
        finally:
            self._end(turn)

    def _end(self, turn: object) -> None:
        # Only the turn itself clears the flag: the judge of the previous turn may finish
        # while a newer turn runs, and must not make that newer turn uncancellable.
        if self._active_turn is turn:
            self._active_turn = None

    async def _play(
        self,
        prefix: Sequence[Event],
        branches: dict[str, Turn] | None,
        user_message: str | None,
    ) -> AsyncIterator[Event]:
        started = time.monotonic()
        turn_id = _new_id()
        ids: dict[str, str] = {}
        tokens: list[str] = []
        decision: Decision | None = None
        last_call: Event | None = None  # the tool behind the next memory_updated
        queue = list(prefix)
        index = 0
        while index < len(queue):
            template = queue[index]
            index += 1
            kind = template["type"]
            if self._cancelled and kind in INTERRUPTIBLE:
                yield self._event(
                    "message_done",
                    turn_id,
                    content="".join(tokens),
                    interrupted=True,
                    usage=None,
                    duration_ms=_elapsed_ms(started),
                )
                return
            await asyncio.sleep(self._pacing.before(kind))
            event = self._instantiate(template, turn_id, ids, user_message, decision)
            if kind == "token":
                tokens.append(event["delta"])
            elif kind == "message_done":
                event["duration_ms"] = _elapsed_ms(started)
            elif kind == "tool_call":
                last_call = event
            elif kind == "memory_updated":
                self._remember(last_call, origin_ui=user_message is None)
            yield event
            if kind == "confirmation_required" and branches is not None:
                decision = await self._confirm(_pending_action(event))
                queue[index:] = branches["approve" if decision.approved else "reject"]

    async def _confirm(self, action: PendingAction) -> Decision:
        """Same pause as Engine._confirm: the broker's answer, or "timeout" after the delay."""
        try:
            return await asyncio.wait_for(self._broker.request(action), action.expires_in_s)
        except asyncio.TimeoutError:  # distinct from the builtin TimeoutError in 3.10
            return Decision("timeout", "timeout")

    def _instantiate(
        self,
        template: Event,
        turn_id: str,
        ids: dict[str, str],
        user_message: str | None,
        decision: Decision | None,
    ) -> Event:
        event = copy.deepcopy(template)
        event.update(session_id=self.session_id, turn_id=turn_id, ts=time.time())
        for key in ("call_id", "action_id"):
            if key in event:
                event[key] = ids.setdefault(event[key], _new_id())
        kind = event["type"]
        if kind == "turn_started" and event["origin"] == "user":
            event["user_message"] = user_message
        elif kind == "confirmation_required":
            event["expires_in_s"] = self._confirmation_timeout_s
        elif kind == "confirmation_resolved" and decision is not None:
            event.update(
                decision=decision.decision, reason=decision.reason, comment=decision.comment
            )
        elif kind == "tool_result" and decision is not None and not event["ok"]:
            message = REJECTED_MESSAGES.get(decision.reason)
            if message is not None:
                event["error"] = {"code": "ACTION_REJECTED", "message": message}
        return event

    def _remember(self, call: Event | None, *, origin_ui: bool) -> None:
        """Apply the replayed write to the mock memory, so that GET /api/memory shows it."""
        if self._memory is None or call is None:
            return
        args = call["arguments"]
        ctx = AuditContext(actor="user" if origin_ui else "agent", session_id=self.session_id)
        try:
            if call["tool"] == "glossary_define":
                self._memory.define_term(
                    args["term"], args["definition"], args.get("sql_hint"), ctx
                )
            elif call["tool"] == "memory_update":
                self._memory.update(args["kind"], args["ref"], args["patch"], ctx)
            elif call["tool"] == "memory_delete":
                self._memory.delete(args["kind"], args["ref"], ctx)
        except (MemoryStoreError, KeyError):
            logger.debug("Mock memory left unchanged by %s", call["tool"], exc_info=True)

    def _event(self, kind: str, turn_id: str, **fields: Any) -> Event:
        return {
            "type": kind,
            "session_id": self.session_id,
            "turn_id": turn_id,
            "ts": time.time(),
            **fields,
        }


def _pending_action(event: Event) -> PendingAction:
    return PendingAction(
        action_id=event["action_id"],
        call_id=event["call_id"],
        tool=event["tool"],
        sql=event["sql"],
        impact=event["impact"],
        expires_in_s=event["expires_in_s"],
    )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _ui_action_turn(tool: str, arguments: dict[str, Any]) -> tuple[Turn, dict[str, Turn] | None]:
    """Synthetic turn for `ui_action` (websocket.md §4.2, example §8.3)."""

    # Positional-only: memory_updated has its own `kind` field
    def event(event_type: str, /, **fields: Any) -> Event:
        return {"type": event_type, "session_id": "", "turn_id": "", "ts": 0.0, **fields}

    def result(ok: bool, summary: str, data: Event | None, error: Event | None) -> Event:
        return event(
            "tool_result",
            call_id="k-ui",
            ok=ok,
            summary=summary,
            data=data,
            error=error,
            duration_ms=0 if not ok else 10,
        )

    done = event("message_done", content="", interrupted=False, usage=None, duration_ms=0)
    start = [
        event("turn_started", origin="ui", user_message=None),
        event("tool_call", call_id="k-ui", server=None, tool=tool, arguments=arguments),
    ]
    kind, ref = arguments.get("kind"), arguments.get("ref")
    if kind not in MEMORY_TABLES or not isinstance(ref, str) or not ref:
        error = {"code": "INVALID_ARGUMENTS", "message": "Arguments attendus : kind et ref."}
        return [*start, result(False, "Arguments invalides", None, error), done], None

    if tool == "memory_update":
        summary = f"Entrée {ref} modifiée"
        data = {"op": "update", "kind": kind, "ref": ref, "summary": summary}
        updated = event("memory_updated", op="update", kind=kind, ref=ref, summary=summary)
        return [*start, result(True, summary, data, None), updated, done], None

    summary = f"Entrée {ref} supprimée"
    impact = {
        "operation": "DELETE",
        "rows_affected": 1,
        "per_table": {MEMORY_TABLES[kind]: -1},
        "sample": [{"kind": kind, "ref": ref, "label": f"{kind} {ref}"}],
        "blocked": [],
    }
    pause = event(
        "confirmation_required",
        action_id="a-ui",
        call_id="k-ui",
        tool=tool,
        sql=None,
        impact=impact,
        expires_in_s=0,
    )
    resolved = event(
        "confirmation_resolved", action_id="a-ui", decision="", reason="", comment=None
    )
    rejected = {"code": "ACTION_REJECTED", "message": "Suppression refusée par l'utilisateur."}
    branches = {
        "approve": [
            resolved,
            result(
                True, summary, {"op": "delete", "kind": kind, "ref": ref, "summary": summary}, None
            ),
            event("memory_updated", op="delete", kind=kind, ref=ref, summary=summary),
            done,
        ],
        "reject": [resolved, result(False, "Action refusée", None, rejected), done],
    }
    return [*start, pause], branches


class MockBackend:
    """WebBackend of `sensai web --mock`: no Ollama, no MCP server, no database."""

    def __init__(
        self, config: Config, *, fixtures_dir: Path = FIXTURES_DIR, pacing: Pacing | None = None
    ) -> None:
        self._config = config
        self._pacing = pacing or Pacing()
        # Loaded once and shared read-only by every session: a missing fixture fails at startup
        self._turns = {name: load_turns(fixtures_dir / f"{name}.jsonl") for name in FIXTURES}
        # A real store, in RAM and shared by the sessions, so that the memory panel has real
        # data. Opened on first use: SQLite wants the thread of the server's event loop.
        self._memory: MemoryStore | None = None

    async def start(self) -> None:
        return None  # no server to start

    async def stop(self) -> None:
        if self._memory is not None:
            self._memory.close()
            self._memory = None

    async def memory_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return self._store().dump()

    def _store(self) -> MemoryStore:
        if self._memory is None:
            self._memory = mock_memory()
        return self._memory

    async def list_models(self) -> list[ModelInfo]:
        models = [ModelInfo(**model) for model in MOCK_MODELS]
        if all(model["name"] != self._config.ollama.model for model in models):
            models.insert(
                0, ModelInfo(name=self._config.ollama.model, size_bytes=0, supports_tools=True)
            )
        return models

    async def ollama_available(self) -> bool:
        return True  # simulated

    def personas(self) -> tuple[str | None, list[PersonaTools]]:
        return DEFAULT_PERSONA, [
            PersonaTools(
                **persona.info(),
                tools=[tool["name"] for tool in MOCK_TOOLS if persona.allows(tool["name"])],
            )
            for persona in MOCK_PERSONAS
        ]

    def new_engine(
        self,
        session_id: str,
        broker: WebConfirmationBroker,
        questions: WebQuestionBroker | None = None,  # the scripted turns ask nothing yet
    ) -> MockEngine:
        return MockEngine(
            session_id, self._config, broker, self._turns, self._pacing, memory=self._store()
        )


def _persona(persona_id: str) -> MockPersona:
    return next(persona for persona in MOCK_PERSONAS if persona.id == persona_id)


def mock_memory() -> MemoryStore:
    """What the agent already knows in demo mode. The glossary starts empty: act 2 fills it."""
    store = MemoryStore.open(":memory:")
    lemaire = store.upsert_entity(
        "client", "Brasserie Lemaire", aliases=["Lemaire"], external_ref="clients:81"
    )
    store.add_fact("Compte stratégique, suivi par Julien Marchand", "note", entity=lemaire.ref)
    store.add_fact("Claire veut les montants en k€", "preference", source="user")
    return store
