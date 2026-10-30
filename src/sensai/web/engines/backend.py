"""What the web layer needs from the engine side.

These protocols describe what the WebSocket and REST code use, so that the MockEngine
(WEB-04, `sensai web --mock`) and the real Engine (WEB-08, `sensai web`) are
interchangeable. Events are plain dicts, or objects with a `to_dict()` method
(events.md §6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, TypedDict

from sensai.documents.store import DocumentStore
from sensai.web.websocket.ask import WebQuestionBroker
from sensai.web.websocket.confirm import WebConfirmationBroker


class ModelInfo(TypedDict):
    name: str
    size_bytes: int
    supports_tools: bool


class Persona(TypedDict):
    """An agent profile as the selector shows it (websocket.md §5.2, A5)."""

    id: str
    name: str
    role: str


class PersonaTools(Persona):
    """A profile and the names of the tools it allows, for GET /api/personas (§9)."""

    tools: list[str]


class WebEngine(Protocol):
    """One engine per web session."""

    model: str
    session_config: dict[str, Any]
    persona: Persona | None  # None for a server without profiles

    def describe(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """MCP servers and the tools of the active profile, as sent in `session_ready` (§5.2)."""
        ...

    def set_persona(self, persona_id: str) -> bool:
        """Profile used from the next turn on. False for an unknown one, which changes nothing."""
        ...

    def run_turn(self, user_message: str) -> AsyncIterator[Any]: ...

    def run_ui_action(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[Any]: ...

    def cancel(self) -> bool:
        """Flag checked by the running turn, which then ends with `message_done {interrupted}`.

        False when no turn is running (the request is ignored), as Engine.cancel().
        """
        ...

    def reset(self) -> None: ...

    def set_model(self, model: str) -> None: ...

    def set_config(self, patch: dict[str, Any]) -> None: ...

    async def aclose(self) -> None:
        """Release what the session owns (Ollama client, connections) when it expires."""
        ...


class WebBackend(Protocol):
    """Shared by every session: creates the engines and answers the REST routes."""

    async def start(self) -> None:
        """Start the shared services once, before the first connection (FastAPI lifespan)."""
        ...

    async def stop(self) -> None: ...

    async def list_models(self) -> list[ModelInfo]: ...

    async def ollama_available(self) -> bool: ...

    async def memory_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Every memory entry, for GET /api/memory (memory.md §5)."""
        ...

    def personas(self) -> tuple[str | None, list[PersonaTools]]:
        """The profile a new session starts with, and every profile (GET /api/personas)."""
        ...

    def documents(self) -> DocumentStore:
        """The committee note (M4): GET /api/documents/current, shares, Note panel."""
        ...

    def new_engine(
        self,
        session_id: str,
        broker: WebConfirmationBroker,
        questions: WebQuestionBroker | None = None,
    ) -> WebEngine: ...

    def scheduled_engine(self, session_id: str) -> WebEngine:
        """The engine of a scheduled run (X5, V-4): nobody to confirm or answer, and no tool
        that writes. Closed by the scheduler (aclose) after the run."""
        ...
