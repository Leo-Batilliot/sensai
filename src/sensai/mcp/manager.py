"""McpManager: starts the MCP servers of the configuration and keeps track of them.

Each [mcp.servers.<name>] table gives a command; the manager starts them all in parallel
with one McpClient each, lists their tools, and reports their state. A server that cannot
start is marked down with its reason: the others keep working. A server that crashes is
restarted once, on the next call that needs it; if it crashes again it stays down.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sensai.core.config import Config, McpServerConfig
from sensai.core.errors import McpServerDied, SensaiError
from sensai.mcp.client import DEFAULT_REQUEST_TIMEOUT_S, McpCallResult, McpClient, McpTool
from sensai.tools.base import ServerStatus

logger = logging.getLogger(__name__)

DEFAULT_START_TIMEOUT_S = 30.0
MAX_RESTARTS = 1
# Our own SQL server: run by the current interpreter, with the limits of [database]
SQL_TOOLBOX_MODULE = "sensai.mcp_servers.sql_toolbox"
PYTHON_COMMANDS = {"python", "python3"}


@dataclass
class _Server:
    name: str
    command: str
    args: tuple[str, ...]
    client: McpClient | None = None
    tools: list[McpTool] = field(default_factory=list)  # last known list, kept when down
    error: str | None = "pas encore démarré"
    restarts: int = 0

    @property
    def up(self) -> bool:
        return self.client is not None and self.client.available


class McpManager:
    """The MCP servers of one session. Use it from a single event loop."""

    def __init__(
        self,
        servers: Mapping[str, McpServerConfig],
        *,
        start_timeout_s: float = DEFAULT_START_TIMEOUT_S,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self.start_timeout_s = start_timeout_s
        self.request_timeout_s = request_timeout_s
        self._servers = {
            name: _Server(name, _command(server), tuple(server.args))
            for name, server in servers.items()
        }

    @classmethod
    def from_config(cls, config: Config, **options: Any) -> McpManager:
        servers = {name: _own_server(server, config) for name, server in config.mcp.servers.items()}
        return cls(servers, **options)

    async def start_all(self) -> list[ServerStatus]:
        """Start every server in parallel; returns their state. Never raises."""
        await asyncio.gather(*(self._start(server) for server in self._servers.values()))
        return self.status()

    async def stop_all(self) -> None:
        await asyncio.gather(*(self._stop(server) for server in self._servers.values()))

    def status(self) -> list[ServerStatus]:
        """State of each configured server, in configuration order."""
        return [
            ServerStatus(server.name, "up")
            if server.up
            else ServerStatus(server.name, "down", self._down_reason(server))
            for server in self._servers.values()
        ]

    def tools(self) -> dict[str, list[McpTool]]:
        """Last known tools of each server, including the servers that are down."""
        return {name: list(server.tools) for name, server in self._servers.items()}

    def is_up(self, name: str) -> bool:
        server = self._servers.get(name)
        return server is not None and server.up

    async def call(
        self, name: str, tool: str, arguments: dict[str, Any], timeout_s: float | None = None
    ) -> McpCallResult:
        """Call a tool on a server, restarting the server once if it has crashed.

        Raises the McpClient errors (McpServerDied, McpTimeout, JsonRpcError...): turning
        them into tool results is the registry's job. A call that was running when the
        server crashed is not retried: a write must never run twice.
        """
        server = self._servers.get(name)
        if server is None:
            raise McpServerDied(name, "serveur non déclaré dans la configuration")
        if not server.up:
            await self._restart(server)
        assert server.client is not None
        return await server.client.call_tool(tool, arguments, timeout_s)

    async def _start(self, server: _Server) -> None:
        client = McpClient(
            server.name,
            server.command,
            server.args,
            request_timeout_s=self.request_timeout_s,
        )
        try:
            await asyncio.wait_for(client.start(), self.start_timeout_s)
            tools = await client.list_tools()
        except asyncio.TimeoutError:
            await client.close()
            server.client, server.error = (
                None,
                (f"pas de réponse au démarrage en {self.start_timeout_s:g} s"),
            )
        except SensaiError as exc:
            await client.close()
            server.client, server.error = None, exc.message
        except Exception as exc:  # a broken server must not stop the others
            logger.exception("MCP server %s failed to start", server.name)
            await client.close()
            server.client, server.error = None, f"échec du démarrage : {exc}"
        else:
            server.client, server.tools, server.error = client, tools, None
            logger.info("MCP server %s: %d tools", server.name, len(tools))
            return
        logger.warning("MCP server %s is down: %s", server.name, server.error)

    async def _restart(self, server: _Server) -> None:
        """One restart per server for the whole session, then it stays down."""
        if server.restarts >= MAX_RESTARTS:
            raise McpServerDied(server.name, self._down_reason(server))
        server.restarts += 1
        logger.warning(
            "MCP server %s is down (%s), restarting it", server.name, self._down_reason(server)
        )
        await self._stop(server)
        await self._start(server)
        if not server.up:
            raise McpServerDied(server.name, self._down_reason(server))

    async def _stop(self, server: _Server) -> None:
        if server.client is not None:
            await server.client.close()

    @staticmethod
    def _down_reason(server: _Server) -> str:
        if server.client is not None and not server.client.available:
            return "le serveur s'est arrêté"
        return server.error or "indisponible"


def _command(server: McpServerConfig) -> str:
    """Command that starts a server: our own server runs with the current interpreter.

    "python" is not on the PATH everywhere (Ubuntu only has python3, and the project's
    interpreter lives in .venv): run that way, sql_toolbox always finds the sensai package.
    """
    if SQL_TOOLBOX_MODULE in server.args and server.command in PYTHON_COMMANDS:
        return sys.executable
    return server.command


def _own_server(server: McpServerConfig, config: Config) -> McpServerConfig:
    """sql_toolbox gets its database and limits from [database], unless already given.

    [database] business_path is the only place that says where the database is: the seed,
    the web server and the confirmation policy read it too. A --db written in the server's
    arguments still wins, with a warning when it points elsewhere.
    """
    args = list(server.args)
    if SQL_TOOLBOX_MODULE not in args:
        return server
    business_path = config.database.business_path
    if "--db" not in args:
        args += ["--db", str(business_path)]
    else:
        given = args[args.index("--db") + 1] if args.index("--db") + 1 < len(args) else ""
        if Path(given).resolve() != Path(business_path).resolve():
            logger.warning(
                "sql_toolbox lit %s alors que [database] business_path vaut %s",
                given,
                business_path,
            )
    if "--row-limit" not in args:
        args += ["--row-limit", str(config.database.row_limit)]
    if "--timeout-ms" not in args:
        args += ["--timeout-ms", str(config.database.query_timeout_ms)]
    return server.model_copy(update={"args": tuple(args)})
