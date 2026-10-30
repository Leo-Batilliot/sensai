"""Ollama client tests, replaying the responses stored in tests/fixtures/ollama/.

Expected values are computed from the fixtures themselves, so the tests still hold
after re-recording them with scripts/record_ollama.py.
"""

import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from sensai.core.errors import (
    ModelNotFound,
    ModelNoTools,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    StreamInterrupted,
)
from sensai.llm.ollama_client import Chunk, OllamaClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ollama"
HOST = "http://ollama.test"
MODEL = "qwen3:8b"
MESSAGES = [{"role": "user", "content": "Bonjour"}]

Handler = Callable[[httpx.Request], httpx.Response]


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def raw_lines(name: str) -> list[str]:
    return [line for line in raw(name).decode("utf-8").splitlines() if line.strip()]


def joined(lines: list[str], field: str) -> str:
    return "".join(json.loads(line)["message"].get(field) or "" for line in lines)


def reply(status: int, body: bytes | dict[str, Any]) -> Handler:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return handler


def failing(exc_type: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("simulated failure", request=request)

    return handler


def make_client(handler: Handler, **kwargs: Any) -> OllamaClient:
    return OllamaClient(HOST, timeout_s=5, transport=httpx.MockTransport(handler), **kwargs)


async def stream(handler: Handler, **kwargs: Any) -> list[Chunk]:
    async with make_client(handler) as ollama:
        return [chunk async for chunk in ollama.chat_stream(MODEL, MESSAGES, **kwargs)]


class CutStream(httpx.AsyncByteStream):
    """Sends some bytes, then drops the connection."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data
        raise httpx.RemoteProtocolError("peer closed connection")


# --- streaming ---------------------------------------------------------------


async def test_stream_yields_text_then_usage_on_final_chunk() -> None:
    lines = raw_lines("chat_simple.ndjson")
    final = json.loads(lines[-1])

    chunks = await stream(reply(200, raw("chat_simple.ndjson")))

    assert "".join(c.content for c in chunks) == joined(lines, "content")
    assert chunks[-1].done
    assert chunks[-1].done_reason == final["done_reason"]
    assert all(c.usage is None and not c.done for c in chunks[:-1])
    usage = chunks[-1].usage
    assert usage is not None
    assert usage.prompt_tokens == final["prompt_eval_count"]
    assert usage.completion_tokens == final["eval_count"]
    assert usage.total_duration_ms == pytest.approx(final["total_duration"] / 1_000_000)


async def test_stream_tool_call_arguments_are_a_dict() -> None:
    expected = [
        (call["function"]["name"], call["function"]["arguments"])
        for line in raw_lines("chat_tool_call.ndjson")
        for call in json.loads(line)["message"].get("tool_calls", [])
    ]

    chunks = await stream(reply(200, raw("chat_tool_call.ndjson")))

    calls = [call for chunk in chunks for call in chunk.tool_calls]
    assert [(c.name, c.arguments) for c in calls] == expected
    assert calls[0].name == "get_current_time"
    assert isinstance(calls[0].arguments, dict)


async def test_stream_keeps_thinking_apart_from_content() -> None:
    lines = raw_lines("chat_thinking.ndjson")

    chunks = await stream(reply(200, raw("chat_thinking.ndjson")), think=True)

    thinking = "".join(c.thinking for c in chunks)
    assert thinking
    assert thinking == joined(lines, "thinking")
    assert "".join(c.content for c in chunks) == joined(lines, "content")


async def test_tool_call_arguments_sent_as_a_json_string_are_decoded() -> None:
    line = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "run_select", "arguments": '{"sql": "SELECT 1"}'}}
            ],
        },
        "done": True,
    }

    chunks = await stream(reply(200, (json.dumps(line) + "\n").encode()))

    assert chunks[0].tool_calls[0].arguments == {"sql": "SELECT 1"}


async def test_payload_contains_only_the_given_options() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=raw("chat_simple.ndjson"))

    tools = [{"type": "function", "function": {"name": "get_current_time"}}]
    async with make_client(handler, keep_alive="30m") as ollama:
        async for _ in ollama.chat_stream(
            MODEL, MESSAGES, tools=tools, format="json", options={"temperature": 0}, think=False
        ):
            pass
        async for _ in ollama.chat_stream(MODEL, MESSAGES):
            pass

    full, minimal = sent
    assert full == {
        "model": MODEL,
        "messages": MESSAGES,
        "stream": True,
        "tools": tools,
        "format": "json",
        "options": {"temperature": 0},
        "think": False,
        "keep_alive": "30m",
    }
    # None means "not sent"; False (think) is a real value and is sent above
    assert minimal == {"model": MODEL, "messages": MESSAGES, "stream": True, "keep_alive": "30m"}


async def test_stopping_early_closes_the_stream_cleanly() -> None:
    async with make_client(reply(200, raw("chat_simple.ndjson"))) as ollama:
        chunks = ollama.chat_stream(MODEL, MESSAGES)
        first = await chunks.__anext__()
        await chunks.aclose()

    assert first.content == json.loads(raw_lines("chat_simple.ndjson")[0])["message"]["content"]


# --- non-streaming calls -----------------------------------------------------


async def test_chat_returns_the_whole_answer_as_a_final_chunk() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=raw("chat_format.json"))

    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    async with make_client(handler) as ollama:
        answer = await ollama.chat(MODEL, MESSAGES, format=schema, options={"temperature": 0})

    assert sent[0]["stream"] is False
    assert sent[0]["format"] == schema
    assert answer.done
    assert answer.usage is not None
    assert "answer" in json.loads(answer.content)


async def test_list_models_returns_installed_names() -> None:
    expected = [model["name"] for model in json.loads(raw("tags.json"))["models"]]

    async with make_client(reply(200, raw("tags.json"))) as ollama:
        assert await ollama.list_models() == expected


async def test_show_reports_capabilities_and_context_length() -> None:
    data = json.loads(raw("show.json"))
    expected_ctx = next(
        value for key, value in data["model_info"].items() if key.endswith(".context_length")
    )

    async with make_client(reply(200, raw("show.json"))) as ollama:
        details = await ollama.show(MODEL)

    assert details.capabilities == tuple(data["capabilities"])
    assert details.supports_tools
    assert details.context_length == expected_ctx


# --- errors ------------------------------------------------------------------


async def test_missing_model_raises_model_not_found_everywhere() -> None:
    handler = reply(404, raw("error_404.json"))

    with pytest.raises(ModelNotFound) as exc:
        await stream(handler)
    assert f"ollama pull {MODEL}" in exc.value.message

    async with make_client(handler) as ollama:
        with pytest.raises(ModelNotFound):
            await ollama.chat(MODEL, MESSAGES)
        with pytest.raises(ModelNotFound):
            await ollama.show(MODEL)


@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_unreachable_server_raises_unavailable(exc_type: type[httpx.TransportError]) -> None:
    with pytest.raises(OllamaUnavailable) as exc:
        await stream(failing(exc_type))
    assert HOST in exc.value.message

    async with make_client(failing(exc_type)) as ollama:
        with pytest.raises(OllamaUnavailable):
            await ollama.list_models()


async def test_read_timeout_raises_ollama_timeout() -> None:
    with pytest.raises(OllamaTimeout):
        await stream(failing(httpx.ReadTimeout))


async def test_stream_without_final_chunk_is_interrupted() -> None:
    lines = raw_lines("chat_simple.ndjson")[:-1]
    # Blank lines between chunks must be ignored
    body = "\n\n".join(lines).encode()

    with pytest.raises(StreamInterrupted) as exc:
        await stream(reply(200, body))
    assert exc.value.partial == joined(lines, "content")


async def test_invalid_json_line_is_interrupted_with_partial_text() -> None:
    first = raw_lines("chat_simple.ndjson")[0]
    body = (first + '\n{"message": {"content": "tronq').encode()

    with pytest.raises(StreamInterrupted) as exc:
        await stream(reply(200, body))
    assert exc.value.partial == joined([first], "content")


async def test_connection_cut_mid_stream_is_interrupted_with_partial_text() -> None:
    first = raw_lines("chat_simple.ndjson")[0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=CutStream((first + "\n").encode()))

    with pytest.raises(StreamInterrupted) as exc:
        await stream(handler)
    assert exc.value.partial == joined([first], "content")


async def test_error_line_mid_stream_raises_ollama_error() -> None:
    first = raw_lines("chat_simple.ndjson")[0]
    body = (first + "\n" + json.dumps({"error": "out of memory"}) + "\n").encode()

    with pytest.raises(OllamaError, match="out of memory"):
        await stream(reply(200, body))


async def test_a_model_without_tools_raises_model_no_tools() -> None:
    refused = reply(400, {"error": f"registry.ollama.ai/library/{MODEL} does not support tools"})

    with pytest.raises(ModelNoTools) as exc:
        await stream(refused)
    assert f"« {MODEL} » ne gère pas les outils" in exc.value.message


async def test_server_error_status_raises_ollama_error() -> None:
    with pytest.raises(OllamaError) as exc:
        await stream(reply(500, {"error": "boom"}))
    assert exc.value.status == 500
    assert exc.value.detail == "boom"


# --- real server (excluded from CI) ------------------------------------------


@pytest.mark.ollama
async def test_real_ollama_round_trip() -> None:
    host = os.environ.get("SENSAI_OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("SENSAI_OLLAMA_MODEL", MODEL)

    async with OllamaClient(host) as ollama:
        assert model in await ollama.list_models()
        assert (await ollama.show(model)).supports_tools
        chunks = [
            chunk
            async for chunk in ollama.chat_stream(
                model,
                [{"role": "user", "content": "Réponds seulement : OK"}],
                think=False,
                options={"temperature": 0},
            )
        ]

    assert chunks[-1].done
    assert chunks[-1].usage is not None
    assert "".join(c.content for c in chunks).strip()
