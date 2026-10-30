"""Agent profiles (A5, PERS-01): the files, the tools of a profile, a change in session."""

import contextlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from event_checks import check_turn
from fakes import (
    PERSONAS_DIR,
    FakeClient,
    FakeTool,
    FakeToolRegistry,
    answer,
    select_tool,
    time_tool,
    tool_request,
)
from sensai.core import events
from sensai.core.config import AgentConfig, Config
from sensai.core.engine import Engine
from sensai.core.errors import PersonaNotFound
from sensai.core.personas import Persona, PersonaError, load_personas
from sensai.core.prompts import system_prompt_template
from sensai.data.schema import connect, create_schema
from sensai.mcp.client import ToolAnnotations
from sensai.tools.base import ToolInfo, ToolResult

EXECUTE = "sql_toolbox__execute_write"
PERSONAS = load_personas(PERSONAS_DIR)
ADV_NOTE = {"role": "system", "content": "Profil actif : ADV (Copilote de Karim, responsable ADV)"}

# A valid profile file, broken one way per test
VALID = """id = "compta"
name = "Comptabilité"
role = "Copilote de la comptabilité"
tone = "Précis."
scope = "Factures et paiements."

[tools]
allow = ["sql_toolbox__run_select"]
"""


def execute_tool() -> tuple[FakeTool, list[dict[str, Any]]]:
    """execute_write of sql_toolbox, recording its calls: the list stays empty if nothing ran."""
    calls: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any]) -> ToolResult:
        calls.append(arguments)
        return ToolResult(ok=True, text="1 ligne supprimée", summary="1 ligne")

    info = ToolInfo(
        name=EXECUTE,
        server="sql_toolbox",
        original_name="execute_write",
        description="Exécute une écriture en transaction",
        input_schema={"type": "object", "properties": {"sql": {"type": "string"}}},
        annotations=ToolAnnotations(read_only=False, destructive=True),
        trusted=True,
    )
    return FakeTool(info, handler), calls


def engine(
    *replies: list[Any], persona: str = "analyste", system_prompt: Any = "Tu es l'assistant."
) -> tuple[Engine, FakeClient, list[dict[str, Any]]]:
    """An Engine with the profiles of personas/, on a time tool, run_select and execute_write."""
    execute, calls = execute_tool()
    client = FakeClient(*replies)
    built = Engine(
        Config(agent=AgentConfig(persona=persona)),
        client,
        registry=FakeToolRegistry(time_tool(), select_tool(), execute),
        system_prompt=system_prompt,
        session_id="s-1",
        sql_classifier=lambda sql: None,
        personas=PERSONAS,
    )
    return built, client, calls


async def run(engine: Engine, question: str = "Question") -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(question)) as turn:
        emitted = [event async for event in turn]
    check_turn(emitted)
    return emitted


def offered(tools: list[dict[str, Any]] | None) -> list[str]:
    return [tool["function"]["name"] for tool in tools or []]


def tool_error(emitted: list[events.Event]) -> events.ToolError:
    (result,) = [event for event in emitted if isinstance(event, events.ToolResult)]
    assert result.error is not None
    return result.error


# --- files of personas/ -------------------------------------------------------------------


def test_the_three_profiles_of_the_scenario() -> None:
    assert list(PERSONAS) == ["admin", "adv", "analyste"]  # order of the file names
    analyste, adv, admin = PERSONAS["analyste"], PERSONAS["adv"], PERSONAS["admin"]
    assert analyste.label == "Contrôle de gestion (Copilote de Claire, contrôleuse de gestion)"
    # Claire reads, Karim writes in the database, Inès writes files
    assert not analyste.allows(EXECUTE) and not analyste.allows("file_write")
    assert adv.allows(EXECUTE) and adv.allows("sql_toolbox__preview_write")
    assert not adv.allows("file_write")
    assert admin.allows("file_write") and not admin.allows(EXECUTE)
    for persona in PERSONAS.values():
        for tool in ("sql_toolbox__run_select", "time__get_current_time", "memory_search"):
            assert persona.allows(tool), (persona.id, tool)
        assert len(persona.prompt_block()) < 1200  # a fixed budget in the prompt (M2)


def test_allow_then_deny() -> None:
    persona = Persona.model_validate(
        {
            "id": "memoire",
            "name": "Mémoire",
            "role": "Test",
            "tone": "Bref.",
            "scope": "La mémoire.",
            "tools": {"allow": ["memory_*"], "deny": ["memory_delete"]},
        }
    )
    assert persona.allows("memory_search")
    assert not persona.allows("memory_delete")  # deny wins
    assert not persona.allows("file_read")  # matches no allow pattern
    assert not persona.allows("MEMORY_search")  # case-sensitive on every system


