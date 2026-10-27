"""What the Engine and the tools agree on (docs/contracts/tools.md §3).

The Engine only talks to a ToolRegistry. Behind it, MCP tools (wrapped by the registry,
MCP-04) and native tools (memory, MEM-03) look the same: a ToolInfo to describe them, a
ToolResult for every call. A tool failure is a ToolResult with ok=False, never an
exception: the Engine sends it back to the model so that it can correct itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sensai.core.events import MemoryKind, ToolError
from sensai.mcp.client import ToolAnnotations

__all__ = [
    "TOOL_NAME",
    "MemoryChange",
    "ServerStatus",
    "Tool",
    "ToolAnnotations",
    "ToolError",
    "ToolInfo",
    "ToolRegistry",
    "ToolResult",
]

# Names as seen by the model: "<server>__<tool>" for MCP tools, bare for native ones (P-17)
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolInfo:
    """What the registry knows about a tool: read by the Engine, the policy and the UI."""

    name: str  # as seen by the model: "sql_toolbox__run_select", "memory_delete"
    server: str | None  # "sql_toolbox", "time"... None for a native tool
    original_name: str  # name on its server: "run_select" (same as name if native)
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations
    trusted: bool  # native tool or trusted server (tools.md §5)


@dataclass(frozen=True)
class MemoryChange:
    """An effective memory write: the Engine turns it into a memory_updated event."""

    op: Literal["create", "update", "delete"]
    kind: MemoryKind
    ref: str
    summary: str


@dataclass(frozen=True)
class ToolResult:
    """Result of a tool call. ok is True if and only if error is None."""

    ok: bool
    text: str  # what the model reads; never sent to the UI
    summary: str  # short French text for the timeline: "5 lignes"
    data: dict[str, Any] | None = None  # structured result (MCP structuredContent)
    error: ToolError | None = None
    duration_ms: int = 0
    memory_change: MemoryChange | None = None

    def __post_init__(self) -> None:
        if self.ok != (self.error is None):
            raise ValueError("a ToolResult is ok if and only if it has no error")

    @classmethod
    def failure(cls, code: str, message: str, *, duration_ms: int = 0) -> ToolResult:
        """A failed call: the message is both what the model reads and what the UI shows."""
        return cls(
            ok=False,
            text=message,
            summary=message,
            error=ToolError(code=code, message=message),
            duration_ms=duration_ms,
        )


@dataclass(frozen=True)
class ServerStatus:
    name: str
    status: Literal["up", "down"]
    error: str | None = None  # French message shown as is, None when up


class Tool(Protocol):
    """A native tool (memory, MEM-03). MCP tools are wrapped by the registry instead."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations

    async def call(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry(Protocol):
    """The only entry point of the Engine to the tools (tools.md §3.6)."""

    def ollama_tools(self, allowed: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
        """Available tools in Ollama's format, inputSchema without its $schema key; only
        those for which `allowed(name)` is true when given (tools of the profile, A5)."""
        ...

    def get(self, name: str) -> ToolInfo | None: ...

    def tools(self) -> list[ToolInfo]: ...

    def servers(self) -> list[ServerStatus]: ...

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]:
        """Validation errors against the inputSchema (3 at most), [] when valid."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Route the call to its server. A tool failure is a failed ToolResult, never raised."""
        ...
