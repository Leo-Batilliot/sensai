"""Test doubles shared by the unit tests: no network, no Ollama, no MCP server."""

import asyncio
import contextlib
import json
import signal
import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from sensai.core.errors import ModelNotFound
from sensai.llm.ollama_client import Chunk, Message, ModelDetails, ToolCall, Usage
from sensai.tools.base import ServerStatus, ToolAnnotations, ToolInfo, ToolResult

MODEL = "qwen3:8b"
OTHER_MODEL = "llama3.1:8b"
FIXED_NOW = "2026-10-14T09:30:00+02:00"


# --- Fake Ollama -----------------------------------------------------------------------


@dataclass(frozen=True)
class Pause:
    """Stream item: the user presses Ctrl+C (ctrl_c), and/or the stream waits until
    cancelled (hang). Without hang, the stream goes on: the engine sees its cancel flag
    on the next chunk."""

    ctrl_c: bool = False
    hang: bool = True


HANG = Pause()
CTRL_C = Pause(ctrl_c=True, hang=False)

StreamItem = Chunk | Exception | Pause


def _final(prompt_tokens: int = 12, completion_tokens: int = 3) -> Chunk:
    usage = Usage(prompt_tokens, completion_tokens, total_duration_ms=50.0)
    return Chunk(done=True, done_reason="stop", usage=usage)


def answer(*parts: str, prompt_tokens: int = 12, completion_tokens: int = 3) -> list[StreamItem]:
    """A complete reply: one chunk per part, then the final chunk with the token counts."""
    return [*(Chunk(content=part) for part in parts), _final(prompt_tokens, completion_tokens)]


def tool_request(name: str, **arguments: Any) -> list[StreamItem]:
    """A reply in which the model asks for one tool instead of answering."""
    return tool_requests((name, arguments))


def tool_requests(*calls: tuple[str, dict[str, Any]]) -> list[StreamItem]:
    """A reply in which the model asks for several tools at once."""
    requested = tuple(ToolCall(name, arguments) for name, arguments in calls)
    return [Chunk(tool_calls=requested), _final()]


class FakeClient:
    """Stands in for OllamaClient: each chat_stream call plays the next scripted reply."""

    def __init__(
        self,
        *replies: list[StreamItem],
        models: tuple[str, ...] = (MODEL, OTHER_MODEL),
        capabilities: tuple[str, ...] = ("completion", "tools"),
        show_error: Exception | None = None,
    ) -> None:
        self.replies = list(replies)
        self.models = models
        self.capabilities = capabilities
        self.show_error = show_error
        # Copies of what each call received: the engine keeps extending its message list
        self.calls: list[tuple[str, list[Message], dict[str, Any] | None]] = []
        self.tools_sent: list[list[dict[str, Any]] | None] = []
        self.open_streams = 0
        self.closed = False

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Chunk, None]:
        self.calls.append((model, list(messages), options))
        self.tools_sent.append(tools)
        reply = self.replies.pop(0)
        self.open_streams += 1
        try:
            for item in reply:
                await asyncio.sleep(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, Pause):
                    if item.ctrl_c:
                        signal.raise_signal(signal.SIGINT)
                        await asyncio.sleep(0)  # lets the SIGINT callback run
                    if item.hang:
                        await asyncio.Event().wait()
                    continue
                yield item
        finally:
            self.open_streams -= 1

    async def show(self, model: str) -> ModelDetails:
        if self.show_error is not None:
            raise self.show_error
        if model not in self.models:
            raise ModelNotFound(model)
        return ModelDetails(name=model, capabilities=self.capabilities, context_length=8192)

    async def list_models(self) -> list[str]:
        if self.show_error is not None:
            raise self.show_error
        return list(self.models)

    async def aclose(self) -> None:
        self.closed = True


# --- Fake tools ------------------------------------------------------------------------

Handler = Callable[[dict[str, Any]], Awaitable[ToolResult]]


@dataclass(frozen=True)
class FakeTool:
    info: ToolInfo
    handler: Handler


