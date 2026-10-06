import pytest

from sensai.core.errors import (
    ModelNotFound,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
    SensaiError,
    StreamInterrupted,
)

ERRORS = [
    OllamaUnavailable("http://localhost:11434"),
    ModelNotFound("qwen3:8b"),
    OllamaTimeout(120),
    StreamInterrupted("JSON invalide", partial="Bonj"),
    OllamaError("boom", status=500),
]


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
def test_errors_share_the_base_contract(error: SensaiError) -> None:
    assert isinstance(error, SensaiError)
    assert error.code.isupper()
    assert error.recoverable is True
    assert str(error) == error.message


def test_codes_are_unique() -> None:
    codes = [e.code for e in ERRORS] + [SensaiError.code]
    assert len(codes) == len(set(codes))


def test_messages_tell_the_user_what_to_do() -> None:
    assert "ollama serve" in OllamaUnavailable("http://localhost:11434").message
    assert "http://localhost:11434" in OllamaUnavailable("http://localhost:11434").message
    assert "ollama pull qwen3:8b" in ModelNotFound("qwen3:8b").message
    assert "120 s" in OllamaTimeout(120).message


def test_stream_interrupted_keeps_partial_text() -> None:
    error = StreamInterrupted("flux coupé", partial="Bonjour, je")
    assert error.partial == "Bonjour, je"
    assert "flux coupé" in error.message


def test_ollama_error_with_and_without_status() -> None:
    assert OllamaError("boom", status=500).message == "Erreur Ollama (HTTP 500) : boom"
    assert OllamaError("boom").message == "Erreur Ollama : boom"
