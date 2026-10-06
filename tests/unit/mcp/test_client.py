import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from sensai.core.errors import McpProtocolError, McpServerDied, McpTimeout
from sensai.mcp.client import (
    SUPPORTED_PROTOCOL_VERSIONS,
    McpClient,
    ToolAnnotations,
)
from sensai.mcp.jsonrpc import JsonRpcError, Message
from sensai.mcp.stdio import OnClosed, OnMessage


class FakeTransport:
    """Records what the client sends; the test plays the server by calling reply()."""

    def __init__(self, protocol_version: str = "2025-06-18") -> None:
        self.protocol_version = protocol_version
        self.sent: list[Message] = []
        self.closed = False
        self.on_message: OnMessage | None = None
        self.on_closed: OnClosed | None = None

    async def start(self, on_message: OnMessage, on_closed: OnClosed) -> None:
        self.on_message = on_message
        self.on_closed = on_closed

    async def send(self, message: Message) -> None:
        if self.closed:
            raise McpServerDied("fake", "connexion fermée")
        self.sent.append(message)
        if message.get("method") == "initialize":
            self.reply(
                message["id"],
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-server", "version": "9.9"},
                },
            )

    async def close(self) -> None:
        self.closed = True

    def deliver(self, message: Message) -> None:
        assert self.on_message is not None
        asyncio.get_running_loop().call_soon(self.on_message, message)

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.deliver({"jsonrpc": "2.0", "id": request_id, "result": result})

    def methods(self) -> list[str | None]:
        return [message.get("method") for message in self.sent]

    async def wait_for_request(self, method: str, count: int = 1) -> list[Message]:
        for _ in range(100):
            found = [m for m in self.sent if m.get("method") == method and "id" in m]
            if len(found) >= count:
                return found
            await asyncio.sleep(0.01)
        raise AssertionError(f"{method} never sent")


@pytest.fixture
async def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
async def client(transport: FakeTransport) -> AsyncIterator[McpClient]:
    async with McpClient("fake", "unused", transport=transport, request_timeout_s=1) as client:
        yield client


async def test_start_runs_the_handshake(transport: FakeTransport) -> None:
    client = McpClient("fake", "unused", transport=transport)
    info = await client.start()
    initialize = transport.sent[0]
    assert initialize["params"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert initialize["params"]["clientInfo"]["name"] == "sensai"
    assert transport.methods() == ["initialize", "notifications/initialized"]
    assert (info.name, info.version, info.protocol_version) == ("fake-server", "9.9", "2025-06-18")
    assert client.available


async def test_unsupported_protocol_version_closes_the_server() -> None:
    transport = FakeTransport(protocol_version="1999-01-01")
    client = McpClient("fake", "unused", transport=transport)
    with pytest.raises(McpProtocolError, match="1999-01-01"):
        await client.start()
    assert transport.closed
    assert not client.available


async def test_responses_are_matched_by_id_when_out_of_order(
    client: McpClient, transport: FakeTransport
) -> None:
    first = asyncio.ensure_future(client.call_tool("echo", {"text": "first"}))
    second = asyncio.ensure_future(client.call_tool("echo", {"text": "second"}))
    calls = await transport.wait_for_request("tools/call", count=2)
    for call in reversed(calls):
        text = call["params"]["arguments"]["text"]
        transport.reply(call["id"], {"content": [{"type": "text", "text": text}]})
    assert (await first).text == "first"
    assert (await second).text == "second"


async def test_rpc_error_is_raised(client: McpClient, transport: FakeTransport) -> None:
    task = asyncio.ensure_future(client.call_tool("echo", {}))
    (call,) = await transport.wait_for_request("tools/call")
    transport.deliver(
        {"jsonrpc": "2.0", "id": call["id"], "error": {"code": -32602, "message": "Invalid params"}}
    )
    with pytest.raises(JsonRpcError) as exc:
        await task
    assert exc.value.rpc_code == -32602


async def test_tool_error_is_a_result_not_an_exception(
    client: McpClient, transport: FakeTransport
) -> None:
    task = asyncio.ensure_future(client.call_tool("fail", {}))
    (call,) = await transport.wait_for_request("tools/call")
    transport.reply(call["id"], {"content": [{"type": "text", "text": "boom"}], "isError": True})
    result = await task
    assert result.is_error
    assert result.text == "boom"


async def test_timeout_sends_a_cancellation(client: McpClient, transport: FakeTransport) -> None:
    with pytest.raises(McpTimeout):
        await client.call_tool("sleep", {}, timeout_s=0.05)
    (call,) = await transport.wait_for_request("tools/call")
    cancelled = transport.sent[-1]
    assert cancelled["method"] == "notifications/cancelled"
    assert cancelled["params"]["requestId"] == call["id"]
    # A late answer is ignored and the client keeps working
    transport.reply(call["id"], {"content": []})
    assert client.available


async def test_server_crash_fails_pending_and_next_calls(
    client: McpClient, transport: FakeTransport
) -> None:
    task = asyncio.ensure_future(client.call_tool("echo", {"text": "x"}))
    await transport.wait_for_request("tools/call")
    assert transport.on_closed is not None
    transport.on_closed("sortie standard fermée")
    with pytest.raises(McpServerDied):
        await task
    assert not client.available
    with pytest.raises(McpServerDied):
        await client.list_tools()


async def test_server_requests_get_an_answer(client: McpClient, transport: FakeTransport) -> None:
    transport.deliver({"jsonrpc": "2.0", "id": "s1", "method": "ping"})
    transport.deliver({"jsonrpc": "2.0", "id": "s2", "method": "sampling/createMessage"})
    for _ in range(100):
        answers = {m["id"]: m for m in transport.sent if m.get("id") in ("s1", "s2")}
        if len(answers) == 2:
            break
        await asyncio.sleep(0.01)
    assert answers["s1"]["result"] == {}
    assert answers["s2"]["error"]["code"] == -32601


async def test_list_changed_notification_is_remembered(
    client: McpClient, transport: FakeTransport
) -> None:
    transport.deliver({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    await asyncio.sleep(0.01)
    assert client.tools_changed


async def test_list_tools_parses_tools(client: McpClient, transport: FakeTransport) -> None:
    task = asyncio.ensure_future(client.list_tools())
    (call,) = await transport.wait_for_request("tools/list")
    transport.reply(
        call["id"],
        {
            "tools": [
                {
                    "name": "run_select",
                    "description": "Read",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True, "title": "Lire"},
                },
                {"name": "bare"},
            ]
        },
    )
    run_select, bare = await task
    assert run_select.annotations == ToolAnnotations(title="Lire", read_only=True)
    # Absent hints stay None: the confirmation policy applies the MCP defaults itself
    assert bare.annotations == ToolAnnotations()
    assert bare.input_schema == {"type": "object"}


async def test_list_tools_rejects_a_repeated_cursor(
    client: McpClient, transport: FakeTransport
) -> None:
    task = asyncio.ensure_future(client.list_tools())
    (first,) = await transport.wait_for_request("tools/list")
    transport.reply(first["id"], {"tools": [], "nextCursor": "a"})
    _, second = await transport.wait_for_request("tools/list", count=2)
    transport.reply(second["id"], {"tools": [], "nextCursor": "a"})
    with pytest.raises(McpProtocolError):
        await task
