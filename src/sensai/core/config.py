"""SENSAI configuration: built-in defaults < config.toml < SENSAI_* env vars < CLI overrides.

Interface for the adapters (CLI in CORE-04, web in WEB-04)::

    config = load_config(args.config, overrides={
        "ollama.model": args.model,   # --model
        "ollama.host": args.host,     # --host
    })

- ``path``: explicit file (``--config``). When None, ``$SENSAI_CONFIG`` is used, then
  ``./config.toml`` if it exists, otherwise the built-in defaults only. An explicit
  path (argument or ``$SENSAI_CONFIG``) that does not exist is an error.
- ``overrides``: dotted keys (``"section.key"``). None values are skipped, so argparse
  options that default to None can be passed straight through.
- Environment: ``SENSAI_<SECTION>_<KEY>`` for every scalar or list key
  (``SENSAI_OLLAMA_MODEL``, ``SENSAI_HITL_DENY=a,b``). Ollama's own ``OLLAMA_HOST`` is
  deliberately ignored: its format (``0.0.0.0:11434``, no scheme) is not a URL.

Every problem raises ``ConfigError`` with a message meant for the user. The returned
``Config`` is frozen: derive a modified copy with ``config.model_copy(update=...)``
(e.g. per-session settings changed by ``set_config`` over WebSocket).
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: tomllib is not in the stdlib yet
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("config.toml")
CONFIG_PATH_ENV = "SENSAI_CONFIG"
ENV_PREFIX = "SENSAI_"

# Server names prefix tool names (``time__get_current_time``) sent to Ollama, whose
# function names only accept [A-Za-z0-9_-]. A double underscore would make the
# prefix ambiguous.
_SERVER_NAME = re.compile(r"^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$")
# A scheduled task: its id is in the WebSocket messages and the report file name
_SCHEDULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# "MON 07:00" ... "SUN 07:00", or "DAILY 07:00": local time of the machine
SCHEDULE_AT = re.compile(r"^(MON|TUE|WED|THU|FRI|SAT|SUN|DAILY) ([01][0-9]|2[0-3]):([0-5][0-9])$")


class ConfigError(Exception):
    """Invalid or unreadable configuration. The message is shown to the user as is."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OllamaConfig(_Section):
    host: str = "http://localhost:11434"
    model: str = Field(default="qwen3:8b", min_length=1)
    judge_model: str = ""
    timeout_s: float = Field(default=120, gt=0)
    keep_alive: str = "30m"
    # Same bounds as set_config in docs/contracts/websocket.md §4.6. 16384: the prompt and
    # the thinking of qwen3:8b do not fit in 8192 (CORE-12, decisions.md D-09)
    num_ctx: int = Field(default=16384, ge=2048, le=32768)
    # Tokens one model call may generate, thinking included. Ollama has no limit: past
    # num_ctx it drops half of the context and goes on, so a thinking loop never ends
    num_predict: int = Field(default=8192, ge=256, le=32768)

    @field_validator("host")
    @classmethod
    def _host_is_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("doit commencer par http:// ou https://")
        return value.rstrip("/")

    @property
    def effective_judge_model(self) -> str:
        """Model used by the judge: ``judge_model``, or the agent model when empty."""
        return self.judge_model or self.model


class AgentConfig(_Section):
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_tool_iterations: int = Field(default=6, ge=1)
    # Size of a tool result as sent to the model (the UI still gets the full result)
    max_result_rows: int = Field(default=50, ge=1)
    max_result_chars: int = Field(default=8000, ge=500)
    max_sql_retries: int = Field(default=2, ge=0)
    language: str = Field(default="fr", min_length=1)
    # Clarifying questions (ask_user): beyond the limit the call fails, without an answer
    # in time the model makes an explicit assumption
    max_questions_per_turn: int = Field(default=1, ge=0)
    question_timeout_s: float = Field(default=120, gt=0)
    # Version of the system prompt in core/prompts/registry.toml (A4); "v1" is the way back
    prompt_version: str = Field(default="v1", pattern=r"^v[0-9]+$")
    # Agent profile of a new session (A5): personas/<id>.toml, relative to the launch directory
    persona: str = Field(default="analyste", min_length=1)
    personas_dir: Path = Path("personas")


