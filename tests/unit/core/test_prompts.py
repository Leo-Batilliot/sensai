"""System prompt templates (CORE-08) and their registry of versions (A4, PROMPT-01)."""

import hashlib
import re
from pathlib import Path

import pytest

from sensai.core.config import Config
from sensai.core.errors import PromptVersionNotFound
from sensai.core.prompts import (
    FIXED_HOLES,
    SYSTEM_SQL,
    TURN_HOLES,
    PromptRegistry,
    RegistryError,
    SystemPrompt,
    fingerprint,
    load,
    placeholders,
    render,
    system_prompt,
    system_prompt_template,
)
from sensai.data.schema import connect, create_schema

TEMPLATES = Path(__file__).resolve().parents[3] / "src/sensai/core/prompts"
LEFTOVER = re.compile(r"\$\{?\w")


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.close()
    return path


def test_the_prompt_is_a_versioned_markdown_file() -> None:
    assert (TEMPLATES / SYSTEM_SQL).is_file()
    assert placeholders(load(SYSTEM_SQL)) == {"schema", "tools_hint", "date_rule", "memory"}


def test_render_fills_every_placeholder() -> None:
    text = render(
        SYSTEM_SQL, schema="<SCHEMA>", tools_hint="<OUTILS>", date_rule="<DATE>", memory="<MEM>"
    )

    assert all(value in text for value in ("<SCHEMA>", "<OUTILS>", "<DATE>", "<MEM>"))
    assert not LEFTOVER.search(text)


def test_sql_in_the_prompt_is_left_alone() -> None:
    # string.Template only reads $name: strftime('%m', ...) and braces stay as written
    text = render(SYSTEM_SQL, schema="{ }", tools_hint="t", date_rule="d", memory="m")

    assert "strftime('%m', J)" in text
    assert "{ }" in text


@pytest.mark.parametrize(
    "values",
    [
        {"schema": "s", "tools_hint": "t"},
        {"schema": "s", "tools_hint": "t", "memory": "m", "shema": "typo"},
    ],
    ids=["missing", "unknown"],
)
def test_render_refuses_a_missing_or_unknown_value(values: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="trous"):
        render(SYSTEM_SQL, **values)


def test_system_prompt_for_a_database_and_tools(empty_db: Path) -> None:
    text = system_prompt(empty_db, ["time__get_current_time", "sql_toolbox__run_select"])

    assert "Noms exacts : time__get_current_time, sql_toolbox__run_select" in text
    assert "orders (0 lignes) : id INTEGER PK" in text
    assert text.rstrip().endswith("# Contexte mémorisé\naucun")
    assert not LEFTOVER.search(text)


def test_the_prompt_forbids_guessing_the_date(empty_db: Path) -> None:
    # The evaluation freezes the date: SQL with date('now') would be wrong (CORE-08 pitfall)
    text = system_prompt(empty_db, [])

    assert "Ne devine jamais la date du jour" in text
    assert "n'écris jamais date('now')" in text


def test_the_prompt_names_the_date_tool_when_there_is_one(empty_db: Path) -> None:
    # The code decides, not the model: a small model calls a tool the prompt names (MCP-06)
    text = system_prompt(empty_db, ["sql_toolbox__run_select", "horloge__get_current_time"])

    assert "appelle d'abord l'outil `horloge__get_current_time`" in text
    assert "aucun outil de date" not in text


def test_without_a_date_tool_the_agent_asks_for_the_period(empty_db: Path) -> None:
    # The date server is down (uvx missing): the agent must not guess (US-T1-01)
    text = system_prompt(empty_db, ["sql_toolbox__run_select"])

    assert "aucun outil de date n'est disponible" in text
    assert "demande la période exacte à l'utilisateur" in text


# --- registry of the published versions (A4, PROMPT-01) --------------------------------


def registry_text(**changes: str) -> str:
    row = {
        "name": "system_sql",
        "version": "v1",
        "file": SYSTEM_SQL,
        "created": "2026-10-13",
        "parent": "",
        "sha256": fingerprint((TEMPLATES / SYSTEM_SQL).read_bytes()),
        "notes": "Mid-term.",
        **changes,
    }
    return "[[prompt]]\n" + "".join(f'{key} = "{value}"\n' for key, value in row.items())


def test_published_prompts_never_change() -> None:
    # US-A4-02: a published file edited without a new version fails here
    registry = PromptRegistry.load()

    assert registry.problems() == []
    for entry in registry.entries:
        assert entry.sha256 == hashlib.sha256((TEMPLATES / entry.file).read_bytes()).hexdigest()


def test_every_version_only_has_holes_the_engine_fills() -> None:
    for entry in PromptRegistry.load().entries:
        assert placeholders(load(entry.file)) <= FIXED_HOLES | TURN_HOLES, entry.version


def test_a_changed_file_is_refused() -> None:
    with pytest.raises(RegistryError, match="a changé depuis sa publication"):
        PromptRegistry.load(registry_text(sha256="0" * 64))


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"version": "2"}, "de la forme v1"),
        ({"version": "v2"}, "doit s'appeler system_sql_v2.md"),
        ({"parent": "v0"}, "parent v0 inconnu"),
        ({"file": "absent_v1.md", "name": "absent"}, "absent"),
    ],
)
def test_a_malformed_entry_is_refused(changes: dict[str, str], problem: str) -> None:
    with pytest.raises(RegistryError, match=problem):
        PromptRegistry.load(registry_text(**changes))


