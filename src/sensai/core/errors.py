"""Typed errors shared by the whole project.

Each error carries a stable `code` (used by the `error` event of the contract), a
`recoverable` flag, and a French message that tells the user what to do next.
"""

from __future__ import annotations


class SensaiError(Exception):
    """Base class for failures that adapters can show to the user as is."""

    code = "INTERNAL"
    recoverable = True

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OllamaUnavailable(SensaiError):
    """The Ollama server cannot be reached (not started, wrong host)."""

    code = "OLLAMA_UNAVAILABLE"

    def __init__(self, host: str) -> None:
        super().__init__(f"Ollama injoignable sur {host} — lancez `ollama serve`.")
        self.host = host


class ModelNotFound(SensaiError):
    """The requested model is not installed in Ollama."""

    code = "MODEL_NOT_FOUND"

    def __init__(self, model: str) -> None:
        super().__init__(
            f"Le modèle « {model} » n'est pas installé. Lancez : `ollama pull {model}`."
        )
        self.model = model


class OllamaTimeout(SensaiError):
    """Ollama did not answer within the configured delay."""

    code = "OLLAMA_TIMEOUT"

    def __init__(self, timeout_s: float) -> None:
        super().__init__(
            f"Ollama n'a pas répondu en {timeout_s:g} s. Réessayez : l'historique est conservé."
        )
        self.timeout_s = timeout_s


class StreamInterrupted(SensaiError):
    """The response stream stopped before its final chunk."""

    code = "STREAM_INTERRUPTED"

    def __init__(self, reason: str, partial: str = "") -> None:
        super().__init__(f"La réponse a été interrompue ({reason}).")
        self.reason = reason
        # Text received before the cut, so the caller can show it as interrupted
        self.partial = partial


class OllamaError(SensaiError):
    """Any other error reported by Ollama (HTTP status or an error line in the stream)."""

    code = "OLLAMA_ERROR"

    def __init__(self, detail: str, status: int | None = None) -> None:
        prefix = f"Erreur Ollama (HTTP {status})" if status is not None else "Erreur Ollama"
        super().__init__(f"{prefix} : {detail}")
        self.detail = detail
        self.status = status


class McpCommandNotFound(SensaiError):
    """The command that starts an MCP server is not installed (uvx, npx, ...)."""

    code = "MCP_COMMAND_NOT_FOUND"

    def __init__(self, server: str, command: str) -> None:
        super().__init__(
            f"Serveur MCP « {server} » : la commande `{command}` est introuvable. "
            "Installez-la ou corrigez [mcp.servers] dans config.toml."
        )
        self.server = server
        self.command = command


class McpServerDied(SensaiError):
    """The MCP server process stopped, or closed its output, while still in use."""

    code = "MCP_SERVER_DIED"

    def __init__(self, server: str, detail: str = "") -> None:
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"Le serveur MCP « {server} » s'est arrêté{suffix}.")
        self.server = server
        self.detail = detail


class McpTimeout(SensaiError):
    """An MCP request got no answer within its delay."""

    code = "MCP_TIMEOUT"

    def __init__(self, server: str, method: str, timeout_s: float) -> None:
        super().__init__(
            f"Le serveur MCP « {server} » n'a pas répondu à `{method}` en {timeout_s:g} s."
        )
        self.server = server
        self.method = method
        self.timeout_s = timeout_s


class McpProtocolError(SensaiError):
    """The MCP server broke the protocol (unsupported version, malformed answer)."""

    code = "MCP_PROTOCOL_ERROR"

    def __init__(self, server: str, detail: str) -> None:
        super().__init__(f"Serveur MCP « {server} » : {detail}.")
        self.server = server
        self.detail = detail