def test_a_new_file_is_a_new_persona(tmp_path: Path) -> None:
    folder = tmp_path / "personas"
    shutil.copytree(PERSONAS_DIR, folder)
    (folder / "compta.toml").write_text(VALID, encoding="utf-8")

    personas = load_personas(folder)

    assert list(personas) == ["admin", "adv", "analyste", "compta"]
    assert personas["compta"].label == "Comptabilité (Copilote de la comptabilité)"
    assert "Consignes" not in personas["compta"].prompt_block()  # instructions are optional


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (VALID.replace('name = "Comptabilité"\n', ""), "name"),
        (VALID.replace('["sql_toolbox__run_select"]', '[""]'), "tools.allow.0"),
        (VALID.replace('["sql_toolbox__run_select"]', "[]"), "tools.allow"),
        ('couleur = "bleu"\n' + VALID, "couleur"),
        (VALID.replace('"Précis."', '"' + "x" * 301 + '"'), "tone"),
        (VALID.replace('"compta"', '"Compta générale"'), "id"),
        ("id = ", "TOML invalide"),
    ],
    ids=[
        "missing-field",
        "empty-pattern",
        "no-tool",
        "unknown-key",
        "over-the-prompt-budget",
        "invalid-id",
        "not-toml",
    ],
)
def test_invalid_file_is_reported(tmp_path: Path, content: str, expected: str) -> None:
    (tmp_path / "compta.toml").write_text(content, encoding="utf-8")
    with pytest.raises(PersonaError) as error:
        load_personas(tmp_path)
    message = str(error.value)
    assert "compta.toml" in message and expected in message


def test_two_files_with_the_same_id_are_refused(tmp_path: Path) -> None:
    (tmp_path / "compta.toml").write_text(VALID, encoding="utf-8")
    (tmp_path / "copie.toml").write_text(VALID, encoding="utf-8")
    with pytest.raises(PersonaError, match="déjà celui de"):
        load_personas(tmp_path)


def test_a_missing_or_empty_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="introuvable"):
        load_personas(tmp_path / "absent")
    with pytest.raises(PersonaError, match="Aucun profil"):
        load_personas(tmp_path)


# --- the profile of a session ------------------------------------------------------------


async def test_tools_are_filtered() -> None:
    built, client, _calls = engine(answer("Bonjour."))

    await run(built)

    assert offered(client.tools_sent[0]) == ["time__get_current_time", "sql_toolbox__run_select"]
    system = client.calls[0][1][0]
    assert system["role"] == "system"
    assert system["content"].startswith(
        "Tu es l'assistant.\n\n# Profil actif : Contrôle de gestion"
    )
    assert "Ne propose jamais de modifier les données" in system["content"]


async def test_the_prompt_names_only_the_tools_of_the_profile(tmp_path: Path) -> None:
    conn = connect(tmp_path / "business.db")
    create_schema(conn)
    conn.close()
    names = ["time__get_current_time", "sql_toolbox__run_select", EXECUTE]
    template = system_prompt_template(tmp_path / "business.db", names)
    built, client, _calls = engine(answer("Bonjour."), system_prompt=template)

    await run(built)

    prompt = client.calls[0][1][0]["content"]
    assert "Noms exacts : time__get_current_time, sql_toolbox__run_select\n" in prompt
    assert EXECUTE not in prompt
    assert "appelle d'abord l'outil `time__get_current_time`" in prompt  # the date tool is kept


async def test_a_tool_outside_the_profile_is_refused_even_if_the_model_calls_it() -> None:
    built, _client, calls = engine(
        tool_request(EXECUTE, sql="DELETE FROM clients WHERE id = 7"),
        answer("Je ne peux pas modifier les données."),
    )

    emitted = await run(built)

    error = tool_error(emitted)
    assert error.code == "TOOL_DENIED"
    assert "hors du profil « Contrôle de gestion »" in error.message
    assert calls == []  # nothing ran
    assert not any(isinstance(event, events.ConfirmationRequired) for event in emitted)


async def test_an_unknown_tool_lists_only_the_tools_of_the_profile() -> None:
    built, _client, _calls = engine(tool_request("purge_clients"), answer("Je ne peux pas."))

    error = tool_error(await run(built))

    assert error.code == "UNKNOWN_TOOL"
    assert "sql_toolbox__run_select" in error.message
    assert EXECUTE not in error.message  # never suggested to the model