def test_a_version_is_published_once() -> None:
    with pytest.raises(RegistryError, match="publiée deux fois"):
        PromptRegistry.load(registry_text() + "\n" + registry_text())


def test_crlf_keeps_the_fingerprint() -> None:
    # A file edited outside git may get CRLF line endings (PROMPT-01 pitfall)
    assert fingerprint(b"a\r\nb\r\n") == fingerprint(b"a\nb\n")


def test_version_is_chosen_by_config(empty_db: Path) -> None:
    # US-A4-02: [agent] prompt_version picks the file of the registry
    template = system_prompt_template(empty_db, [], Config().agent.prompt_version)

    assert template.version is not None
    assert (template.version.version, template.version.file) == ("v1", SYSTEM_SQL)


def test_unknown_version_is_refused(empty_db: Path) -> None:
    with pytest.raises(PromptVersionNotFound, match="v9") as exc:
        system_prompt_template(empty_db, [], "v9")

    assert exc.value.code == "PROMPT_VERSION_NOT_FOUND"
    assert "v1" in exc.value.message  # the published versions are listed


def test_the_v1_shows_the_memory_but_no_examples_nor_note(empty_db: Path) -> None:
    template = system_prompt_template(empty_db, [])

    assert template.holes == {"memory"}
    assert template("<MEM>").rstrip().endswith("# Contexte mémorisé\n<MEM>")


def test_turn_blocks_fill_the_holes_of_a_version() -> None:
    template = SystemPrompt(
        "$schema\n$memory\n$examples\n$document_outline", {"schema": "S", "date_rule": "unused"}
    )

    assert template.holes == {"memory", "examples", "document_outline"}
    assert template("M", examples="E") == "S\nM\nE\naucun"
    with pytest.raises(ValueError, match="blocs inconnus"):
        template("M", mood="P")  # persona is a known block since prompt v2 (CORE-14)


def test_a_hole_the_engine_cannot_fill_is_refused() -> None:
    with pytest.raises(ValueError, match="trous inconnus"):
        SystemPrompt("$schema $mood", {"schema": "S"})


# --- prompt v2 (CORE-14) ---


def test_the_v2_descends_from_the_v1() -> None:
    v2 = PromptRegistry.load().get("v2")

    assert (v2.parent, v2.file) == ("v1", "system_sql_v2.md")


def test_the_v2_shows_the_profile_the_examples_and_the_note(empty_db: Path) -> None:
    template = system_prompt_template(empty_db, ["sql_toolbox__run_select"], "v2")

    assert template.holes == {"memory", "examples", "document_outline", "persona"}
    text = template("<MEM>", persona="<PROFIL>", examples="<EX>", document_outline="<NOTE>")
    assert "# Profil actif\n<PROFIL>\n" in text
    assert "# Exemples proches de la question\n<EX>\n" in text
    assert "# Note du comité\n<NOTE>\n" in text
    assert text.rstrip().endswith("# Contexte mémorisé\n<MEM>")
    assert LEFTOVER.search(text.replace("$$", "")) is None


def test_the_v2_without_profile_nor_examples_says_so(empty_db: Path) -> None:
    text = system_prompt_template(empty_db, [], "v2")("")

    assert "# Profil actif\naucun\n" in text  # the harness runs without a profile
    assert "# Exemples proches de la question\naucun\n" in text


# --- prompt v3: explicit write rule (30/10) ---


def test_the_v3_descends_from_the_v2() -> None:
    v3 = PromptRegistry.load().get("v3")

    assert (v3.parent, v3.file) == ("v2", "system_sql_v3.md")


def test_the_v3_only_changes_the_write_rule_of_the_v2() -> None:
    # qwen3:8b stopped after the preview and asked in text: the modal never opened (act 4)
    v2, v3 = load("system_sql_v2.md").splitlines(), load("system_sql_v3.md").splitlines()

    changed = [(old, new) for old, new in zip(v2, v3, strict=True) if old != new]
    assert len(changed) == 1
    old, new = changed[0]
    assert old.startswith("7. Pour modifier des données") and new.startswith(
        "7. Pour modifier des données"
    )
    assert "Ne demande jamais de confirmation dans ta réponse" in new
    assert "fenêtre de confirmation" in new
    assert placeholders(load("system_sql_v3.md")) == placeholders(load("system_sql_v2.md"))


# --- prompt v4: direct write (30/10) ---


def test_the_v4_descends_from_the_v3() -> None:
    v4 = PromptRegistry.load().get("v4")

    assert (v4.parent, v4.file) == ("v3", "system_sql_v4.md")


def test_the_v4_writes_without_its_own_preview() -> None:
    # With the preview in hand, qwen3:8b still asked in text (live act 4 in v3): the engine
    # computes the preview of the confirmation itself (Engine._impact), so the model skips it
    v3, v4 = load("system_sql_v3.md").splitlines(), load("system_sql_v4.md").splitlines()

    changed = [(old, new) for old, new in zip(v3, v4, strict=True) if old != new]
    assert len(changed) == 1
    _old, new = changed[0]
    assert new.startswith("7. Pour modifier des données : appelle directement l'outil d'écriture")
    assert "sans appeler l'outil d'aperçu avant" in new
    assert "fenêtre de confirmation" in new
    assert placeholders(load("system_sql_v4.md")) == placeholders(load("system_sql_v3.md"))
