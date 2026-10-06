"""What the web layer needs from the engine side.

The real `Engine` (CORE-05) does not exist yet: these protocols describe what the
WebSocket and REST code use, so that the MockEngine (WEB-04) and the real engine
(WEB-08) are interchangeable. Events are plain dicts, or objects with a `to_dict()`
method (events.md §6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, TypedDict

from sensai.web.websocket.confirm import WebConfirmationBroker


class ModelInfo(TypedDict):
    name: str
    size_bytes: int
    supports_tools: bool


class WebEngine(Protocol):
    """One engine per web session."""

    model: str
    session_config: dict[str, Any]

    def describe(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """MCP servers and tools, as sent in `session_ready` (websocket.md §5.2)."""
        ...

    def run_turn(self, user_message: str) -> AsyncIterator[Any]: ...

    def run_ui_action(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[Any]: ...

    def cancel(self) -> None:
        """Flag checked by the running turn, which then ends with `message_done {interrupted}`."""
        ...

    def reset(self) -> None: ...

    def set_model(self, model: str) -> None: ...

    def set_config(self, patch: dict[str, Any]) -> None: ...


class WebBackend(Protocol):
    """Shared by every session: creates the engines and answers the REST routes."""

    async def list_models(self) -> list[ModelInfo]: ...

    async def ollama_available(self) -> bool: ...

    def new_engine(self, session_id: str, broker: WebConfirmationBroker) -> WebEngine: ...