class ContextConfig(_Section):
    # Token budget of the prompt (M2, CORE-13): over budget_ratio × num_ctx, the oldest
    # exchanges are summarized ("compress") or dropped ("truncate", the witness of EVAL-10)
    strategy: Literal["compress", "truncate"] = "compress"
    budget_ratio: float = Field(default=0.75, gt=0, lt=1)  # the rest: answer and tool results
    keep_recent_turns: int = Field(default=4, ge=0)  # recent exchanges never summarized
    summary_max_tokens: int = Field(default=500, ge=50)


class DatabaseConfig(_Section):
    business_path: Path = Path("data/business.db")
    memory_path: Path = Path("data/memory.db")
    row_limit: int = Field(default=200, ge=1)
    query_timeout_ms: int = Field(default=5000, ge=1)


class HitlConfig(_Section):
    confirmation_timeout_s: float = Field(default=120, gt=0)
    always_confirm: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


class EvalConfig(_Section):
    judge_enabled: bool = True
    numeric_tolerance: float = Field(default=0.01, ge=0, lt=1)


class FewshotConfig(_Section):
    # Dynamic examples (A7): the k closest examples of the bank go into the prompt
    enabled: bool = True
    k: int = Field(default=3, ge=0)
    min_score: float = Field(default=0.0, ge=0)  # bm25 score; 0 = always the k best


class DocumentsConfig(_Section):
    # Committee note (M4): sections of a new note, from documents/templates/<template>.toml
    template: str = Field(default="note_comite", pattern=r"^[a-z0-9_]+$")


class McpServerConfig(_Section):
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()


def _default_mcp_servers() -> dict[str, McpServerConfig]:
    # The defaults ship the two servers of the demo, so `sensai chat` works without a
    # config.toml. A server that fails to start is marked unavailable by the McpManager.
    return {
        "sql_toolbox": McpServerConfig(
            command="python",
            # The database comes from [database] business_path (added by the McpManager)
            args=("-m", "sensai.mcp_servers.sql_toolbox"),
        ),
        "time": McpServerConfig(
            command="uvx", args=("mcp-server-time", "--local-timezone=Europe/Paris")
        ),
    }


class McpConfig(_Section):
    # Defaults live here, not in the merged layers: a file that declares [mcp.servers.*]
    # replaces this whole table, so removing a server from config.toml really removes it.
    servers: dict[str, McpServerConfig] = Field(default_factory=_default_mcp_servers)

    @field_validator("servers")
    @classmethod
    def _server_names_are_valid(
        cls, servers: dict[str, McpServerConfig]
    ) -> dict[str, McpServerConfig]:
        for name in servers:
            if not _SERVER_NAME.match(name):
                raise ValueError(
                    f"nom de serveur invalide {name!r} : lettres, chiffres, '-' et '_' "
                    "seulement, sans '__'"
                )
        return servers


class FileRootConfig(_Section):
    path: Path  # relative to the launch directory
    mode: Literal["r", "rw"] = "r"


def _default_file_roots() -> tuple[FileRootConfig, ...]:
    return (
        FileRootConfig(path=Path("reports"), mode="rw"),
        FileRootConfig(path=Path("data/imports"), mode="r"),
    )


class FilesConfig(_Section):
    # Nothing is reachable by the file tools outside the declared roots
    max_read_bytes: int = Field(default=200_000, ge=1)
    write_extensions: tuple[str, ...] = (".md", ".csv", ".json", ".txt")
    # Always refused, even under a root
    deny: tuple[str, ...] = (".env", "config.toml", "*.db", "*.sqlite", "*.key", "*.pem")
    roots: tuple[FileRootConfig, ...] = Field(default_factory=_default_file_roots)


