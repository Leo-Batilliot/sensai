"""Session settings changeable from the interface with `set_config` (websocket.md §4.7).

They only apply to the session, from the next turn, and are never written to config.toml.
"""

from __future__ import annotations

from typing import Any

from sensai.core.config import Config

# Key in the `set_config` patch -> key in config.toml
SESSION_SETTINGS = {
    "temperature": "agent.temperature",
    "num_ctx": "ollama.num_ctx",
    "judge_enabled": "eval.judge_enabled",
    "judge_model": "ollama.judge_model",
}
NUM_CTX_BOUNDS = (2048, 32768)
TEMPERATURE_BOUNDS = (0.0, 1.0)


def session_settings(config: Config) -> dict[str, Any]:
    """Initial session settings, read from config.toml (`GET /api/config` defaults)."""
    return {
        "temperature": config.agent.temperature,
        "num_ctx": config.ollama.num_ctx,
        "judge_enabled": config.eval.judge_enabled,
        "judge_model": config.ollama.judge_model or None,  # empty = same model as the agent
    }


def invalid_session_patch(patch: dict[str, Any]) -> str | None:
    """All-or-nothing check of a `set_config` patch: the problem in French, or None."""
    for key, value in patch.items():
        if key not in SESSION_SETTINGS:
            return f"Réglage « {key} » non modifiable depuis l'interface."
        if key == "temperature":
            low, high = TEMPERATURE_BOUNDS
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "temperature doit être un nombre."
            if not low <= value <= high:
                return f"temperature doit être entre {low:g} et {high:g}."
        elif key == "num_ctx":
            low_ctx, high_ctx = NUM_CTX_BOUNDS
            if isinstance(value, bool) or not isinstance(value, int):
                return "num_ctx doit être un entier."
            if not low_ctx <= value <= high_ctx:
                return f"num_ctx doit être entre {low_ctx} et {high_ctx}."
        elif key == "judge_enabled" and not isinstance(value, bool):
            return "judge_enabled doit être vrai ou faux."
        elif key == "judge_model" and value is not None and not (isinstance(value, str) and value):
            return "judge_model doit être un nom de modèle ou null."
    return None


def config_view(config: Config) -> dict[str, Any]:
    """`GET /api/config` (websocket.md §9): what the Réglages menu is built from.

    `defaults` are the values of config.toml (« Réinitialiser » sends them back), `bounds`
    the limits of the numeric settings, `read_only` what is shown but never changed from
    the interface. No file path, MCP command, deny list nor [web] section."""
    return {
        "defaults": session_settings(config),
        "bounds": {"temperature": list(TEMPERATURE_BOUNDS), "num_ctx": list(NUM_CTX_BOUNDS)},
        "read_only": {
            "ollama_host": config.ollama.host,
            "keep_alive": config.ollama.keep_alive,
            "max_tool_iterations": config.agent.max_tool_iterations,
            "max_sql_retries": config.agent.max_sql_retries,
            "row_limit": config.database.row_limit,
            "query_timeout_ms": config.database.query_timeout_ms,
            "confirmation_timeout_s": config.hitl.confirmation_timeout_s,
            "numeric_tolerance": config.eval.numeric_tolerance,
        },
    }
