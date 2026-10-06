"""Generic MCP client, written by hand on top of JSON-RPC 2.0.

One McpClient talks to one server: `initialize` handshake, `tools/list` and
`tools/call`. It works with any MCP server, ours (sql_toolbox) as well as existing ones
(mcp-server-time, filesystem). Aggregating several servers is the McpManager's job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from sensai import __version__
from sensai.core.errors import McpProtocolError, McpServerDied, McpTimeout
from sensai.mcp import jsonrpc
from sensai.mcp.jsonrpc import IdGenerator, JsonRpcError, Message, RequestId
from sensai.mcp.stdio import StdioTransport, Transport

logger = logging.getLogger(__name__)

# Newest first: the first one is announced, any of them is accepted from the server.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_REQUEST_TIMEOUT_S = 30.0
# A server that keeps returning a cursor must not keep list_tools() looping forever
MAX_TOOL_PAGES = 100


@dataclass(frozen=True)
class ServerInfo:
    """What the server told us in its `initialize` answer."""

    name: str
    version: str
    protocol_version: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None


@dataclass(frozen=True)
class ToolAnnotations:
    """Behaviour hints declared by the server, None when a hint is absent.

    MCP defines defaults for absent hints (readOnly false, destructive true, idempotent
    false, openWorld true); they are left to the confirmation policy, which must tell
    "declared" from "absent". Hints are not guarantees: they never replace the policy.
    """

    title: str | None = None
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ToolAnnotations:
        if not isinstance(payload, dict):
            return cls()

        def hint(key: str) -> bool | None:
            value = payload.get(key)
            return value if isinstance(value, bool) else None

        title = payload.get("title")
        return cls(
            title=title if isinstance(title, str) else None,
            read_only=hint("readOnlyHint"),
            destructive=hint("destructiveHint"),
            idempotent=hint("idempotentHint"),
            open_world=hint("openWorldHint"),
        )


@dataclass(frozen=True)
class McpTool:
    """A tool as listed by `tools/list`."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)


@dataclass(frozen=True)
class McpCallResult:
    """Result of `tools/call`. is_error=True is a tool failure, to send back to the model."""

    content: list[dict[str, Any]]
    structured: dict[str, Any] | None = None
    is_error: bool = False

    @property
    def text(self) -> str:
        """Text blocks joined, which is what the model reads."""
        return "\n".join(
            block["text"]
            for block in self.content
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        )


