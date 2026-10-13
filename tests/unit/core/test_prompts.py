"""System prompt templates (CORE-08)."""

import re
from pathlib import Path

import pytest

from sensai.core.prompts import SYSTEM_SQL, load, placeholders, render, system_prompt
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
