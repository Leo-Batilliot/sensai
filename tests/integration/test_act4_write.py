"""Act 4 end to end: the Engine, the real sql_toolbox over MCP and a seeded database.

"Supprime les clients inactifs" -> execute_write needs a confirmation -> the impact
comes from preview_write -> refused: nothing changes; approved: 25 clients deleted.
"""

import contextlib
import shutil
import sqlite3
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, answer, tool_request
from sensai.core import events
from sensai.core.config import Config, DatabaseConfig, McpServerConfig
from sensai.core.confirm import Decision, PendingAction
from sensai.core.engine import Engine
from sensai.data.seed import generate
from sensai.mcp.manager import McpManager
from sensai.tools.registry import Registry

PURGE = "DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM orders)"
EXECUTE = "sql_toolbox__execute_write"


class Answer:
    """A broker that always gives the same decision and keeps the actions it saw."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.actions: list[PendingAction] = []

    async def request(self, action: PendingAction) -> Decision:
        self.actions.append(action)
        return self.decision


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("act4") / "business.db"
    generate(path, seed=42, today=date(2026, 10, 20))
    return path


@pytest.fixture
def config(seeded: Path, tmp_path: Path) -> Config:
    db_path = tmp_path / "business.db"
    shutil.copy(seeded, db_path)
    toolbox = McpServerConfig(command="python", args=("-m", "sensai.mcp_servers.sql_toolbox"))
    return Config(
        database=DatabaseConfig(business_path=db_path),
        mcp={"servers": {"sql_toolbox": toolbox}},
    )


@contextlib.asynccontextmanager
async def running(config: Config) -> AsyncIterator[Registry]:
    manager = McpManager.from_config(config)
    await manager.start_all()
    try:
        yield Registry(manager)
    finally:
        await manager.stop_all()


async def purge(config: Config, broker: Answer) -> list[events.Event]:
    async with running(config) as registry:
        engine = Engine(
            config,
            FakeClient(tool_request(EXECUTE, sql=PURGE), answer("Voilà.")),
            registry=registry,
            broker=broker,
            session_id="s-1",
        )
        try:
            async with contextlib.aclosing(
                engine.run_turn("Supprime les clients inactifs")
            ) as turn:
                emitted = [event async for event in turn]
        finally:
            await engine.aclose()
    check_turn(emitted)
    return emitted


def inactive(config: Config) -> int:
    conn = sqlite3.connect(config.database.business_path)
    value = conn.execute("SELECT COUNT(*) FROM clients WHERE status = 'inactive'").fetchone()[0]
    conn.close()
    return int(value)


def only(emitted: list[events.Event], kind: type) -> Any:
    (event,) = [event for event in emitted if isinstance(event, kind)]
    return event


async def test_refused_purge_changes_nothing(config: Config) -> None:
    broker = Answer(Decision("reject", "user"))
    emitted = await purge(config, broker)

    required = only(emitted, events.ConfirmationRequired)
    assert required.tool == EXECUTE and required.sql == PURGE
    assert required.impact["rows_affected"] == 25
    assert required.impact["per_table"]["clients"] == -25
    assert only(emitted, events.ToolResult).error.code == "ACTION_REJECTED"
    assert inactive(config) == 40


async def test_approved_purge_deletes_25_clients(config: Config) -> None:
    emitted = await purge(config, Answer(Decision("approve", "user")))

    result = only(emitted, events.ToolResult)
    assert result.ok and result.data["rows_affected"] == 25
    assert inactive(config) == 15  # the 15 protected clients stay
    conn = sqlite3.connect(config.database.business_path)
    assert conn.execute("SELECT COUNT(*) FROM write_audit").fetchone()[0] == 1
    conn.close()