class McpClient:
    """Client for one MCP server over stdio.

    Use it as `async with McpClient(...) as client:` or call start() and close(). After
    a crash the client stays unavailable: every call raises McpServerDied.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | tuple[str, ...] = (),
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        transport: Transport | None = None,
    ) -> None:
        self.name = name
        self.request_timeout_s = request_timeout_s
        self.transport: Transport = transport or StdioTransport(
            name, command, args, env=env, cwd=cwd
        )
        # Set when the server sends notifications/tools/list_changed; list_tools() resets it
        self.tools_changed = False
        self._ids = IdGenerator()
        self._pending: dict[RequestId, asyncio.Future[Message]] = {}
        self._background: set[asyncio.Future[None]] = set()
        self._server_info: ServerInfo | None = None
        self._died: str | None = None
        self._closing = False

    @property
    def server_info(self) -> ServerInfo | None:
        return self._server_info

    @property
    def available(self) -> bool:
        return self._server_info is not None and self._died is None and not self._closing

    async def __aenter__(self) -> McpClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> ServerInfo:
        """Start the server, then run the initialize handshake."""
        await self.transport.start(self._on_message, self._on_closed)
        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
                    "capabilities": {},
                    "clientInfo": {"name": "sensai", "version": __version__},
                },
            )
            info = self._parse_server_info(result)
            await self.transport.send(jsonrpc.notification("notifications/initialized"))
        except BaseException:
            await self.close()
            raise
        self._server_info = info
        logger.info(
            "MCP server %s ready: %s %s (protocol %s)",
            self.name,
            info.name,
            info.version,
            info.protocol_version,
        )
        return info

    async def list_tools(self) -> list[McpTool]:
        """Every tool of the server, all pages included."""
        tools: list[McpTool] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_PAGES):
            result = await self._request("tools/list", {"cursor": cursor} if cursor else None)
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpProtocolError(self.name, "réponse à tools/list sans liste d'outils")
            tools.extend(self._parse_tool(raw) for raw in page)
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                self.tools_changed = False
                return tools
            if not isinstance(next_cursor, str) or next_cursor == cursor:
                raise McpProtocolError(self.name, "pagination de tools/list invalide")
            cursor = next_cursor
        raise McpProtocolError(self.name, f"tools/list dépasse {MAX_TOOL_PAGES} pages")

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout_s: float | None = None
    ) -> McpCallResult:
        """Run a tool. A tool failure comes back as is_error=True, not as an exception."""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout_s
        )
        content = result.get("content", [])
        if not isinstance(content, list):
            raise McpProtocolError(self.name, "réponse à tools/call sans contenu valide")
        structured = result.get("structuredContent")
        return McpCallResult(
            content=[block for block in content if isinstance(block, dict)],
            structured=structured if isinstance(structured, dict) else None,
            is_error=result.get("isError") is True,
        )

    async def close(self) -> None:
        self._closing = True
        await self.transport.close()
        self._fail_pending("connexion fermée")
        for task in list(self._background):
            task.cancel()

    async def _request(
        self, method: str, params: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> dict[str, Any]:
        if self._died is not None:
            raise McpServerDied(self.name, self._died)
        timeout = self.request_timeout_s if timeout_s is None else timeout_s
        request_id = self._ids.next()
        future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.transport.send(jsonrpc.request(request_id, method, params))
            message = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            # The spec forbids cancelling initialize; for the rest, tell the server to stop
            if method != "initialize":
                await self._send_cancelled(request_id, f"délai de {timeout:g} s dépassé")
            raise McpTimeout(self.name, method, timeout) from None
        finally:
            self._pending.pop(request_id, None)
        if "error" in message:
            raise JsonRpcError.from_payload(message["error"])
        result = message.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(self.name, f"réponse à {method} sans résultat")
        return result

    async def _send_cancelled(self, request_id: RequestId, reason: str) -> None:
        with contextlib.suppress(McpServerDied):
            await self.transport.send(
                jsonrpc.notification(
                    "notifications/cancelled", {"requestId": request_id, "reason": reason}
                )
            )

    def _on_message(self, message: Message) -> None:
        if jsonrpc.is_response(message):
            future = self._pending.get(message["id"])
            if future is not None and not future.done():
                future.set_result(message)
            else:
                # Late answer to a request that timed out, or an error with id null
                logger.debug("MCP server %s: unexpected response id %r", self.name, message["id"])
        elif jsonrpc.is_request(message):
            self._spawn(self._answer_server_request(message))
        elif jsonrpc.is_notification(message):
            if message["method"] == "notifications/tools/list_changed":
                self.tools_changed = True
            logger.debug("MCP server %s: notification %s", self.name, message["method"])
        else:
            logger.warning("MCP server %s: ignored a malformed message", self.name)

    async def _answer_server_request(self, message: Message) -> None:
        # We declare no client capability (sampling, roots, elicitation): only ping exists
        if message["method"] == "ping":
            answer = jsonrpc.result_response(message["id"], {})
        else:
            answer = jsonrpc.error_response(
                message["id"], jsonrpc.METHOD_NOT_FOUND, f"Method not found: {message['method']}"
            )
        with contextlib.suppress(McpServerDied):
            await self.transport.send(answer)

    def _on_closed(self, reason: str) -> None:
        if self._closing:
            return
        self._died = reason
        logger.warning("MCP server %s stopped: %s", self.name, reason)
        self._fail_pending(reason)

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpServerDied(self.name, reason))
        self._pending.clear()

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _parse_server_info(self, result: dict[str, Any]) -> ServerInfo:
        version = result.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise McpProtocolError(
                self.name,
                f"version de protocole {version!r} non prise en charge "
                f"(versions acceptées : {', '.join(SUPPORTED_PROTOCOL_VERSIONS)})",
            )
        server = result.get("serverInfo")
        server = server if isinstance(server, dict) else {}
        capabilities = result.get("capabilities")
        instructions = result.get("instructions")
        return ServerInfo(
            name=str(server.get("name", self.name)),
            version=str(server.get("version", "")),
            protocol_version=version,
            capabilities=capabilities if isinstance(capabilities, dict) else {},
            instructions=instructions if isinstance(instructions, str) else None,
        )

    def _parse_tool(self, raw: Any) -> McpTool:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise McpProtocolError(self.name, f"outil mal formé dans tools/list : {raw!r}"[:300])
        input_schema = raw.get("inputSchema")
        output_schema = raw.get("outputSchema")
        description = raw.get("description")
        return McpTool(
            name=raw["name"],
            description=description if isinstance(description, str) else "",
            input_schema=input_schema if isinstance(input_schema, dict) else {"type": "object"},
            output_schema=output_schema if isinstance(output_schema, dict) else None,
            annotations=ToolAnnotations.from_payload(raw.get("annotations")),
        )
