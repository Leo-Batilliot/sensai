"""Structured output (CORE-07, T5): validation, one repair, clean degradation, counters."""

import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from fakes import MODEL
from sensai.core.errors import OllamaUnavailable
from sensai.llm.ollama_client import Chunk, Message, OllamaClient
from sensai.llm.structured import (
    MAX_ERRORS,
    RepairStats,
    StructuredResult,
    generate_structured,
    schema_errors,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["label", "value"],
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "minLength": 1},
        "value": {"type": "number"},
    },
}
VALID = {"label": "Marge", "value": 48712.4}


class FakeChat:
    """Stands in for OllamaClient.chat: plays the scripted contents, or raises."""

    def __init__(self, *replies: str | dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        self.calls.append(
            {"model": model, "messages": list(messages), "format": format, "options": options}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return Chunk(content=content, done=True, done_reason="stop")


QUESTION: list[Message] = [{"role": "user", "content": "Donne la marge en JSON."}]


async def generate(chat: FakeChat, stats: RepairStats | None = None) -> StructuredResult:
    return await generate_structured(chat, QUESTION, SCHEMA, MODEL, stats=stats)


# --- The four cases of the task card ---


async def test_valid_json_is_accepted_at_the_first_try() -> None:
    chat, stats = FakeChat(VALID), RepairStats()

    result = await generate(chat, stats)

    assert result == StructuredResult(True, VALID, json.dumps(VALID), (), 1)
    assert stats.to_dict() == {"first_try": 1, "repaired": 0, "failed": 0}


async def test_invalid_then_valid_is_repaired_with_the_exact_errors() -> None:
    chat, stats = FakeChat({"label": "Marge", "value": "48712.4"}, VALID), RepairStats()

    result = await generate(chat, stats)

    assert (result.ok, result.data, result.attempts) == (True, VALID, 2)
    assert stats.to_dict() == {"first_try": 0, "repaired": 1, "failed": 0}
    repair = chat.calls[1]["messages"]
    assert repair[:-2] == QUESTION
    assert repair[-2] == {"role": "assistant", "content": '{"label": "Marge", "value": "48712.4"}'}
    assert repair[-1]["role"] == "user"
    assert "value : '48712.4' is not of type 'number'" in repair[-1]["content"]
    assert "Renvoie uniquement le JSON corrigé" in repair[-1]["content"]


async def test_invalid_twice_degrades_without_raising() -> None:
    chat, stats = FakeChat({"label": ""}, {"value": 1}), RepairStats()

    result = await generate(chat, stats)

    assert (result.ok, result.data, result.attempts) == (False, None, 2)
    assert result.raw == '{"value": 1}'  # the last answer
    assert result.errors == ("(racine) : 'label' is a required property",)
    assert stats.to_dict() == {"first_try": 0, "repaired": 0, "failed": 1}
    assert len(chat.calls) == 2


@pytest.mark.parametrize("text", ["La marge est de 48 712 €.", "", "{pas du json"])
async def test_text_that_is_not_json_is_invalid(text: str) -> None:
    result = await generate(FakeChat(text, text))

    assert result.ok is False
    assert result.errors[0].startswith("JSON invalide : ")


# --- What the model sends back around the JSON ---


@pytest.mark.parametrize(
    "raw",
    [
        f"```json\n{json.dumps(VALID)}\n```",
        f"```\n{json.dumps(VALID)}\n```",
        f"<think>Je cherche la marge.</think>\n{json.dumps(VALID)}",
        f"  {json.dumps(VALID)}\n",
    ],
)
async def test_fences_and_thinking_around_the_json_are_ignored(raw: str) -> None:
    result = await generate(FakeChat(raw))

    assert (result.ok, result.data, result.raw) == (True, VALID, raw)


async def test_a_json_value_that_is_not_an_object_is_invalid() -> None:
    result = await generate_structured(FakeChat("[1, 2]", "3"), QUESTION, {}, MODEL)

    assert result.ok is False
    assert result.errors == ("(racine) : un objet JSON est attendu",)


async def test_numbers_sent_as_strings_are_not_converted() -> None:
    result = await generate(FakeChat({"label": "Marge", "value": "12.5"}, VALID))

    assert result.attempts == 2  # the schema decides: "12.5" is not a number


# --- The call itself ---


async def test_the_call_uses_the_schema_and_temperature_zero() -> None:
    chat = FakeChat(VALID)

    await generate_structured(chat, QUESTION, SCHEMA, MODEL, options={"num_ctx": 8192})

    (call,) = chat.calls
    assert call["model"] == MODEL
    assert call["format"] == SCHEMA
    assert call["options"] == {"temperature": 0, "num_ctx": 8192}


async def test_the_caller_can_choose_the_temperature() -> None:
    chat = FakeChat(VALID)

    await generate_structured(chat, QUESTION, SCHEMA, MODEL, options={"temperature": 0.3})

    assert chat.calls[0]["options"] == {"temperature": 0.3}


async def test_the_caller_messages_are_left_untouched() -> None:
    messages = list(QUESTION)

    await generate_structured(FakeChat("{}", VALID), messages, SCHEMA, MODEL)

    assert messages == QUESTION


async def test_without_repair_a_single_call_is_made() -> None:
    chat = FakeChat("{}")

    result = await generate_structured(chat, QUESTION, SCHEMA, MODEL, max_repairs=0)

    assert (result.ok, result.attempts, len(chat.calls)) == (False, 1, 1)


async def test_ollama_failures_are_left_to_the_caller() -> None:
    with pytest.raises(OllamaUnavailable):
        await generate(FakeChat(OllamaUnavailable("http://localhost:11434")))


async def test_http_request_with_the_real_client() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = {"message": {"role": "assistant", "content": json.dumps(VALID)}, "done": True}
        return httpx.Response(200, json=body)

    async with OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler)) as client:
        result = await generate_structured(client, QUESTION, SCHEMA, MODEL)

    assert result.data == VALID
    (payload,) = requests
    assert (payload["stream"], payload["format"]) == (False, SCHEMA)


