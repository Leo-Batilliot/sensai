"""McpClient against real subprocesses: the echo server of the fixtures, and
mcp-server-time (external, needs uvx)."""

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest

from sensai.core.errors import McpCommandNotFound, McpProtocolError, McpServerDied, McpTimeout
from sensai.mcp.client import McpClient
from sensai.mcp.jsonrpc import JsonRpcError
from sensai.mcp.stdio import StdioTransport

ECHO_SERVER = str(Path(__file__).parents[1] / "fixtures" / "mcp" / "echo_server.py")


def echo_client(*options: str, stop_grace_s: float = 2.0) -> McpClient:
    transport = StdioTransport(
        "echo", sys.executable, [ECHO_SERVER, *options], stop_grace_s=stop_grace_s
    )
    return McpClient("echo", sys.executable, transport=transport, request_timeout_s=5)


def assert_process_gone(transport: StdioTransport) -> None:
    assert transport.returncode is not None
    assert transport.pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(transport.pid, 0)


@pytest.fixture
async def echo() -> AsyncIterator[McpClient]:
    async with echo_client() as client:
        yield client


async def test_handshake_and_paginated_tools(echo: McpClient) -> None:
    assert echo.server_info is not None
    assert echo.server_info.name == "echo-server"
    tools = {tool.name: tool for tool in await echo.list_tools()}
    assert list(tools) == ["echo", "fail", "sleep", "crash"]
    assert tools["echo"].annotations.read_only is True
    assert tools["fail"].annotations.read_only is None
    assert tools["crash"].annotations.destructive is True


async def test_call_tool(echo: McpClient) -> None:
    result = await echo.call_tool("echo", {"text": "Bonjour\nClaire"})
    assert not result.is_error
    assert result.text == "Bonjour\nClaire"
    assert result.structured == {"text": "Bonjour\nClaire"}


async def test_tool_errors_are_results(echo: McpClient) -> None:
    assert (await echo.call_tool("fail", {})).is_error
    assert (await echo.call_tool("missing", {})).is_error


async def test_invalid_params_raise_rpc_error(echo: McpClient) -> None:
    with pytest.raises(JsonRpcError) as exc:
        await echo.call_tool("echo", {})
    assert exc.value.rpc_code == -32602


async def test_timeout_then_the_client_still_works(echo: McpClient) -> None:
    with pytest.raises(McpTimeout):
        await echo.call_tool("sleep", {"seconds": 0.5}, timeout_s=0.1)
    # The late "awake" answer is dropped; the next call gets its own answer
    assert (await echo.call_tool("echo", {"text": "after"})).text == "after"


async def test_crash_makes_the_client_unavailable() -> None:
    client = echo_client()
    await client.start()
    try:
        with pytest.raises(McpServerDied):
            await client.call_tool("crash", {})
        assert not client.available
        with pytest.raises(McpServerDied):
            await client.call_tool("echo", {"text": "x"})
    finally:
        await client.close()
    assert isinstance(client.transport, StdioTransport)
    assert client.transport.returncode == 3


async def test_close_leaves_no_process() -> None:
    client = echo_client()
    await client.start()
    await client.close()
    assert isinstance(client.transport, StdioTransport)
    assert_process_gone(client.transport)


async def test_close_stops_a_busy_server() -> None:
    client = echo_client(stop_grace_s=0.2)
    await client.start()
    with pytest.raises(McpTimeout):
        await client.call_tool("sleep", {"seconds": 30}, timeout_s=0.1)
    started = time.monotonic()
    await client.close()
    assert time.monotonic() - started < 2
    assert isinstance(client.transport, StdioTransport)
    assert_process_gone(client.transport)


async def test_banner_on_stdout_is_ignored() -> None:
    async with echo_client("--banner") as client:
        assert (await client.call_tool("echo", {"text": "ok"})).text == "ok"


async def test_server_ping_is_answered() -> None:
    async with echo_client("--ping") as client:
        assert isinstance(client.transport, StdioTransport)
        for _ in range(100):
            answers = [line for line in client.transport.stderr_tail if "client answered" in line]
            if answers:
                break
            await asyncio.sleep(0.02)
        assert answers
        answer = json.loads(answers[0].removeprefix("client answered "))
        assert answer == {"jsonrpc": "2.0", "id": "srv-1", "result": {}}


async def test_unsupported_version_is_refused() -> None:
    client = echo_client("--protocol-version", "1999-01-01")
    with pytest.raises(McpProtocolError):
        await client.start()
    assert isinstance(client.transport, StdioTransport)
    assert_process_gone(client.transport)


async def test_missing_command() -> None:
    client = McpClient("ghost", "sensai-command-that-does-not-exist")
    with pytest.raises(McpCommandNotFound):
        await client.start()


@pytest.mark.external
async def test_mcp_server_time() -> None:
    async with McpClient(
        "time", "uvx", ["mcp-server-time", "--local-timezone=Europe/Paris"], request_timeout_s=60
    ) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "get_current_time" in tools
        assert tools["get_current_time"].annotations.read_only is True

        result = await client.call_tool("get_current_time", {"timezone": "Europe/Paris"})
        assert not result.is_error
        now = json.loads(result.text)
        assert now["timezone"] == "Europe/Paris"
        assert datetime.fromisoformat(now["datetime"]).year >= 2026

        assert (await client.call_tool("get_current_time", {"timezone": "Mars/Olympus"})).is_error
