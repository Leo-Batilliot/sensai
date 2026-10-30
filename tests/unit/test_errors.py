import json
from pathlib import Path

import pytest

from sensai.core.errors import (
    ModelNotFound,
    ModelNoTools,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    PersonaNotFound,
    SensaiError,
    StreamInterrupted,
)

# The errors that reach the user as an `error` event (the MCP ones become tool results)
ERRORS = [
    OllamaUnavailable("http://localhost:11434"),
    ModelNotFound("qwen3:8b"),
    ModelNoTools("gemma:2b"),
    OllamaTimeout(120),
    StreamInterrupted("JSON invalide", partial="Bonj"),
    OllamaError("boom", status=500),
    PersonaNotFound("compta", ["admin", "adv", "analyste"]),
]
ERROR_SCHEMA = Path(__file__).resolve().parents[2] / "docs/contracts/schemas/events/error.json"


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
def test_errors_share_the_base_contract(error: SensaiError) -> None:
    assert isinstance(error, SensaiError)
    assert error.code.isupper()
    assert error.recoverable is True
    assert str(error) == error.message


def test_codes_are_unique() -> None:
    codes = [e.code for e in ERRORS] + [SensaiError.code]
    assert len(codes) == len(set(codes))


def test_codes_are_in_the_closed_list_of_the_contract() -> None:
    schema = json.loads(ERROR_SCHEMA.read_text(encoding="utf-8"))
    allowed = set(schema["properties"]["code"]["enum"])
    assert {e.code for e in ERRORS} | {SensaiError.code} <= allowed


def test_messages_tell_the_user_what_to_do() -> None:
    assert "ollama serve" in OllamaUnavailable("http://localhost:11434").message
    assert "http://localhost:11434" in OllamaUnavailable("http://localhost:11434").message
    assert "ollama pull qwen3:8b" in ModelNotFound("qwen3:8b").message
    assert "120 s" in OllamaTimeout(120).message
    assert "qwen3:8b" in ModelNoTools("gemma:2b").message  # a model that works
    assert "analyste" in PersonaNotFound("compta", ["analyste"]).message  # the known ones


def test_stream_interrupted_keeps_partial_text() -> None:
    error = StreamInterrupted("flux coupé", partial="Bonjour, je")
    assert error.partial == "Bonjour, je"
    assert "flux coupé" in error.message


def test_ollama_error_with_and_without_status() -> None:
    assert OllamaError("boom", status=500).message == "Erreur Ollama (HTTP 500) : boom"
    assert OllamaError("boom").message == "Erreur Ollama : boom"
