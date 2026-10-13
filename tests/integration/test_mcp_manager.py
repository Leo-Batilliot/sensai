"""McpManager and Registry with real subprocesses: the echo server of the fixtures, our
sql_toolbox, and the Engine calling run_select through MCP."""

import contextlib
import sys
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from fakes import FakeClient, answer, tool_request
from sensai.core import events
from sensai.core.config import Config, DatabaseConfig, McpServerConfig
from sensai.core.engine import Engine
from sensai.data.seed import generate
from sensai.mcp.manager import McpManager
from sensai.tools.registry import Registry

ECHO_SERVER = str(Path(__file__).parents[1] / "fixtures" / "mcp" / "echo_server.py")
ECHO = McpServerConfig(command=sys.executable, args=(ECHO_SERVER,))
GHOST = McpServerConfig(command="sensai-command-that-does-not-exist")


@pytest.fixture(scope="module")
def business_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("manager") / "business.db"
    generate(path, seed=42, today=date(2026, 10, 20))
    return path


def toolbox(db_path: Path) -> McpServerConfig:
    return McpServerConfig(
        command="python", args=("-m", "sensai.mcp_servers.sql_toolbox", "--db", str(db_path))
    )


@contextlib.asynccontextmanager
async def started(timeout_s: float = 5, **servers: McpServerConfig) -> AsyncIterator[McpManager]:
    manager = McpManager(servers, request_timeout_s=timeout_s)
    await manager.start_all()
    try:
        yield manager
    finally:
        await manager.stop_all()


async def test_a_failing_server_does_not_stop_the_others() -> None:
    async with started(echo=ECHO, ghost=GHOST) as manager:
        echo, ghost = manager.status()
        assert (echo.name, echo.status, echo.error) == ("echo", "up", None)
        assert ghost.status == "down" and ghost.error is not None
        assert "introuvable" in ghost.error

        registry = Registry(manager)
        names = [tool["function"]["name"] for tool in registry.ollama_tools()]
        assert names == ["echo__echo", "echo__fail", "echo__sleep", "echo__crash"]
        result = await registry.call("echo__echo", {"text": "Bonjour"})
        assert result.ok and result.text == "Bonjour" and result.data == {"text": "Bonjour"}


async def test_failures_become_tool_results() -> None:
    async with started(timeout_s=0.3, echo=ECHO) as manager:
        registry = Registry(manager)
        failed = await registry.call("echo__fail", {})
        assert failed.error is not None and failed.error.code == "TOOL_FAILED"
        invalid = await registry.call("echo__echo", {})  # rejected by the server (-32602)
        assert invalid.error is not None and invalid.error.code == "TOOL_FAILED"
        slow = await registry.call("echo__sleep", {"seconds": 1})
        assert slow.error is not None and slow.error.code == "TOOL_TIMEOUT"


async def test_a_crashed_server_is_restarted_once() -> None:
    async with started(echo=ECHO) as manager:
        registry = Registry(manager)
        crash = await registry.call("echo__crash", {})
        assert crash.error is not None and crash.error.code == "MCP_SERVER_UNAVAILABLE"
        assert manager.status()[0].status == "down"
        # Down servers disappear from what the model sees, but stay known
        assert registry.ollama_tools() == []
        assert registry.get("echo__echo") is not None

        # Next call: one automatic restart
        assert (await registry.call("echo__echo", {"text": "encore"})).ok
        assert manager.status()[0].status == "up"

        # A second crash: it stays down
        await registry.call("echo__crash", {})
        again = await registry.call("echo__echo", {"text": "x"})
        assert again.error is not None and again.error.code == "MCP_SERVER_UNAVAILABLE"


async def test_from_config_runs_sql_toolbox_with_our_interpreter(business_db: Path) -> None:
    config = Config(
        database=DatabaseConfig(row_limit=7, query_timeout_ms=1234),
        mcp={"servers": {"sql_toolbox": toolbox(business_db), "echo": ECHO}},
    )
    manager = McpManager.from_config(config)
    server = manager._servers["sql_toolbox"]
    assert server.command == sys.executable
    assert server.args[-4:] == ("--row-limit", "7", "--timeout-ms", "1234")
    assert manager._servers["echo"].args == (ECHO_SERVER,)  # other servers untouched

    await manager.start_all()
    try:
        registry = Registry(manager)
        info = registry.get("sql_toolbox__run_select")
        assert info is not None and info.trusted and info.annotations.read_only
        echo = registry.get("echo__echo")
        assert echo is not None and not echo.trusted
        rows = await registry.call("sql_toolbox__run_select", {"sql": "SELECT id FROM clients"})
        assert rows.summary == "7 lignes · tronqué"
    finally:
        await manager.stop_all()


async def test_the_engine_calls_run_select_through_mcp(business_db: Path) -> None:
    sql = "SELECT name FROM clients WHERE name = 'Brasserie Lemaire'"
    async with started(sql_toolbox=toolbox(business_db)) as manager:
        engine = Engine(
            Config(),
            FakeClient(
                tool_request("sql_toolbox__run_select", sql=sql, purpose="test"),
                answer("Brasserie Lemaire est bien cliente."),
            ),
            registry=Registry(manager),
            session_id="s-1",
        )
        async with contextlib.aclosing(engine.run_turn("Lemaire est-il client ?")) as turn:
            emitted = [event async for event in turn]

    results = [event for event in emitted if isinstance(event, events.ToolResult)]
    assert len(results) == 1
    assert results[0].ok
    assert results[0].data is not None
    assert results[0].data["rows"] == [["Brasserie Lemaire"]]
    assert isinstance(emitted[-1], events.MessageDone)
