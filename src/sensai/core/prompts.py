"""System prompts (CORE-08): Markdown templates in core/prompts/, filled with string.Template.

Prompt text lives in versioned .md files, never in the code. string.Template only reads
`$name` placeholders, so the braces of SQL or JSON in a prompt are left alone, whereas
str.format would take them for fields.

The templates are package data (pyproject.toml), read with importlib.resources so that
they are found in an installed package too. The core/prompts/ folder has no
__init__.py: it is not a package, so this module is what `sensai.core.prompts` imports.

Versions (A4, PROMPT-01, docs/contracts/prompts.md §1): core/prompts/registry.toml lists
every published version of a prompt with the fingerprint of its file. A published file
never changes; [agent] prompt_version picks the version, "v1" being the way back.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from string import Template

from sensai.core.errors import PromptVersionNotFound
from sensai.core.schema_summary import compact_schema

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: tomllib is not in the stdlib yet
    import tomli as tomllib

SYSTEM_SQL = "system_sql_v1.md"
SYSTEM_SQL_NAME = "system_sql"
DEFAULT_VERSION = "v1"  # becomes "v2" with CORE-14 (prompts.md, point PR-2)
REGISTRY_FILE = "registry.toml"
EMPTY = "aucun"  # value of a placeholder with nothing to show

# Holes filled once per engine, and holes filled at every turn (prompts.md §1.1, rule 3)
FIXED_HOLES = frozenset({"schema", "tools_hint", "date_rule"})
TURN_HOLES = frozenset({"memory", "examples", "document_outline", "persona"})  # persona: A5, v2

# Date tool of mcp-server-time (MCP-06), whatever the name of its server in config.toml
DATE_TOOL_SUFFIX = "get_current_time"
NO_DATE_TOOL = (
    "aucun outil de date n'est disponible : n'exécute pas de requête, dis-le et demande "
    "la période exacte à l'utilisateur."
)

# $name or ${name}; "$$" is a literal dollar sign and is removed before searching
_PLACEHOLDER = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
_VERSION = re.compile(r"^v[0-9]+$")


def load(name: str) -> str:
    """Text of the template core/prompts/<name>."""
    return resources.files("sensai.core").joinpath("prompts", name).read_text(encoding="utf-8")


def fingerprint(data: bytes) -> str:
    """SHA-256 of a prompt file. CRLF is read as LF: a file edited outside git keeps its
    fingerprint (the repository stores LF, .gitattributes)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


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


# --- registry (A4) ----------------------------------------------------------------------


@dataclass(frozen=True)
class PromptVersion:
    """One [[prompt]] entry of registry.toml."""

    name: str
    version: str
    file: str
    created: str
    parent: str
    sha256: str
    notes: str

    def info(self) -> dict[str, str]:
        """What a harness report records about its prompt (judge.md §5)."""
        return {
            "name": self.name,
            "version": self.version,
            "file": self.file,
            "sha256": self.sha256,
        }

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class RegistryError(Exception):
    """registry.toml does not match the prompt files. The message lists every problem."""


