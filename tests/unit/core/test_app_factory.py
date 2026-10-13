"""Assembly of the application (CORE-10): Services shared by the sessions, one Engine each."""

from pathlib import Path

import pytest

from fakes import FakeClient
from sensai.core.app_factory import Services
from sensai.core.config import Config, DatabaseConfig, McpConfig, McpServerConfig
from sensai.data.schema import connect, create_schema
from sensai.memory.context import MemoryContext
from sensai.tools.registry import Registry


@pytest.fixture
def config(tmp_path: Path) -> Config:
    business = tmp_path / "business.db"
    conn = connect(business)
    create_schema(conn)
    conn.close()
    return Config(
        database=DatabaseConfig(business_path=business, memory_path=tmp_path / "memory.db"),
        mcp=McpConfig(servers={}),
    )


def services_for(config: Config) -> tuple[Services, list[FakeClient]]:
    clients: list[FakeClient] = []

    def client_factory(_config: Config) -> FakeClient:
        clients.append(FakeClient())
        return clients[-1]

    return Services(config, client_factory=client_factory), clients


async def test_an_engine_gets_tools_memory_judge_and_prompt(config: Config) -> None:
    services, clients = services_for(config)
    await services.start()
    try:
        engine = services.new_engine(session_id="s-1")
    finally:
        await services.stop()

    assert isinstance(engine.registry, Registry)
    assert "memory_delete" in [info.name for info in engine.registry.tools()]
    assert isinstance(engine.memory, MemoryContext)
    assert engine.judge is not None
    assert engine.client is clients[0]
    assert callable(engine.system_prompt)
    assert "clients" in engine.system_prompt("")  # the schema of the business database
    assert engine.session_id == "s-1"


async def test_sessions_share_the_services_but_not_their_engine(config: Config) -> None:
    services, clients = services_for(config)
    await services.start()
    try:
        first, second = services.new_engine(), services.new_engine()
    finally:
        await services.stop()

    assert first.client is not second.client and len(clients) == 2
    assert first.registry is not second.registry
    assert first.memory is not None and second.memory is not None
    assert first.memory.store is second.memory.store  # one memory.db for the application


async def test_a_server_that_cannot_start_does_not_stop_the_others(config: Config) -> None:
    broken = McpServerConfig(command="commande-introuvable-sensai", args=())
    config = config.model_copy(update={"mcp": McpConfig(servers={"broken": broken})})
    services, _clients = services_for(config)

    await services.start()
    try:
        engine = services.new_engine()
        assert engine.registry is not None
        (status,) = engine.registry.servers()
    finally:
        await services.stop()

    assert (status.name, status.status) == ("broken", "down")
    assert status.error is not None and "introuvable" in status.error


async def test_an_engine_needs_started_services(config: Config) -> None:
    services, _clients = services_for(config)

    with pytest.raises(RuntimeError, match="start"):
        services.new_engine()


async def test_stop_closes_the_memory(config: Config) -> None:
    services, _clients = services_for(config)
    await services.start()

    await services.stop()

    with pytest.raises(RuntimeError):
        _ = services.store
