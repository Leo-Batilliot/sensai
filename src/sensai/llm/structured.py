"""Structured output (T5, PROJECT.md §6.2): model answers that our code consumes as JSON.

generate_structured() asks Ollama for JSON constrained by a JSON Schema (`format`),
validates the answer and gives the model one chance to repair it with the exact errors.
It never raises because of a bad answer: the caller gets ok=False and degrades cleanly
(the judge shows "vérification indisponible").

schema_errors() turns jsonschema errors into short French-friendly lines; the tool
registries use it for the tool arguments too (tools.md §3.6, validate_arguments).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

from sensai.llm.ollama_client import Chunk, Message

logger = logging.getLogger(__name__)

MAX_ERRORS = 3  # jsonschema messages are long: the model only gets the most relevant ones

# Some models wrap the JSON in a Markdown fence or think aloud first, even with `format`
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_THINK = re.compile(r"^<think>.*?</think>", re.DOTALL)


class StructuredClient(Protocol):
    """What generate_structured needs from OllamaClient (tests use a scripted fake)."""

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk: ...


@dataclass(frozen=True)
class StructuredResult:
    """Outcome of generate_structured. data is set if and only if ok."""

    ok: bool
    data: dict[str, Any] | None
    raw: str  # last answer of the model, as received
    errors: tuple[str, ...]  # errors of the last answer, () when ok
    attempts: int  # model calls made: 1, or 2 after a repair


@dataclass
class RepairStats:
    """T5 counters: valid at the first try, valid after a repair, still invalid.

    Read by the evaluation harness (EVAL-06) for the "JSON valide" metric (judge.md §4).
    """

    first_try: int = 0
    repaired: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.first_try + self.repaired + self.failed

    def record(self, result: StructuredResult) -> None:
        if not result.ok:
            self.failed += 1
        elif result.attempts == 1:
            self.first_try += 1
        else:
            self.repaired += 1

    def to_dict(self) -> dict[str, int]:
        return {"first_try": self.first_try, "repaired": self.repaired, "failed": self.failed}


def schema_errors(
    validator: Draft202012Validator, instance: Any, limit: int = MAX_ERRORS
) -> list[str]:
    """Validation errors as "path : message" lines, the most relevant first, `limit` at most.

    [] when the instance is valid.
    """
    errors = list(validator.iter_errors(instance))
    if not errors:
        return []
    best = best_match(errors)
    ordered = [best, *(error for error in errors if error is not best)]
    return [_describe(error) for error in ordered[:limit]]


async def generate_structured(
    client: StructuredClient,
    messages: list[Message],
    schema: dict[str, Any],
    model: str,
    max_repairs: int = 1,
    options: dict[str, Any] | None = None,
    *,
    stats: RepairStats | None = None,
) -> StructuredResult:
    """Ask for JSON that follows `schema`, validate it, repair it at most max_repairs times.

    The messages should also describe the schema in plain words: models follow `format`
    better that way (the judge prompt does it). Temperature is 0 unless `options` says
    otherwise. Ollama failures (SensaiError) are raised as is: they are not output
    errors, and the caller decides how to degrade.
    """
    validator = Draft202012Validator(schema)
    chat_options = {"temperature": 0, **(options or {})}
    conversation = list(messages)  # the caller's list is never changed
    result = StructuredResult(False, None, "", ("aucun appel",), 0)
    for attempt in range(1, max_repairs + 2):
        chunk = await client.chat(model, conversation, format=schema, options=chat_options)
        data, errors = _check(chunk.content, validator)
        result = StructuredResult(not errors, data, chunk.content, tuple(errors), attempt)
        if result.ok:
            break
        logger.info("structured output invalid (attempt %d): %d errors", attempt, len(errors))
        # Repair: the model sees its own answer, then exactly what is wrong with it
        conversation += [
            {"role": "assistant", "content": chunk.content},
            {"role": "user", "content": _repair_request(errors)},
        ]
    if stats is not None:
        stats.record(result)
    return result


def _check(raw: str, validator: Draft202012Validator) -> tuple[dict[str, Any] | None, list[str]]:
    """(data, []) when the answer can be used, (None, errors) otherwise."""
    try:
        data = json.loads(_strip(raw))
    except json.JSONDecodeError as exc:
        return None, [f"JSON invalide : {exc.msg} (ligne {exc.lineno}, colonne {exc.colno})"]
    errors = schema_errors(validator, data)
    if not errors and not isinstance(data, dict):
        errors = ["(racine) : un objet JSON est attendu"]
    if errors:
        return None, errors
    return data, []


def _strip(raw: str) -> str:
    """The JSON part of an answer: without a leading <think> block nor a Markdown fence."""
    text = _THINK.sub("", raw.strip(), count=1).strip()
    fenced = _FENCE.match(text)
    return fenced.group(1) if fenced else text


def _describe(error: ValidationError) -> str:
    path = "/".join(str(part) for part in error.absolute_path) or "(racine)"
    return f"{path} : {error.message}"


def _repair_request(errors: list[str]) -> str:
    listed = "\n".join(f"- {error}" for error in errors)
    return (
        f"Ta réponse n'est pas valide :\n{listed}\n"
        "Renvoie uniquement le JSON corrigé, sans texte autour."
    )
