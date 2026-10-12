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
    assert placeholders(load(SYSTEM_SQL)) == {"schema", "tools_hint", "memory"}


def test_render_fills_every_placeholder() -> None:
    text = render(SYSTEM_SQL, schema="<SCHEMA>", tools_hint="<OUTILS>", memory="<MEMOIRE>")

    assert "<SCHEMA>" in text and "<OUTILS>" in text and "<MEMOIRE>" in text
    assert not LEFTOVER.search(text)


def test_sql_in_the_prompt_is_left_alone() -> None:
    # string.Template only reads $name: strftime('%m', ...) and braces stay as written
    text = render(SYSTEM_SQL, schema="{ }", tools_hint="t", memory="m")

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