# --- Counters and error messages ---


def test_stats_add_up() -> None:
    stats = RepairStats()
    for ok, attempts in [(True, 1), (True, 1), (True, 2), (False, 2)]:
        stats.record(StructuredResult(ok, VALID if ok else None, "", (), attempts))

    assert stats.to_dict() == {"first_try": 2, "repaired": 1, "failed": 1}
    assert stats.total == 4


def test_schema_errors_are_empty_for_a_valid_instance() -> None:
    assert schema_errors(Draft202012Validator(SCHEMA), VALID) == []


def test_schema_errors_give_the_path_and_keep_the_most_relevant() -> None:
    nested = {
        "type": "object",
        "properties": {"figures": {"type": "array", "items": SCHEMA}},
    }
    instance = {"figures": [VALID, {"label": "", "value": "x", "a": 1}, {"label": 5, "value": 1}]}

    errors = schema_errors(Draft202012Validator(nested), instance)

    assert len(errors) == MAX_ERRORS  # 4 errors in the instance, 3 sent to the model
    assert all(error.startswith(("figures/1/", "figures/2/", "figures/1 ")) for error in errors)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"label": "Marge"}, "(racine) : 'value' is a required property"),
        ({"label": "Marge", "value": "beaucoup"}, "value : 'beaucoup' is not of type 'number'"),
        ({**VALID, "unit": "€"}, "Additional properties are not allowed ('unit' was unexpected)"),
    ],
)
def test_schema_errors_for_tool_arguments(arguments: dict[str, Any], expected: str) -> None:
    errors = schema_errors(Draft202012Validator(SCHEMA), arguments)

    assert any(expected in error for error in errors)
