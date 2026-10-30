"""Assembly of the application (CORE-10): Services shared by the sessions, one Engine each."""

from pathlib import Path

import pytest

from fakes import PERSONAS_DIR, FakeClient
from sensai.core.app_factory import Services
from sensai.core.config import AgentConfig, Config, DatabaseConfig, McpConfig, McpServerConfig
from sensai.core.personas import PersonaError
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
        agent=AgentConfig(personas_dir=PERSONAS_DIR),
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


async def test_an_engine_starts_with_the_profile_of_the_configuration(config: Config) -> None:
    services, _clients = services_for(config)
    await services.start()
    try:
        first, second = services.new_engine(), services.new_engine()
    finally:
        await services.stop()

    assert list(first.personas) == ["admin", "adv", "analyste"]
    assert first.persona is not None and first.persona.id == "analyste"
    first.set_persona("adv")  # one session changes profile, not the others
    assert second.persona is not None and second.persona.id == "analyste"


def test_an_unknown_starting_profile_stops_the_application(config: Config) -> None:
    agent = config.agent.model_copy(update={"persona": "compta"})
    with pytest.raises(PersonaError, match="compta"):
        Services(config.model_copy(update={"agent": agent}))


async def test_an_engine_gets_the_note_and_the_examples(config: Config) -> None:
    # M4 and A7: one note and one example index for the whole application
    services, _ = services_for(config)
    await services.start()
    try:
        engine = services.new_engine(session_id="s-1")
        names = [info.name for info in engine.registry.tools()]
        impact = await engine.registry.preview("document_delete_section", {"section_id": "actions"})
        shared = engine.documents is services.documents and engine.examples is services.examples
    finally:
        await services.stop()

    assert {"document_read", "document_patch", "document_delete_section"} <= set(names)
    assert "document_restore" not in names  # interface only (memory.md §7.3)
    assert shared and engine.examples is not None
    assert impact is not None and impact["per_table"] == {"document_sections": -1}
