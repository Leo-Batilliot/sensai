"""How the McpManager builds the command of sql_toolbox from the configuration."""

import logging
import sys
from pathlib import Path

import pytest

from sensai.core.config import Config, DatabaseConfig, McpServerConfig
from sensai.mcp.manager import McpManager

MODULE = ("-m", "sensai.mcp_servers.sql_toolbox")


def args_of(config: Config, name: str = "sql_toolbox") -> tuple[str, ...]:
    return McpManager.from_config(config)._servers[name].args


def option(args: tuple[str, ...], name: str) -> str:
    return args[args.index(name) + 1]


def test_default_config_reads_business_path() -> None:
    args = args_of(Config())
    assert option(args, "--db") == str(Config().database.business_path)
    assert args.count("--db") == 1


def test_changing_business_path_moves_the_server_too() -> None:
    config = Config(
        database=DatabaseConfig(business_path=Path("data/demo.db"), row_limit=50),
    )
    args = args_of(config)
    assert option(args, "--db") == str(Path("data/demo.db"))
    assert option(args, "--row-limit") == "50"


def test_an_explicit_db_wins_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    server = McpServerConfig(command="python", args=(*MODULE, "--db", "other.db"))
    config = Config(mcp={"servers": {"sql_toolbox": server}})
    with caplog.at_level(logging.WARNING):
        args = args_of(config)
    assert option(args, "--db") == "other.db"
    assert args.count("--db") == 1
    assert "business_path" in caplog.text


def test_the_same_db_written_twice_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    path = str(Config().database.business_path)
    server = McpServerConfig(command="python", args=(*MODULE, "--db", path))
    with caplog.at_level(logging.WARNING):
        args_of(Config(mcp={"servers": {"sql_toolbox": server}}))
    assert caplog.text == ""


def test_other_servers_are_left_alone() -> None:
    other = McpServerConfig(command="uvx", args=("mcp-server-time",))
    manager = McpManager.from_config(Config(mcp={"servers": {"time": other}}))
    assert manager._servers["time"].args == ("mcp-server-time",)
    assert manager._servers["time"].command == "uvx"


def test_our_server_runs_with_this_interpreter() -> None:
    assert McpManager.from_config(Config())._servers["sql_toolbox"].command == sys.executable