async def test_switch_keeps_the_history() -> None:
    built, client, _calls = engine(answer("42 jours en moyenne."), answer("Oui, 12 clients."))
    await run(built, "Délai moyen de paiement ?")

    assert built.set_persona("adv") is PERSONAS["adv"]
    await run(built, "Peut-on purger les clients inactifs ?")

    messages = client.calls[1][1]
    assert "# Profil actif : ADV" in messages[0]["content"]
    assert messages[1:] == [
        {"role": "user", "content": "Délai moyen de paiement ?"},
        {"role": "assistant", "content": "42 jours en moyenne."},
        ADV_NOTE,
        {"role": "user", "content": "Peut-on purger les clients inactifs ?"},
    ]
    assert EXECUTE in offered(client.tools_sent[1])


async def test_a_change_during_a_turn_applies_to_the_next_one() -> None:
    built, client, _calls = engine(answer("Premier."), answer("Second."))

    async with contextlib.aclosing(built.run_turn("Q1")) as turn:
        async for event in turn:
            if isinstance(event, events.TurnStarted):
                built.set_persona("adv")  # the selector, while the turn runs

    assert EXECUTE not in offered(client.tools_sent[0])
    assert "# Profil actif : Contrôle de gestion" in client.calls[0][1][0]["content"]
    await run(built, "Q2")
    assert EXECUTE in offered(client.tools_sent[1])
    # The note comes after the exchange that the former profile answered
    assert client.calls[1][1][1:] == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "Premier."},
        ADV_NOTE,
        {"role": "user", "content": "Q2"},
    ]


async def test_several_changes_between_two_turns_give_one_note() -> None:
    built, client, _calls = engine(answer("Un."), answer("Deux."), answer("Trois."))
    await run(built, "Q1")

    built.set_persona("adv")
    built.set_persona("admin")
    await run(built, "Q2")
    built.set_persona("analyste")
    built.set_persona("admin")  # back to the profile of Q2: nothing to note
    await run(built, "Q3")

    notes = [message for message in client.calls[2][1][1:] if message["role"] == "system"]
    assert notes == [
        {"role": "system", "content": "Profil actif : Admin (Copilote d'Inès, administratrice)"}
    ]


async def test_unknown_persona_changes_nothing() -> None:
    built, _client, _calls = engine()

    with pytest.raises(PersonaNotFound) as error:
        built.set_persona("compta")

    assert error.value.code == "PERSONA_NOT_FOUND"
    assert "Profils disponibles : admin, adv, analyste" in error.value.message
    assert built.persona is PERSONAS["analyste"]


async def test_reset_keeps_the_profile() -> None:
    built, client, _calls = engine(answer("Un."), answer("Deux."))
    await run(built, "Q1")
    built.set_persona("adv")

    built.reset()
    await run(built, "Q2")

    messages = client.calls[1][1]
    assert "# Profil actif : ADV" in messages[0]["content"]
    assert messages[1:] == [{"role": "user", "content": "Q2"}]  # no note in an empty history


def test_the_starting_profile_comes_from_the_configuration() -> None:
    built, _client, _calls = engine(persona="admin")
    assert built.persona is PERSONAS["admin"]
    with pytest.raises(PersonaNotFound):
        engine(persona="compta")


async def test_without_profiles_every_tool_is_offered() -> None:
    execute, _calls = execute_tool()
    client = FakeClient(answer("Bonjour."))
    built = Engine(Config(), client, registry=FakeToolRegistry(select_tool(), execute))

    await run(built)

    assert built.persona is None
    assert offered(client.tools_sent[0]) == ["sql_toolbox__run_select", EXECUTE]
    assert client.calls[0][1] == [{"role": "user", "content": "Question"}]  # no profile block


async def test_with_prompt_v2_the_profile_fills_its_hole(tmp_path: Path) -> None:
    conn = connect(tmp_path / "business.db")
    create_schema(conn)
    conn.close()
    names = ["time__get_current_time", "sql_toolbox__run_select", EXECUTE]
    template = system_prompt_template(tmp_path / "business.db", names, "v2")
    built, client, _calls = engine(answer("Bonjour."), system_prompt=template)

    await run(built)

    prompt = client.calls[0][1][0]["content"]
    assert (
        "# Profil actif\nContrôle de gestion (Copilote de Claire, contrôleuse de gestion)\n"
        in prompt
    )
    assert prompt.count("Profil actif") == 1  # in its hole, not added again at the end
    assert prompt.rstrip().endswith("# Contexte mémorisé\naucun")
    assert EXECUTE not in prompt  # the tools of the profile only, as with v1
