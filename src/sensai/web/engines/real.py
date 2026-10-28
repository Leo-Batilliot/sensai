"""WebBackend of `sensai web`: the real Engine behind the WebSocket (WEB-08).

Started once with the application (FastAPI lifespan) and shared by every session: the
Services of the application (core/app_factory.py, CORE-10), as for the CLI. Each session
gets its own Engine from them, with the WebConfirmationBroker and WebQuestionBroker of
its socket.

The Engine does not cover the whole web protocol: RealEngine adds the servers and tools
of session_ready, the session settings of set_config, and run_ui_action until the
Engine has its own (point P-6, P1): the Note panel's actions run (engines/note.py), the
memory panel's not yet. Profiles (A5) wait for PERS-01: none for now.

A scheduled run (X5) gets an Engine with nobody behind its brokers and a ReadOnlyRegistry:
the tools that write are not even offered to the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from sensai.core import events
from sensai.core.app_factory import ClientFactory, Services, ollama_client
from sensai.core.config import Config
from sensai.core.engine import ChatClient, Engine
from sensai.core.errors import SensaiError
from sensai.documents.store import DocumentStore
from sensai.eval.online import OnlineJudge
from sensai.mcp.manager import McpManager
from sensai.tools.base import ServerStatus, ToolInfo, ToolRegistry, ToolResult
from sensai.tools.policy import SqlClassifier, evaluate
from sensai.tools.registry import Registry
from sensai.web.engines.backend import ModelInfo, Persona, PersonaTools
from sensai.web.engines.note import NOTE_ACTIONS, note_action, note_registry
from sensai.web.websocket.ask import WebQuestionBroker
from sensai.web.websocket.confirm import WebConfirmationBroker
from sensai.web.websocket.settings import SESSION_SETTINGS, session_settings

UI_ACTION_UNAVAILABLE = (
    "Les modifications depuis l'interface ne sont pas encore branchées sur le moteur réel."
)


class RealEngine:
    """WebEngine over one Engine. The Engine stays the only one to decide anything."""

    def __init__(self, engine: Engine, registry: ToolRegistry, client: ChatClient) -> None:
        self.engine = engine
        self.registry = registry
        self._client = client
        self.session_config = session_settings(engine.config)
        self.persona: Persona | None = None  # no profiles before PERS-01 (P1)

    @property
    def model(self) -> str:
        return self.engine.model

    def describe(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        servers = [
            {"name": server.name, "status": server.status, "error": server.error}
            for server in self.registry.servers()
        ]
        hitl = self.engine.config.hitl
        tools = [
            {
                "name": info.name,
                "server": info.server,
                "description": info.description,
                "read_only": info.annotations.read_only,
                "destructive": info.annotations.destructive,
                # The decision without arguments (tools.md §5): run_select shows no padlock
                "requires_confirmation": evaluate(info, {}, hitl).action == "confirm",
            }
            for info in self.registry.tools()
        ]
        return servers, tools

    def set_persona(self, persona_id: str) -> bool:
        return False  # PERS-01: every profile is unknown until the Engine loads them

    def run_turn(self, user_message: str) -> AsyncIterator[Any]:
        return self.engine.run_turn(user_message)

    async def run_ui_action(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[Any]:
        run = getattr(self.engine, "run_ui_action", None)
        if run is not None:
            async for event in run(tool, arguments):
                yield event
            return
        documents = self.engine.documents
        if tool in NOTE_ACTIONS and documents is not None:
            async for event in note_action(
                note_registry(documents, self.engine.session_id),
                tool,
                arguments,
                session_id=self.engine.session_id,
                broker=self.engine.broker,
                hitl=self.engine.config.hitl,
            ):
                yield event
            return
        yield events.Error(
            session_id=self.engine.session_id,
            turn_id=None,
            code="INTERNAL",
            message=UI_ACTION_UNAVAILABLE,
            recoverable=False,
        )

    def cancel(self) -> bool:
        return self.engine.cancel()

    def reset(self) -> None:
        self.engine.reset()

    def set_model(self, model: str) -> None:
        self.engine.set_model(model)
        # Without a judge_model, the judge follows the agent's model (judge.md)
        self._update({"ollama.model": model})

    def set_config(self, patch: dict[str, Any]) -> None:
        self.session_config.update(patch)
        updates = {SESSION_SETTINGS[key]: value for key, value in patch.items()}
        if "ollama.judge_model" in updates:
            updates["ollama.judge_model"] = updates["ollama.judge_model"] or ""  # null = same
        self._update(updates)

    async def aclose(self) -> None:
        await self.engine.aclose()

    def _update(self, updates: dict[str, Any]) -> None:
        """Session copy of the configuration: the Engine reads it at every turn."""
        config = self.engine.config
        for dotted, value in updates.items():
            section, key = dotted.split(".")
            part = getattr(config, section).model_copy(update={key: value})
            config = config.model_copy(update={section: part})
        self.engine.config = config
        if self.engine.judge is not None:
            self.engine.judge = OnlineJudge.from_config(config, self._client)


class ReadOnlyRegistry:
    """The registry of a scheduled run: a tool that may write does not exist for the model.

    Kept: tools declared read-only, and those without a declaration (time server), which
    the HITL policy sends to confirmation if untrusted, refused there by AutoRejectBroker.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def tools(self) -> list[ToolInfo]:
        return [info for info in self._registry.tools() if _reads_only(info)]

    def get(self, name: str) -> ToolInfo | None:
        info = self._registry.get(name)
        return info if info is not None and _reads_only(info) else None

    def servers(self) -> list[ServerStatus]:
        return self._registry.servers()

    def ollama_tools(self) -> list[dict[str, Any]]:
        allowed = {info.name for info in self.tools()}
        return [t for t in self._registry.ollama_tools() if t["function"]["name"] in allowed]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]:
        return self._registry.validate_arguments(name, arguments)

    async def preview(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        preview = getattr(self._registry, "preview", None)
        return None if preview is None else await preview(name, arguments)

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self.get(name) is None:  # the Engine already refuses an unknown tool
            return ToolResult.failure("TOOL_DENIED", "Outil indisponible pour une tâche planifiée.")
        return await self._registry.call(name, arguments)


def _reads_only(info: ToolInfo) -> bool:
    return info.annotations.read_only is not False and info.annotations.destructive is not True


class RealBackend:
    """WebBackend of `sensai web`: Ollama, the MCP servers and the memory of the machine."""

    def __init__(
        self,
        config: Config,
        *,
        manager: McpManager | None = None,
        client_factory: ClientFactory = ollama_client,
        sql_classifier: SqlClassifier | None = None,
    ) -> None:
        self._services = Services(
            config, manager=manager, client_factory=client_factory, sql_classifier=sql_classifier
        )
        self._client = client_factory(config)  # REST routes: models, health
        self._supports_tools: dict[str, bool] = {}

    async def start(self) -> None:
        await self._services.start()

    async def stop(self) -> None:
        await self._services.stop()
        await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        """Installed models; empty when Ollama is down (the turn then says why)."""
        try:
            names = await self._client.list_models()
        except SensaiError:
            return []
        unknown = [name for name in names if name not in self._supports_tools]
        details = await asyncio.gather(
            *(self._client.show(name) for name in unknown), return_exceptions=True
        )
        for name, detail in zip(unknown, details, strict=True):
            if not isinstance(detail, BaseException):
                self._supports_tools[name] = detail.supports_tools
        # /api/tags sizes are not read by OllamaClient: 0 until the selector needs them
        return [
            ModelInfo(name=name, size_bytes=0, supports_tools=self._supports_tools.get(name, False))
            for name in names
        ]

    async def memory_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return self._services.store.dump()

    def personas(self) -> tuple[str | None, list[PersonaTools]]:
        return None, []  # PERS-01

    def documents(self) -> DocumentStore:
        return self._services.documents

    async def ollama_available(self) -> bool:
        try:
            await self._client.list_models()
        except SensaiError:
            return False
        return True

    def new_engine(
        self,
        session_id: str,
        broker: WebConfirmationBroker,
        questions: WebQuestionBroker | None = None,
    ) -> RealEngine:
        engine = self._services.new_engine(broker, session_id, questions)
        assert isinstance(engine.registry, Registry)  # Services always gives one
        return RealEngine(engine, engine.registry, engine.client)

    def scheduled_engine(self, session_id: str) -> RealEngine:
        # No broker: AutoRejectBroker and NoHumanQuestionBroker (Engine defaults)
        engine = self._services.new_engine(None, session_id, None)
        assert isinstance(engine.registry, Registry)
        registry = ReadOnlyRegistry(engine.registry)
        engine.registry = registry
        return RealEngine(engine, registry, engine.client)
