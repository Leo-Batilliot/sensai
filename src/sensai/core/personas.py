"""Agent profiles (A5, PERS-01): who the agent works for, how it answers, which tools it may use.

One TOML file per profile in [agent] personas_dir (docs/contracts/v1.1-extensions.md §3.7).
A file is checked once, at startup: an invalid one stops the application with a message
that names the file and the field.

A profile only restricts. Its tools are kept out of the list the model sees, and the
policy refuses the others even if the model makes up their name (tools/policy.py). It can
never remove a confirmation, change [hitl], or allow a tool of [hitl] deny (ADR-009).

Its text (role, tone, scope, instructions) goes into the system prompt: every field has a
maximum length, so that the block stays under ~300 tokens whatever the file says (M2).
"""

from __future__ import annotations

import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from sensai.core.config import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: tomllib is not in the stdlib yet
    import tomli as tomllib

Pattern = Annotated[str, StringConstraints(min_length=1)]


class PersonaError(ConfigError):
    """An invalid profile file or folder, found at startup. Shown to the user as is."""


class ToolPatterns(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # fnmatch patterns on the names the model sees: "sql_toolbox__run_select", "memory_*"
    allow: tuple[Pattern, ...] = Field(min_length=1)
    deny: tuple[Pattern, ...] = ()


class Persona(BaseModel):
    """One file of personas/: the profile of v1.1-extensions.md §3.7."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=80)
    tone: str = Field(min_length=1, max_length=300)
    scope: str = Field(min_length=1, max_length=300)
    instructions: str = Field(default="", max_length=400)
    tools: ToolPatterns

    @property
    def label(self) -> str:
        """What the history and the CLI show: 'ADV (Copilote de Karim, responsable ADV)'."""
        return f"{self.name} ({self.role})"

    def allows(self, tool_name: str) -> bool:
        """True when the tool matches an `allow` pattern and no `deny` pattern."""
        allowed = any(fnmatchcase(tool_name, pattern) for pattern in self.tools.allow)
        return allowed and not any(fnmatchcase(tool_name, pattern) for pattern in self.tools.deny)

    def prompt_block(self) -> str:
        """The profile in the system prompt ($persona of prompt v2, CORE-14)."""
        lines = [
            f"# Profil actif : {self.name}",
            f"Rôle : {self.role}",
            f"Ton : {self.tone}",
            f"Périmètre : {self.scope}",
        ]
        if self.instructions:
            lines.append(f"Consignes : {self.instructions}")
        return "\n".join(lines)


def load_personas(directory: Path) -> dict[str, Persona]:
    """Every profile of the folder, by id, in the order of the file names."""
    if not directory.is_dir():
        raise PersonaError(f"Dossier des profils introuvable : {directory} ([agent] personas_dir).")
    personas: dict[str, Persona] = {}
    files: dict[str, Path] = {}
    for path in sorted(directory.glob("*.toml")):
        persona = _read(path)
        if persona.id in personas:
            raise PersonaError(
                f"{path} : l'id « {persona.id} » est déjà celui de {files[persona.id]}."
            )
        personas[persona.id] = persona
        files[persona.id] = path
    if not personas:
        raise PersonaError(f"Aucun profil dans {directory} : ajoutez un fichier <id>.toml.")
    return personas


def _read(path: Path) -> Persona:
    try:
        with path.open("rb") as handle:  # tomllib and tomli both require a binary handle
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise PersonaError(f"{path} : TOML invalide : {exc}") from exc
    except OSError as exc:
        raise PersonaError(f"{path} : lecture impossible : {exc.strerror}") from exc
    try:
        return Persona.model_validate(data)
    except ValidationError as exc:
        lines = [
            f"  - {'.'.join(str(part) for part in error['loc']) or '(racine)'} : {error['msg']}"
            for error in exc.errors()
        ]
        raise PersonaError(f"Profil invalide ({path}) :\n" + "\n".join(lines)) from exc