class WebConfig(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    share_dir: Path = Path("data/shares")  # X3: frozen snapshots of the shared links


class ScheduleConfig(_Section):
    """One task of [schedules.<id>] (X5): a question asked without a human, on a timetable."""

    at: str | None = None  # "MON 07:00", "DAILY 07:00"
    every_minutes: int | None = Field(default=None, ge=1)
    question: str = Field(min_length=1, max_length=4000)
    persona: str | None = None
    # {date}: the day of the run, 2026-10-26; the scheduler only writes under reports/planifie/
    output: str = Field(default="reports/planifie/{id}-{date}.md", min_length=1)
    enabled: bool = True

    @field_validator("at")
    @classmethod
    def _at_is_a_weekday_and_a_time(cls, value: str | None) -> str | None:
        if value is not None and not SCHEDULE_AT.match(value):
            raise ValueError('format attendu : "MON 07:00" … "SUN 07:00" ou "DAILY 07:00"')
        return value

    @model_validator(mode="after")
    def _one_timetable(self) -> ScheduleConfig:
        if (self.at is None) == (self.every_minutes is None):
            raise ValueError("indiquer soit at, soit every_minutes")
        return self


class DemoConfig(_Section):
    # Act 3 of the demo (scenario S-1, CORE-20): a figure of the answer is made wrong
    # before the judge, only for the questions that contain alter_trigger. Announced.
    alter_answer: bool = False
    alter_trigger: str = Field(default="Brasserie Lemaire", min_length=1)


class Config(_Section):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    hitl: HitlConfig = Field(default_factory=HitlConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)
    fewshot: FewshotConfig = Field(default_factory=FewshotConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    # No task by default; the example of config.example.toml is disabled
    schedules: dict[str, ScheduleConfig] = Field(default_factory=dict)

    @field_validator("schedules")
    @classmethod
    def _schedule_ids_are_valid(
        cls, schedules: dict[str, ScheduleConfig]
    ) -> dict[str, ScheduleConfig]:
        for schedule_id in schedules:
            if not _SCHEDULE_ID.match(schedule_id):
                raise ValueError(
                    f"identifiant de tâche invalide {schedule_id!r} : minuscules, chiffres, "
                    "'-' et '_' seulement"
                )
        return schedules


def load_config(
    path: str | Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Build the effective configuration. See the module docstring for the priority rules."""
    env = os.environ if environ is None else environ
    file_path = _resolve_path(path, env)
    layers = [
        _read_toml(file_path) if file_path is not None else {},
        _from_env(env),
        _from_overrides(overrides or {}),
    ]
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = _deep_merge(merged, layer)
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        source = f" ({file_path})" if file_path is not None else ""
        raise ConfigError(f"Configuration invalide{source} :\n{_format_errors(exc)}") from exc


def _resolve_path(path: str | Path | None, env: Mapping[str, str]) -> Path | None:
    explicit = path if path is not None else env.get(CONFIG_PATH_ENV) or None
    if explicit is None:
        return DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else None
    explicit_path = Path(explicit)
    if not explicit_path.is_file():
        raise ConfigError(f"Fichier de configuration introuvable : {explicit_path}")
    return explicit_path


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:  # tomllib and tomli both require a binary handle
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} : TOML invalide : {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path} : lecture impossible : {exc.strerror}") from exc


def _env_keys() -> dict[str, tuple[str, str, bool]]:
    """Map SENSAI_<SECTION>_<KEY> to (section, key, is_list) for every flat setting."""
    keys: dict[str, tuple[str, str, bool]] = {}
    for section, section_field in Config.model_fields.items():
        section_model = section_field.annotation
        if section in ("mcp", "schedules") or not (
            isinstance(section_model, type) and issubclass(section_model, BaseModel)
        ):
            continue  # nested tables (mcp.servers.*, schedules.*) are in the file only
        for key, field in section_model.model_fields.items():
            if _is_table_list(field.annotation):
                continue  # arrays of tables ([[files.roots]]) are configured in the file only
            is_list = get_origin(field.annotation) is tuple
            keys[f"{ENV_PREFIX}{section.upper()}_{key.upper()}"] = (section, key, is_list)
    return keys


def _is_table_list(annotation: Any) -> bool:
    return get_origin(annotation) is tuple and any(
        isinstance(arg, type) and issubclass(arg, BaseModel) for arg in get_args(annotation)
    )


def _from_env(env: Mapping[str, str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for name, (section, key, is_list) in _env_keys().items():
        if name not in env:
            continue
        raw = env[name]
        value: Any = [item.strip() for item in raw.split(",") if item.strip()] if is_list else raw
        layer = _deep_merge(layer, {section: {key: value}})
    return layer


def _from_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    layer: dict[str, Any] = {}
    for dotted, value in overrides.items():
        if value is None:
            continue
        nested: Any = value
        for part in reversed(dotted.split(".")):
            nested = {part: nested}
        layer = _deep_merge(layer, nested)
    return layer


def _deep_merge(base: Mapping[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """Return a new dict where ``top`` wins; nested tables are merged key by key."""
    merged = dict(base)
    for key, value in top.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(racine)"
        lines.append(f"  - {location} : {error['msg']}")
    return "\n".join(lines)
