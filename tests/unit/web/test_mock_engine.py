from __future__ import annotations

import json
from typing import Any

import pytest

from contract_rules import check_event, check_turns
from sensai.core.config import Config
from sensai.web.engines.mock import MockBackend, MockEngine, Pacing
from sensai.web.websocket.confirm import WebConfirmationBroker

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)
SESSION = "3f2b9c1e-0000-4000-8000-000000000002"


def make_engine() -> MockEngine:
    return MockBackend(Config(), pacing=INSTANT).new_engine(SESSION, WebConfirmationBroker())


async def play(engine: MockEngine, message: str) -> list[dict[str, Any]]:
    return [event async for event in engine.run_turn(message)]


@pytest.mark.parametrize(
    ("message", "marker"),
    [
        ("Quels sont nos 5 clients les plus rentables ce trimestre ?", "verified"),
        ("Chez nous, un client rentable c'est 25 % de marge. Retiens-le.", "memory_updated"),
        ("Quels sont nos clients rentables ce trimestre ?", "context_injected"),
        ("Quelle est la marge de Brasserie Lemaire ce trimestre ?", "contradicted"),
        ("Simule une erreur", "OLLAMA_UNAVAILABLE"),
    ],
)
async def test_keywords_pick_the_scenario(message: str, marker: str) -> None:
    events = await play(make_engine(), message)
    assert marker in json.dumps(events, ensure_ascii=False)
    for event in events:
        check_event(event)
    check_turns(events)


async def test_fixture_ids_are_replaced() -> None:
    events = await play(make_engine(), "Quels sont nos 5 clients les plus rentables ?")
    dumped = json.dumps(events)
    for fixture_id in ('"s-fixture"', '"t-1"', '"k-1"'):
        assert fixture_id not in dumped
    assert {event["session_id"] for event in events} == {SESSION}
    assert len({event["turn_id"] for event in events}) == 1


async def test_two_turns_get_different_ids() -> None:
    engine = make_engine()
    first = await play(engine, "Bonjour")
    second = await play(engine, "Bonjour")
    assert first[0]["turn_id"] != second[0]["turn_id"]


async def test_error_scenarios_cycle_and_reset_restarts_them() -> None:
    engine = make_engine()

    async def ending() -> str:
        events = await play(engine, "erreur")
        codes = [e["code"] for e in events if e["type"] == "error"]
        return codes[0] if codes else "message_done"

    assert [await ending() for _ in range(4)] == [
        "OLLAMA_UNAVAILABLE",
        "message_done",  # date server down: the agent asks for the period
        "MAX_ITERATIONS",
        "OLLAMA_UNAVAILABLE",
    ]
    engine.reset()
    assert await ending() == "OLLAMA_UNAVAILABLE"


async def test_ui_action_with_bad_arguments_fails_cleanly() -> None:
    engine = make_engine()
    events = [e async for e in engine.run_ui_action("memory_update", {"kind": "table"})]
    check_turns(events)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["error"]["code"] == "INVALID_ARGUMENTS"
    assert events[-1]["usage"] is None
