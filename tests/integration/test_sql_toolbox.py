"""sql-toolbox run as a real subprocess and driven by our MCP client."""

import os
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from sensai.data.seed import generate
from sensai.mcp.client import McpClient
from sensai.mcp.stdio import StdioTransport


@pytest.fixture(scope="module")
def business_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("sql_toolbox") / "business.db"
    generate(path, seed=42, today=date(2026, 10, 20))
    return path


def fingerprint(path: Path) -> bytes:
    return path.read_bytes()


def toolbox(db_path: Path, *options: str) -> McpClient:
    args = ["-m", "sensai.mcp_servers.sql_toolbox", "--db", str(db_path), *options]
    return McpClient("sql_toolbox", sys.executable, args, request_timeout_s=10)


@pytest.fixture
async def client(business_db: Path) -> AsyncIterator[McpClient]:
    async with toolbox(business_db) as client:
        yield client


async def test_tools_over_stdio(client: McpClient) -> None:
    assert client.server_info is not None and client.server_info.name == "sql-toolbox"
    tools = {tool.name: tool for tool in await client.list_tools()}
    assert set(tools) == {
        "list_tables",
        "describe_table",
        "run_select",
        "preview_write",
        "execute_write",
    }
    # Only execute_write keeps a change
    assert [name for name, tool in tools.items() if not tool.annotations.read_only] == [
        "execute_write"
    ]
    assert tools["execute_write"].annotations.destructive is True

    tables = (await client.call_tool("list_tables", {})).structured
    assert tables is not None and len(tables["tables"]) == 9

    clients = (await client.call_tool("describe_table", {"table": "clients"})).structured
    assert clients is not None and clients["row_count"] == 300

    result = await client.call_tool(
        "run_select", {"sql": "SELECT name FROM clients", "purpose": "liste des clients"}
    )
    assert not result.is_error and result.structured is not None
    assert result.structured["row_count"] == 200 and result.structured["truncated"]


async def test_a_write_is_refused_and_the_base_is_unchanged(
    client: McpClient, business_db: Path
) -> None:
    before = fingerprint(business_db)
    result = await client.call_tool("run_select", {"sql": "DELETE FROM payments"})
    assert result.is_error
    assert result.structured == {"code": "SQL_FORBIDDEN", "message": result.text}
    assert fingerprint(business_db) == before


async def test_row_limit_option(business_db: Path) -> None:
    async with toolbox(business_db, "--row-limit", "5") as client:
        result = await client.call_tool("run_select", {"sql": "SELECT id FROM orders"})
        assert result.structured is not None and result.structured["row_count"] == 5


async def test_missing_base_stops_the_server(tmp_path: Path) -> None:
    client = toolbox(tmp_path / "missing.db")
    with pytest.raises(Exception, match="arrêté"):
        await client.start()
    assert isinstance(client.transport, StdioTransport)
    assert any("base introuvable" in line for line in client.transport.stderr_tail)
    assert client.transport.pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(client.transport.pid, 0)
