"""System prompts (CORE-08): Markdown templates in core/prompts/, filled with string.Template.

Prompt text lives in versioned .md files, never in the code. string.Template only reads
`$name` placeholders, so the braces of SQL or JSON in a prompt are left alone, whereas
str.format would take them for fields.

The templates are package data (pyproject.toml), read with importlib.resources so that
they are found in an installed package too. The core/prompts/ folder has no
__init__.py: it is not a package, so this module is what `sensai.core.prompts` imports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from string import Template

from sensai.core.schema_summary import compact_schema

SYSTEM_SQL = "system_sql_v1.md"
EMPTY = "aucun"  # value of a placeholder with nothing to show

# $name or ${name}; "$$" is a literal dollar sign and is removed before searching
_PLACEHOLDER = re.compile(r"\$(?:(\w+)|\{(\w+)\})")


def load(name: str) -> str:
    """Text of the template core/prompts/<name>."""
    return resources.files("sensai.core").joinpath("prompts", name).read_text(encoding="utf-8")


def placeholders(text: str) -> set[str]:
    return {plain or braced for plain, braced in _PLACEHOLDER.findall(text.replace("$$", ""))}


def render(name: str, **values: str) -> str:
    """Fill a template. Every placeholder needs a value and every value a placeholder,
    so that a typo never sends a raw `$schema` to the model."""
    text = load(name)
    expected = placeholders(text)
    if set(values) != expected:
        raise ValueError(f"{name} : trous {sorted(expected)}, valeurs {sorted(values)}")
    return Template(text).substitute(values)


def system_prompt(db_path: str | Path, tool_names: Iterable[str], memory: str = "") -> str:
    """The text-to-SQL system prompt, for the database at db_path and the given tools."""
    return render(
        SYSTEM_SQL,
        schema=compact_schema(db_path),
        tools_hint=", ".join(tool_names) or EMPTY,
        memory=memory or EMPTY,
    )
