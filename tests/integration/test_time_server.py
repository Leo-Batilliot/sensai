"""MCP-06: mcp-server-time, an existing MCP server, plugged in by configuration only.

The [mcp.servers.time] table of config.example.toml is all it takes: no code of SENSAI
knows this server. Needs uvx, which downloads the server on its first run (marker
external, excluded from the CI).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from sensai.core.config import load_config
from sensai.mcp.manager import McpManager
from sensai.tools.registry import Registry

pytestmark = pytest.mark.external

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config.example.toml"


async def test_the_time_server_comes_from_the_configuration_only() -> None:
    config = load_config(str(EXAMPLE_CONFIG))
    # Only the time server: sql_toolbox would need the demo database
    manager = McpManager({"time": config.mcp.servers["time"]}, start_timeout_s=120)
    try:
        (status,) = await manager.start_all()
        registry = Registry(manager)
        offered = [tool["function"]["name"] for tool in registry.ollama_tools()]
        result = await registry.call("time__get_current_time", {"timezone": "Europe/Paris"})
    finally:
        await manager.stop_all()

    assert (status.name, status.status) == ("time", "up")
    assert "time__get_current_time" in offered  # prefixed by its server name (P-17)
    assert result.ok, result.text
    now = json.loads(result.text)
    assert now["timezone"] == "Europe/Paris"
    assert datetime.fromisoformat(now["datetime"]).date() == datetime.now().date()
