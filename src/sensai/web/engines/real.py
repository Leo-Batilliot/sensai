"""WebBackend of `sensai web`: the real Engine behind the WebSocket (WEB-08).

Started once with the application (FastAPI lifespan) and shared by every session:
the MCP servers (one McpManager, never one per session), the memory store and the
system prompt, whose schema is read once. Each session gets its own Engine: its Ollama
client, its history, its registry (the memory tools audit the session id) and the
WebConfirmationBroker of its socket.

The Engine does not cover the whole web protocol: RealEngine adds the servers and tools
of session_ready, the session settings of set_config, and run_ui_action until the
Engine has its own (point P-6, P1).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from sensai.core import events
from sensai.core.config import Config
from sensai.core.engine import ChatClient, Engine
from sensai.core.errors import SensaiError
from sensai.core.prompts import system_prompt_template
from sensai.eval.online import OnlineJudge
from sensai.llm.ollama_client import OllamaClient
from sensai.mcp.manager import McpManager
from sensai.memory.context import MemoryContext
from sensai.memory.store import MemoryStore
from sensai.tools.memory_tools import memory_tools
from sensai.tools.policy import SqlClassifier, evaluate
from sensai.tools.registry import Registry
from sensai.web.engines.backend import ModelInfo
from sensai.web.websocket.confirm import WebConfirmationBroker
from sensai.web.websocket.settings import SESSION_SETTINGS, session_settings

MEMORY_DELETE = "memory_delete"
UI_ACTION_UNAVAILABLE = (
    "Les modifications depuis l'interface ne sont pas encore branchées sur le moteur réel."
)

ClientFactory = Callable[[Config], ChatClient]


def ollama_client(config: Config) -> OllamaClient:
    return OllamaClient(
        config.ollama.host, timeout_s=config.ollama.timeout_s, keep_alive=config.ollama.keep_alive
    )


class RealEngine:
    """WebEngine over one Engine. The Engine stays the only one to decide anything."""

    def __init__(self, engine: Engine, registry: Registry, client: ChatClient) -> None:
        self.engine = engine
        self.registry = registry
        self._client = client
        self.session_config = session_settings(engine.config)

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

    def run_turn(self, user_message: str) -> AsyncIterator[Any]:
        return self.engine.run_turn(user_message)

    async def run_ui_action(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[Any]:
        run = getattr(self.engine, "run_ui_action", None)
        if run is not None:
            async for event in run(tool, arguments):
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
        self._config = config
        self._manager = manager if manager is not None else McpManager.from_config(config)
        self._client_factory = client_factory
        self._sql_classifier = sql_classifier  # tests only: None = the business database
        self._client = client_factory(config)  # REST routes: models, health
        self._supports_tools: dict[str, bool] = {}
        self._store: MemoryStore | None = None
        self._prompt: Callable[[str], str] | None = None

    async def start(self) -> None:
        await self._manager.start_all()  # a server that cannot start is logged and marked down
        self._store = MemoryStore.open(self._config.database.memory_path)
        tool_names = [info.name for info in self._registry(None).tools()]
        self._prompt = system_prompt_template(self._config.database.business_path, tool_names)

    async def stop(self) -> None:
        await self._manager.stop_all()
        if self._store is not None:
            self._store.close()
            self._store = None
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

    async def ollama_available(self) -> bool:
        try:
            await self._client.list_models()
        except SensaiError:
            return False
        return True

    def new_engine(self, session_id: str, broker: WebConfirmationBroker) -> RealEngine:
        if self._store is None or self._prompt is None:
            raise RuntimeError("RealBackend.start() must run before the first session")
        client = self._client_factory(self._config)
        registry = self._registry(session_id)
        engine = Engine(
            self._config,
            client,
            registry=registry,
            system_prompt=self._prompt,
            broker=broker,
            session_id=session_id,
            memory=MemoryContext(self._store),
            judge=OnlineJudge.from_config(self._config, client),
            sql_classifier=self._sql_classifier,
        )
        return RealEngine(engine, registry, client)

    def _registry(self, session_id: str | None) -> Registry:
        store = self._store
        assert store is not None
        return Registry(
            self._manager,
            memory_tools(store, session_id),
            previewers={MEMORY_DELETE: lambda args: store.delete_impact(args["kind"], args["ref"])},
        )
