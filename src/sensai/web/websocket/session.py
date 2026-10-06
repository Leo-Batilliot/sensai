"""Web sessions: one conversation each, kept in memory between connections.

Concurrency per connection:
- the receive loop (endpoint.py) reads client messages and dispatches them; it never runs a turn;
- each turn runs in its own task, which sends the engine events as they come;
- `confirm` resolves the broker from the receive loop, while the turn task waits on it.

A turn is "busy" from `turn_started` to its end (`message_done` or `error`). The judge
events that follow do not keep the session busy (websocket.md §6.1): the turn task keeps
consuming the generator while a new turn may already run.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import WebSocket

from sensai.web.engines.backend import WebBackend, WebEngine
from sensai.web.websocket.confirm import WebConfirmationBroker

logger = logging.getLogger(__name__)

SESSION_TTL_S = 3600  # kept in memory 1 h after the last disconnection (§2.4)
END_OF_TURN = frozenset({"message_done", "error"})
NOT_IN_HISTORY = frozenset({"token", "thinking"})  # message_done.content has the full text
CLOSE_REPLACED = 4409


def to_payload(event: Any) -> dict[str, Any]:
    """Engine events are dicts (MockEngine) or objects with `to_dict()` (events.md §6)."""
    return event.to_dict() if hasattr(event, "to_dict") else dict(event)


class Session:
    """One conversation: an engine, its broker, its history, and at most one socket."""

    def __init__(self, session_id: str, engine: WebEngine, broker: WebConfirmationBroker) -> None:
        self.id = session_id
        self.engine = engine
        self.broker = broker
        self.websocket: WebSocket | None = None
        self.disconnected_at: float | None = None
        self.history: list[dict[str, Any]] = []
        self.turn_active = False
        self.turn_done = asyncio.Event()
        self.turn_done.set()
        self._live_turns: set[str] = set()  # turns started since the last reset
        self._current_turn: str | None = None
        self._send_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    # --- connection ---------------------------------------------------------------------

    async def attach(
        self, websocket: WebSocket, ready: Callable[[], Awaitable[dict[str, Any]]]
    ) -> None:
        """Bind the socket and send `session_ready` first, before any event of a running turn."""
        async with self._send_lock:
            self.websocket = websocket
            self.disconnected_at = None
            await websocket.send_json(await ready())

    def detach(self, websocket: WebSocket) -> None:
        if self.websocket is not websocket:
            return  # already replaced by a newer connection
        self.websocket = None
        self.disconnected_at = time.monotonic()
        self.interrupt("disconnected")  # a destructive action is never left pending (§2.4)

    async def send(self, payload: dict[str, Any]) -> None:
        websocket = self.websocket
        if websocket is None:
            return
        async with self._send_lock:
            try:
                await websocket.send_json(payload)
            except Exception:  # closed meanwhile: the history keeps the event for a reconnection
                logger.debug("Événement non envoyé, connexion fermée (session %s)", self.id)

    # --- turns --------------------------------------------------------------------------

    def start_turn(self, events: AsyncIterator[Any]) -> None:
        # Busy before the task starts, so that a second message right after gets BUSY
        self.turn_active = True
        self.turn_done.clear()
        task = asyncio.create_task(self._consume(events))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def interrupt(self, reason: str) -> None:
        """`cancel`, `reset` or disconnection: refuse pending confirmations, stop the turn."""
        if self.turn_active:
            self.broker.reject_all(reason)
            self.engine.cancel()

    def clear_history(self) -> None:
        self.history.clear()
        self._live_turns.clear()

    async def _consume(self, events: AsyncIterator[Any]) -> None:
        turn_id: str | None = None
        try:
            async for event in events:
                payload = to_payload(event)
                if payload["type"] == "turn_started":
                    turn_id = payload["turn_id"]
                self._record(payload)
                await self.send(payload)
                if payload["type"] in END_OF_TURN and payload.get("turn_id") == turn_id:
                    self._end_turn(turn_id)  # the judge events may follow, the session is free
        except Exception:
            logger.exception("Erreur interne pendant un tour (session %s)", self.id)
            if self.turn_active and turn_id == self._current_turn:
                error = {
                    "type": "error",
                    "session_id": self.id,
                    "turn_id": turn_id,
                    "ts": time.time(),
                    "code": "INTERNAL",
                    "message": "Erreur interne du serveur : rechargez la page.",
                    "recoverable": False,
                }
                self._record(error)
                await self.send(error)
        finally:
            self._end_turn(turn_id)

    def _record(self, payload: dict[str, Any]) -> None:
        if payload["type"] == "turn_started":
            self._current_turn = payload["turn_id"]
            self._live_turns.add(payload["turn_id"])
        if payload["type"] in NOT_IN_HISTORY:
            return
        if payload.get("turn_id") in self._live_turns:  # late events of a reset turn are dropped
            self.history.append(payload)

    def _end_turn(self, turn_id: str | None) -> None:
        if turn_id is not None and turn_id != self._current_turn:
            return  # judge of an older turn finishing while a newer turn runs
        self.turn_active = False
        self.turn_done.set()


class SessionManager:
    def __init__(self, backend: WebBackend, *, ttl_s: float = SESSION_TTL_S) -> None:
        self.backend = backend
        self._ttl_s = ttl_s
        self._sessions: dict[str, Session] = {}

    async def open(self, session_id: str) -> tuple[Session, bool]:
        """Return the session and whether it already existed. Closes a previous socket (4409)."""
        self._purge()
        session = self._sessions.get(session_id)
        if session is None:
            broker = WebConfirmationBroker()
            session = Session(session_id, self.backend.new_engine(session_id, broker), broker)
            self._sessions[session_id] = session
            return session, False
        previous = session.websocket
        if previous is not None:
            session.websocket = None
            try:
                await previous.close(code=CLOSE_REPLACED)
            except Exception:
                logger.debug("Ancienne connexion déjà fermée (session %s)", session_id)
        return session, True

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.websocket is None
            and session.disconnected_at is not None
            and now - session.disconnected_at > self._ttl_s
            and not session.turn_active
        ]
        for session_id in expired:
            del self._sessions[session_id]
