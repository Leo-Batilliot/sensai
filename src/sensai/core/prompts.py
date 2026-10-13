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
from collections.abc import Callable, Iterable
from importlib import resources
from pathlib import Path
from string import Template

from sensai.core.schema_summary import compact_schema

SYSTEM_SQL = "system_sql_v1.md"
EMPTY = "aucun"  # value of a placeholder with nothing to show

# Date tool of mcp-server-time (MCP-06), whatever the name of its server in config.toml
DATE_TOOL_SUFFIX = "get_current_time"
NO_DATE_TOOL = (
    "aucun outil de date n'est disponible : n'exécute pas de requête, dis-le et demande "
    "la période exacte à l'utilisateur."
)

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
    return system_prompt_template(db_path, tool_names)(memory)


def system_prompt_template(db_path: str | Path, tool_names: Iterable[str]) -> Callable[[str], str]:
    """The system prompt as a function of the memory block of a turn (MEM-04), for
    Engine(system_prompt=...). The schema is read once, not at every turn."""
    schema = compact_schema(db_path)
    names = list(tool_names)
    tools_hint = ", ".join(names) or EMPTY
    rule = date_rule(names)

    def build(memory: str = "") -> str:
        return render(
            SYSTEM_SQL, schema=schema, tools_hint=tools_hint, date_rule=rule, memory=memory or EMPTY
        )

    return build


def date_rule(tool_names: Iterable[str]) -> str:
    """How to get today's date, decided here rather than by the model (MCP-06).

    A small model calls a tool much more reliably when the prompt names it, and it
    misreads a rule like "if no date tool is available". So the prompt either names the
    date tool, or says there is none.
    """
    date_tools = [name for name in tool_names if name.endswith(DATE_TOOL_SUFFIX)]
    if not date_tools:
        return NO_DATE_TOOL
    return (
        f"appelle d'abord l'outil `{date_tools[0]}` (fuseau Europe/Paris) et calcule la "
        "période à partir de la date qu'il renvoie."
    )