def fake_tool(
    name: str,
    handler: Handler,
    *,
    server: str | None = "fake",
    schema: dict[str, Any] | None = None,
    read_only: bool | None = True,
) -> FakeTool:
    """Any tool, for tests that need a specific behaviour (failure, exception...)."""
    original = name.split("__", 1)[1] if server and "__" in name else name
    info = ToolInfo(
        name=name,
        server=server,
        original_name=original,
        description=f"Outil de test {name}",
        input_schema=schema or {"type": "object"},
        annotations=ToolAnnotations(read_only=read_only),
        trusted=server is None,
    )
    return FakeTool(info, handler)


def time_tool(now: str = FIXED_NOW) -> FakeTool:
    """Like time__get_current_time of mcp-server-time, always answering `now`."""

    async def handler(arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, text=now, summary=now, data={"datetime": now}, duration_ms=1)

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",  # stripped for Ollama
        "type": "object",
        "properties": {"timezone": {"type": "string"}},
        "required": ["timezone"],
    }
    tool = fake_tool("time__get_current_time", handler, server="time", schema=schema)
    return FakeTool(replace(tool.info, description="Donne la date et l'heure actuelles"), handler)


def select_tool(row_limit: int = 200, db_path: str | Path | None = None) -> FakeTool:
    """Like sql_toolbox__run_select: on the database file at db_path, opened read-only,
    or by default on an in-memory database of 3 clients.

    The connection is opened for each call, so that none stays open.
    """

    def connect() -> sqlite3.Connection:
        if db_path is not None:
            return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        db = sqlite3.connect(":memory:")
        db.executescript(
            "CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, marge_eur REAL);"
            "INSERT INTO clients VALUES (1, 'Brasserie Lemaire', 48712.4),"
            " (2, 'Imprimerie Vasseur', 41208.15), (3, 'Cabinet Roussel', 37950.0);"
        )
        return db

    async def handler(arguments: dict[str, Any]) -> ToolResult:
        with contextlib.closing(connect()) as db:
            try:
                cursor = db.execute(arguments["sql"])
                rows = [list(row) for row in cursor.fetchmany(row_limit + 1)]
            except sqlite3.Error as exc:
                return ToolResult.failure("SQL_ERROR", f"Erreur SQLite : {exc}")
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        data = {
            "columns": [column[0] for column in cursor.description or ()],
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
        summary = f"{len(rows)} lignes" + (" · tronqué" if truncated else "")
        text = json.dumps(data, ensure_ascii=False)
        return ToolResult(ok=True, text=text, summary=summary, data=data, duration_ms=3)

    schema = {
        "type": "object",
        "properties": {"sql": {"type": "string"}, "purpose": {"type": "string"}},
        "required": ["sql"],
        "additionalProperties": False,
    }
    tool = fake_tool("sql_toolbox__run_select", handler, server="sql_toolbox", schema=schema)
    return FakeTool(replace(tool.info, trusted=True), handler)  # sql_toolbox is trusted


class FakeToolRegistry:
    """Implements the ToolRegistry protocol (tools.md §3.6) with in-process fake tools."""

    def __init__(self, *tools: FakeTool) -> None:
        chosen = tools or (time_tool(), select_tool())
        self._tools = {tool.info.name: tool for tool in chosen}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.info.name,
                    "description": tool.info.description,
                    "parameters": {
                        key: value
                        for key, value in tool.info.input_schema.items()
                        if key != "$schema"
                    },
                },
            }
            for tool in self._tools.values()
        ]

    def get(self, name: str) -> ToolInfo | None:
        tool = self._tools.get(name)
        return tool.info if tool else None

    def tools(self) -> list[ToolInfo]:
        return [tool.info for tool in self._tools.values()]

    def servers(self) -> list[ServerStatus]:
        names = sorted({tool.info.server for tool in self._tools.values() if tool.info.server})
        return [ServerStatus(name=name, status="up") for name in names]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]:
        validator = Draft202012Validator(self._tools[name].info.input_schema)
        return [error.message for error in validator.iter_errors(arguments)][:3]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((name, arguments))
        return await self._tools[name].handler(arguments)
