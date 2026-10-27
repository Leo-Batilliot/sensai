"""The real ToolRegistry: MCP tools from the McpManager plus native tools, in one place.

The Engine only knows this interface (docs/contracts/tools.md §3.6). MCP tools are named
"<server>__<tool>" so that names stay stable when a server is added; native tools keep
their bare name. Every call returns a ToolResult: a server that is down, a timeout or a
tool error become a failed result for the model, never an exception.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from sensai.core.errors import McpCommandNotFound, McpProtocolError, McpServerDied, McpTimeout
from sensai.mcp.client import McpCallResult, McpTool
from sensai.mcp.jsonrpc import JsonRpcError
from sensai.mcp.manager import McpManager
from sensai.tools.base import ServerStatus, Tool, ToolInfo, ToolResult

logger = logging.getLogger(__name__)

SEPARATOR = "__"
MAX_NAME_LENGTH = 64
# Servers whose annotations are believed (tools.md §5, point T-1): ours
TRUSTED_SERVERS = frozenset({"sql_toolbox"})
MAX_VALIDATION_ERRORS = 3
SUMMARY_LENGTH = 80
NON_TEXT_BLOCK = "[contenu non textuel ignoré]"
# Tool of an SQL server that previews a write without keeping it (MCP-05)
PREVIEW_TOOL = "preview_write"
IMPACT_KEYS = {"operation", "rows_affected", "per_table", "sample", "blocked"}
# Optional: what a file_write would write (path, size, existing or not, start of the content)
OPTIONAL_IMPACT_KEYS = {"file"}

Previewer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class _Entry:
    info: ToolInfo
    native: Tool | None  # None for an MCP tool


class Registry:
    """Implements the ToolRegistry protocol on top of an McpManager and native tools."""

    def __init__(
        self,
        manager: McpManager | None = None,
        native_tools: Sequence[Tool] = (),
        *,
        trusted_servers: Iterable[str] = TRUSTED_SERVERS,
        previewers: Mapping[str, Previewer] | None = None,
    ) -> None:
        self.manager = manager
        self.native_tools = list(native_tools)
        # Native tool name -> impact of a call, e.g. memory_delete -> MemoryStore.delete_impact
        self.previewers = dict(previewers or {})
        self.trusted_servers = frozenset(trusted_servers)
        self._validators: dict[str, Draft202012Validator] = {}

    # ---- what the Engine, the policy and the UI read -------------------------------------

    def tools(self) -> list[ToolInfo]:
        """Every known tool, those of servers that are down included."""
        return [entry.info for entry in self._entries().values()]

    def get(self, name: str) -> ToolInfo | None:
        entry = self._entries().get(name)
        return entry.info if entry else None

    def servers(self) -> list[ServerStatus]:
        return self.manager.status() if self.manager else []

    def ollama_tools(self) -> list[dict[str, Any]]:
        """Available tools in Ollama's format: the tools of a server that is down are left out."""
        return [
            {
                "type": "function",
                "function": {
                    "name": info.name,
                    "description": info.description,
                    "parameters": _ollama_parameters(info.input_schema),
                },
            }
            for info in self.tools()
            if info.server is None or self._is_up(info.server)
        ]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]:
        """Validation errors against the inputSchema, 3 at most; [] when valid."""
        info = self.get(name)
        if info is None:
            return [f"outil inconnu : {name}"]
        validator = self._validators.get(name)
        if validator is None:
            validator = self._validators[name] = Draft202012Validator(info.input_schema)
        try:
            errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
        except jsonschema.SchemaError:
            # A third-party server declared a broken schema: let its server judge
            logger.warning("Invalid inputSchema for tool %s, arguments not checked", name)
            return []
        return [_error_text(error) for error in errors[:MAX_VALIDATION_ERRORS]]

    async def preview(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Impact of a call before it runs, in the shape of confirmation_required.impact.

        Native tools use their previewer; an SQL tool asks its server's preview_write.
        None when there is no preview, or when it fails: the confirmation is then asked
        without figures. Never raises.
        """
        entry = self._entries().get(name)
        if entry is None:
            return None
        try:
            if entry.native is not None:
                previewer = self.previewers.get(name)
                return _valid_impact(previewer(arguments)) if previewer else None
            server, sql = entry.info.server, arguments.get("sql")
            if self.manager is None or server is None or not isinstance(sql, str):
                return None
            if not any(tool.name == PREVIEW_TOOL for tool in self.manager.tools()[server]):
                return None
            result = await self.manager.call(server, PREVIEW_TOOL, {"sql": sql})
            return None if result.is_error else _valid_impact(result.structured)
        except Exception:
            logger.warning("No preview for %s", name, exc_info=True)
            return None

    # ---- calls ---------------------------------------------------------------------------

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Route the call to its tool. Never raises for a tool failure."""
        entry = self._entries().get(name)
        if entry is None:
            return ToolResult.failure("UNKNOWN_TOOL", f"Outil inconnu : {name}.")
        started = time.monotonic()
        if entry.native is not None:
            try:
                return await entry.native.call(arguments)
            except Exception as exc:
                logger.exception("Native tool %s raised", name)
                return ToolResult.failure(
                    "TOOL_FAILED", f"L'outil {name} a échoué : {exc}", duration_ms=_ms(started)
                )
        return await self._call_mcp(entry.info, arguments, started)

    async def _call_mcp(
        self, info: ToolInfo, arguments: dict[str, Any], started: float
    ) -> ToolResult:
        assert self.manager is not None and info.server is not None
        server = info.server
        try:
            result = await self.manager.call(server, info.original_name, arguments)
        except McpTimeout as exc:
            return ToolResult.failure("TOOL_TIMEOUT", exc.message, duration_ms=_ms(started))
        except (McpCommandNotFound, McpServerDied, McpProtocolError) as exc:
            return ToolResult.failure(
                "MCP_SERVER_UNAVAILABLE",
                f"Serveur MCP « {server} » indisponible : {exc.message}",
                duration_ms=_ms(started),
            )
        except JsonRpcError as exc:
            return ToolResult.failure("TOOL_FAILED", exc.message, duration_ms=_ms(started))
        except Exception as exc:  # the turn must go on whatever the server does
            logger.exception("Tool %s raised", info.name)
            return ToolResult.failure(
                "TOOL_FAILED", f"L'outil {info.name} a échoué : {exc}", duration_ms=_ms(started)
            )
        return to_tool_result(result, duration_ms=_ms(started))

    # ---- building the list of tools ------------------------------------------------------

    def _entries(self) -> dict[str, _Entry]:
        """Tools by name, rebuilt on each use: a restarted server may list new tools."""
        entries: dict[str, _Entry] = {}
        for tool in self.native_tools:
            info = ToolInfo(
                name=tool.name,
                server=None,
                original_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                annotations=tool.annotations,
                trusted=True,
            )
            entries[tool.name] = _Entry(info, tool)
        if self.manager is not None:
            for server, tools in self.manager.tools().items():
                for tool in tools:
                    info = self._mcp_info(server, tool)
                    if info.name in entries:
                        logger.warning("Tool name %s already used, %s ignored", info.name, server)
                        continue
                    entries[info.name] = _Entry(info, None)
        return entries

    def _mcp_info(self, server: str, tool: McpTool) -> ToolInfo:
        return ToolInfo(
            name=tool_name(server, tool.name),
            server=server,
            original_name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            annotations=tool.annotations,
            trusted=server in self.trusted_servers,
        )

    def _is_up(self, server: str) -> bool:
        return self.manager is not None and self.manager.is_up(server)


def tool_name(server: str, tool: str) -> str:
    """Name seen by the model: "<server>__<tool>", limited to [A-Za-z0-9_-] and 64 chars."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}{SEPARATOR}{tool}")
    return name[:MAX_NAME_LENGTH]


def to_tool_result(result: McpCallResult, *, duration_ms: int = 0) -> ToolResult:
    """Convert an MCP tools/call result (tools.md §3.3)."""
    text = "\n".join(
        block["text"]
        if block.get("type") == "text" and isinstance(block.get("text"), str)
        else NON_TEXT_BLOCK
        for block in result.content
    )
    data = result.structured
    if result.is_error:
        code = data.get("code") if data else None
        message = text or (str(data.get("message")) if data else "") or "Échec de l'outil."
        return ToolResult.failure(
            code if isinstance(code, str) and code else "TOOL_FAILED",
            message,
            duration_ms=duration_ms,
        )
    if not text:
        # The model must always read something (tools.md §3.3)
        text = json.dumps(data, ensure_ascii=False) if data else "(résultat vide)"
    return ToolResult(
        ok=True, text=text, summary=_summary(text, data), data=data, duration_ms=duration_ms
    )


def _summary(text: str, data: dict[str, Any] | None) -> str:
    """Short French text for the timeline: "200 lignes · tronqué", or the start of the text."""
    if data is not None and isinstance(data.get("row_count"), int):
        count = data["row_count"]
        summary = f"{count} ligne" + ("s" if count > 1 else "")
        return summary + (" · tronqué" if data.get("truncated") else "")
    first_line = " ".join(text.split())
    if len(first_line) <= SUMMARY_LENGTH:
        return first_line
    return first_line[: SUMMARY_LENGTH - 1] + "…"


def _ollama_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """The inputSchema without its $schema key, which Ollama does not accept."""
    parameters = {key: value for key, value in schema.items() if key != "$schema"}
    parameters.setdefault("type", "object")
    return parameters


def _error_text(error: jsonschema.ValidationError) -> str:
    path = ".".join(str(part) for part in error.path)
    return f"{path} : {error.message}" if path else error.message


def _valid_impact(impact: Any) -> dict[str, Any] | None:
    """The impact if it has the fields of confirmation_required.impact, else None."""
    if not isinstance(impact, dict) or not set(impact) >= IMPACT_KEYS:
        return None
    return {key: impact[key] for key in IMPACT_KEYS | OPTIONAL_IMPACT_KEYS if key in impact}


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


__all__ = ["Registry", "to_tool_result", "tool_name"]