class PromptRegistry:
    """Published prompt versions, in the order of registry.toml."""

    def __init__(self, entries: Sequence[PromptVersion]) -> None:
        self.entries = list(entries)

    @classmethod
    def load(cls, text: str | None = None) -> PromptRegistry:
        """The registry of the package (or of `text`), checked: RegistryError if a
        published file changed, is missing, or its entry is malformed."""
        if text is None:
            text = load(REGISTRY_FILE)
        try:
            rows = tomllib.loads(text).get("prompt", [])
            registry = cls([PromptVersion(**row) for row in rows])
        except (tomllib.TOMLDecodeError, TypeError) as exc:
            raise RegistryError(f"{REGISTRY_FILE} illisible : {exc}") from exc
        problems = registry.problems()
        if problems:
            raise RegistryError(f"{REGISTRY_FILE} :\n" + "\n".join(f"  - {p}" for p in problems))
        return registry

    def problems(self) -> list[str]:
        """Invariants of prompts.md §1.1, in French; [] when the registry is sound."""
        found: list[str] = []
        seen: set[tuple[str, str]] = set()
        for entry in self.entries:
            label = f"{entry.name} {entry.version}"
            if not _VERSION.match(entry.version):
                found.append(f"{label} : version attendue de la forme v1, v2…")
            if (entry.name, entry.version) in seen:
                found.append(f"{label} : version publiée deux fois")
            seen.add((entry.name, entry.version))
            if entry.file != f"{entry.name}_{entry.version}.md":
                found.append(f"{label} : le fichier doit s'appeler {entry.name}_{entry.version}.md")
            if entry.parent and (entry.name, entry.parent) not in seen:
                found.append(f"{label} : parent {entry.parent} inconnu ou publié après")
            try:
                data = resources.files("sensai.core").joinpath("prompts", entry.file).read_bytes()
            except FileNotFoundError:
                found.append(f"{label} : fichier {entry.file} absent")
                continue
            if fingerprint(data) != entry.sha256:
                found.append(
                    f"{label} : {entry.file} a changé depuis sa publication (empreinte "
                    f"{fingerprint(data)[:12]}…) ; publiez une nouvelle version"
                )
            unknown = placeholders(data.decode("utf-8")) - FIXED_HOLES - TURN_HOLES
            if unknown:
                found.append(f"{label} : trous inconnus du moteur {sorted(unknown)}")
        return found

    def versions(self, name: str = SYSTEM_SQL_NAME) -> list[PromptVersion]:
        return [entry for entry in self.entries if entry.name == name]

    def get(self, version: str, name: str = SYSTEM_SQL_NAME) -> PromptVersion:
        for entry in self.versions(name):
            if entry.version == version:
                return entry
        raise PromptVersionNotFound(name, version, [e.version for e in self.versions(name)])


def check_version(version: str, name: str = SYSTEM_SQL_NAME) -> PromptVersion:
    """The configured version, checked at startup: PromptVersionNotFound if unknown."""
    return PromptRegistry.load().get(version, name)


# --- the system prompt of a turn -------------------------------------------------------


class SystemPrompt:
    """The system prompt of one version, for Engine(system_prompt=...).

    The schema, the tools and the date rule are filled once; the blocks of a turn
    (memory, examples, plan of the note) at each call. `holes` says which of those blocks
    this version shows, so that the engine only computes them when they are used: the v1
    has no $examples, so no example is selected for it.
    """

    def __init__(self, text: str, fixed: dict[str, str], version: PromptVersion | None = None):
        holes = placeholders(text)
        unknown = holes - FIXED_HOLES - TURN_HOLES
        if unknown:
            raise ValueError(f"trous inconnus du moteur : {sorted(unknown)}")
        self.text = text
        self.version = version
        self.fixed = {hole: value for hole, value in fixed.items() if hole in holes}
        self.holes = frozenset(holes & TURN_HOLES)

    def __call__(
        self, memory: str = "", tool_names: Iterable[str] | None = None, **blocks: str
    ) -> str:
        """`tool_names`: the tools of the active profile (A5), which the prompt names
        instead of all of them; None keeps the tools given at construction."""
        values = {"memory": memory, **blocks}
        unknown = set(values) - TURN_HOLES
        if unknown:
            raise ValueError(f"blocs inconnus : {sorted(unknown)}")
        turn = {hole: values.get(hole) or EMPTY for hole in self.holes}
        fixed = dict(self.fixed)
        if tool_names is not None:
            names = list(tool_names)
            narrowed = {"tools_hint": ", ".join(names) or EMPTY, "date_rule": date_rule(names)}
            fixed.update({hole: value for hole, value in narrowed.items() if hole in fixed})
        return Template(self.text).substitute({**fixed, **turn})


def system_prompt(db_path: str | Path, tool_names: Iterable[str], memory: str = "") -> str:
    """The text-to-SQL system prompt, for the database at db_path and the given tools."""
    return system_prompt_template(db_path, tool_names)(memory)


def system_prompt_template(
    db_path: str | Path,
    tool_names: Iterable[str],
    version: str = DEFAULT_VERSION,
    registry: PromptRegistry | None = None,
) -> SystemPrompt:
    """The system prompt of a version as a function of the blocks of a turn (MEM-04).
    The schema is read once, not at every turn. PromptVersionNotFound if unknown."""
    entry = (registry or PromptRegistry.load()).get(version)
    names = list(tool_names)
    fixed = {
        "schema": compact_schema(db_path),
        "tools_hint": ", ".join(names) or EMPTY,
        "date_rule": date_rule(names),
    }
    return SystemPrompt(load(entry.file), fixed, entry)


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
