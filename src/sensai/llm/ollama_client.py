"""Async client for the Ollama HTTP API.

The subject forbids the `ollama` library: every model call goes through this client,
which talks to /api/chat, /api/tags and /api/show with httpx. Streaming responses are
NDJSON (one JSON object per line).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

from sensai.core.errors import (
    ModelNotFound,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    SensaiError,
    StreamInterrupted,
)

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5.0

Message = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool call requested by the model."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Token counts and duration reported on the final chunk."""

    prompt_tokens: int
    completion_tokens: int
    total_duration_ms: float


@dataclass(frozen=True)
class Chunk:
    """One piece of a chat response. Only the final chunk has done=True and a usage."""

    content: str = ""
    thinking: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    done: bool = False
    done_reason: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class ModelDetails:
    """What /api/show tells us about a model."""

    name: str
    capabilities: tuple[str, ...]
    context_length: int | None

    @property
    def supports_tools(self) -> bool:
        return "tools" in self.capabilities


class OllamaClient:
    """Client for one Ollama server. Reuse a single instance: it keeps a connection pool."""

    def __init__(
        self,
        host: str,
        timeout_s: float = 120.0,
        keep_alive: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive
        self._http = httpx.AsyncClient(
            base_url=self.host,
            # The read timeout applies between two received chunks, not to the whole answer
            timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S),
            transport=transport,
        )

    async def __aenter__(self) -> OllamaClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[Chunk, None]:
        """Stream a chat response chunk by chunk (POST /api/chat, stream=true).

        To cancel, stop iterating and close the generator (`contextlib.aclosing`):
        closing the HTTP response makes Ollama stop generating.
        """
        payload = self._chat_payload(
            model,
            messages,
            stream=True,
            tools=tools,
            format=format,
            options=options,
            think=think,
        )
        received: list[str] = []
        try:
            async with self._http.stream("POST", "/api/chat", json=payload) as response:
                if response.is_error:
                    await response.aread()
                    raise self._status_error(response, model)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = _parse_line(line, received)
                    received.append(chunk.content)
                    yield chunk
                    if chunk.done:
                        return
        except httpx.TransportError as exc:
            raise self._transport_error(exc, partial="".join(received)) from exc
        raise StreamInterrupted("flux terminé sans message final", partial="".join(received))

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
        think: bool | str | None = None,
    ) -> Chunk:
        """Get a whole chat response at once (stream=false), as a single final chunk."""
        payload = self._chat_payload(
            model,
            messages,
            stream=False,
            tools=tools,
            format=format,
            options=options,
            think=think,
        )
        data = await self._request_json("POST", "/api/chat", payload, model=model)
        if "error" in data:
            raise OllamaError(str(data["error"]))
        return _parse_chunk(data)

    async def list_models(self) -> list[str]:
        """Names of the installed models (GET /api/tags)."""
        data = await self._request_json("GET", "/api/tags")
        return [model["name"] for model in data.get("models", []) if "name" in model]

    async def show(self, model: str) -> ModelDetails:
        """Capabilities and context length of a model (POST /api/show)."""
        data = await self._request_json("POST", "/api/show", {"model": model}, model=model)
        return ModelDetails(
            name=model,
            capabilities=tuple(data.get("capabilities") or ()),
            context_length=_context_length(data.get("model_info") or {}),
        )

    def _chat_payload(
        self,
        model: str,
        messages: list[Message],
        *,
        stream: bool,
        tools: list[dict[str, Any]] | None,
        format: dict[str, Any] | str | None,
        options: dict[str, Any] | None,
        think: bool | str | None,
    ) -> dict[str, Any]:
        # Never log message contents: they may hold confidential business data
        logger.debug(
            "chat model=%s messages=%d tools=%d stream=%s",
            model,
            len(messages),
            len(tools or ()),
            stream,
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        optional = {
            "tools": tools,
            "format": format,
            "options": options,
            "think": think,
            "keep_alive": self.keep_alive,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, json=payload)
        except httpx.TransportError as exc:
            raise self._transport_error(exc) from exc
        if response.is_error:
            raise self._status_error(response, model)
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError("réponse illisible (JSON invalide)", response.status_code) from exc
        if not isinstance(data, dict):
            raise OllamaError("réponse inattendue", response.status_code)
        return data

    def _status_error(self, response: httpx.Response, model: str | None) -> SensaiError:
        if response.status_code == 404 and model is not None:
            return ModelNotFound(model)
        return OllamaError(_error_detail(response), response.status_code)

    def _transport_error(
        self, exc: httpx.TransportError, partial: str | None = None
    ) -> SensaiError:
        # ConnectTimeout is a TimeoutException, but it means the server is unreachable
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return OllamaUnavailable(self.host)
        if isinstance(exc, httpx.TimeoutException):
            return OllamaTimeout(self.timeout_s)
        if partial is not None:
            return StreamInterrupted("connexion coupée", partial=partial)
        return OllamaError(f"connexion interrompue ({type(exc).__name__})")


def _parse_line(line: str, received: list[str]) -> Chunk:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise StreamInterrupted("réponse illisible", partial="".join(received)) from exc
    if not isinstance(data, dict):
        raise StreamInterrupted("réponse inattendue", partial="".join(received))
    # Ollama reports some failures mid-stream, with an HTTP 200 status
    if "error" in data:
        raise OllamaError(str(data["error"]))
    return _parse_chunk(data)


def _parse_chunk(data: dict[str, Any]) -> Chunk:
    message = data.get("message") or {}
    done = bool(data.get("done"))
    return Chunk(
        content=message.get("content") or "",
        thinking=message.get("thinking") or "",
        tool_calls=tuple(_parse_tool_call(call) for call in message.get("tool_calls") or ()),
        done=done,
        done_reason=data.get("done_reason") if done else None,
        usage=_parse_usage(data) if done else None,
    )


def _parse_tool_call(call: dict[str, Any]) -> ToolCall:
    function = call.get("function") or {}
    arguments = function.get("arguments") or {}
    if isinstance(arguments, str):
        # Ollama sends an object, but some models emit the arguments as a JSON string
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"arguments d'outil illisibles : {arguments!r}") from exc
    if not isinstance(arguments, dict):
        raise OllamaError(f"arguments d'outil inattendus : {arguments!r}")
    return ToolCall(name=str(function.get("name", "")), arguments=arguments)


def _parse_usage(data: dict[str, Any]) -> Usage:
    return Usage(
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        completion_tokens=int(data.get("eval_count") or 0),
        total_duration_ms=(data.get("total_duration") or 0) / 1_000_000,
    )


def _context_length(model_info: dict[str, Any]) -> int | None:
    # The key depends on the architecture, e.g. "qwen3.context_length"
    for key, value in model_info.items():
        if key.endswith(".context_length") and isinstance(value, int):
            return value
    return None


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(data, dict) and "error" in data:
        return str(data["error"])
    return response.text.strip()
