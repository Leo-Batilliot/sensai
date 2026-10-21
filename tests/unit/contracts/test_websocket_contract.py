"""Contract tests for WEB-01: event fixtures and docs/contracts/websocket.md.

The rules themselves live in tests/contract_rules.py, shared with the server tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from contract_rules import (
    CLIENT,
    CLIENT_TYPES,
    CONTRACTS,
    EVENT_TYPES,
    FIXTURES,
    SERVER,
    SERVER_TYPES,
    check_event,
    check_turns,
    load_jsonl,
)

# --- fixtures -------------------------------------------------------------------------


FIXTURE_FILES = sorted(FIXTURES.glob("*.jsonl"))


def test_expected_fixtures_exist() -> None:
    # tests/fixtures/events/README.md and websocket.md §11
    names = {path.name for path in FIXTURE_FILES}
    assert names >= {
        "nominal.jsonl",
        "hitl_approve.jsonl",
        "hitl_reject.jsonl",
        "judge_contradiction.jsonl",
        "errors.jsonl",
        "memory.jsonl",
    }


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_events_follow_the_contract(path: Path) -> None:
    events = load_jsonl(path)
    assert events, "fixture vide"
    for number, event in enumerate(events, start=1):
        try:
            check_event(event)
        except AssertionError as exc:
            raise AssertionError(f"{path.name}, ligne {number} : {exc}") from None
    stamps = [event["ts"] for event in events]
    assert stamps == sorted(stamps), "ts doit croître dans une fixture"
    check_turns(events)


def events_of(name: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in load_jsonl(FIXTURES / name) if e["type"] == kind]


def test_hitl_branches_share_the_same_beginning() -> None:
    # The MockEngine plays this common part, waits for `confirm`, then picks a branch.
    approve = load_jsonl(FIXTURES / "hitl_approve.jsonl")
    reject = load_jsonl(FIXTURES / "hitl_reject.jsonl")
    pause = [e["type"] for e in approve].index("confirmation_required") + 1
    assert approve[:pause] == reject[:pause]
    assert events_of("hitl_approve.jsonl", "confirmation_resolved")[0]["decision"] == "approve"
    assert events_of("hitl_reject.jsonl", "confirmation_resolved")[0]["decision"] == "reject"
    refused = events_of("hitl_reject.jsonl", "tool_result")[-1]
    assert refused["error"]["code"] == "ACTION_REJECTED"


def test_each_fixture_shows_its_scenario() -> None:
    assert events_of("nominal.jsonl", "judge_report")[0]["badge"] == "verified"
    contradiction = events_of("judge_contradiction.jsonl", "judge_report")[0]
    assert contradiction["badge"] == "contradicted"
    assert any(
        claim["verdict"] == "contradicted" and claim["expected_value"]
        for claim in contradiction["report"]["claims"]
    )
    assert events_of("memory.jsonl", "memory_updated")
    assert events_of("memory.jsonl", "context_injected")
    codes = {e["code"] for e in events_of("errors.jsonl", "error")}
    assert codes >= {"OLLAMA_UNAVAILABLE", "MAX_ITERATIONS"}
    tool_codes = {
        e["error"]["code"] for e in events_of("errors.jsonl", "tool_result") if e["error"]
    }
    assert tool_codes >= {"MCP_SERVER_UNAVAILABLE", "DUPLICATE_CALL", "SQL_ERROR"}


# --- websocket.md and its schemas -----------------------------------------------------


def has_ellipsis_key(value: Any) -> bool:
    """Abbreviated examples use a "…" key; they are not checked field by field."""
    if isinstance(value, dict):
        return "…" in value or any(has_ellipsis_key(v) for v in value.values())
    if isinstance(value, list):
        return any(has_ellipsis_key(v) for v in value)
    return False


def doc_examples() -> Iterator[list[dict[str, Any]]]:
    text = (CONTRACTS / "websocket.md").read_text(encoding="utf-8")
    for kind, body in re.findall(r"```(json|jsonl)\n(.*?)```", text, flags=re.S):
        lines = body.strip().splitlines() if kind == "jsonl" else [body]
        yield [json.loads(line) for line in lines]


def test_websocket_examples_follow_the_contract() -> None:
    checked = 0
    for block in doc_examples():
        for message in block:
            if "type" not in message or has_ellipsis_key(message):
                continue  # REST responses (§9) and abbreviated examples
            kind = message["type"]
            if kind in CLIENT_TYPES:
                CLIENT.validate(message)
            elif kind in SERVER_TYPES:
                SERVER.validate(message)
            else:
                check_event(message)
            checked += 1
        engine_events = [m for m in block if m.get("type") in EVENT_TYPES]
        if any(m["type"] == "turn_started" for m in engine_events):
            check_turns(engine_events)
    assert checked >= 30


@pytest.mark.parametrize(
    "message",
    [
        {"type": "user_message", "content": "   "},
        {"type": "user_message", "content": "x" * 4001},
        {"type": "user_message"},
        {"type": "confirm", "action_id": "a-1", "decision": "maybe"},
        {"type": "set_config", "patch": {}},
        {"type": "launch_missiles"},
        {"type": "cancel", "id": ""},
        # v1.1: exactly one of option_id and text (§4.8)
        {"type": "answer", "question_id": "q-1"},
        {"type": "answer", "question_id": "q-1", "option_id": None, "text": None},
        {"type": "answer", "question_id": "q-1", "option_id": "trimestre", "text": "ce trimestre"},
        {"type": "answer", "question_id": "q-1", "text": "  "},
        {"type": "answer", "question_id": "q-1", "text": "x" * 501},
        {"type": "set_persona", "persona": ""},
        {"type": "unshare"},
        {"type": "run_schedule"},
    ],
)
def test_client_schema_rejects_invalid_messages(message: dict[str, Any]) -> None:
    assert not CLIENT.is_valid(message)


def test_client_schema_ignores_unknown_fields() -> None:
    # websocket.md §3: unknown fields are ignored, for forward compatibility
    assert CLIENT.is_valid({"type": "cancel", "id": "c-1", "future_field": True})


@pytest.mark.parametrize(
    "message",
    [
        {"type": "answer", "question_id": "q-1", "option_id": "trimestre"},
        {"type": "answer", "question_id": "q-1", "option_id": None, "text": "Les deux"},
        {"type": "share"},
    ],
)
def test_client_schema_accepts_the_v1_1_messages(message: dict[str, Any]) -> None:
    assert CLIENT.is_valid(message)


def schedule_run_finished(**fields: Any) -> dict[str, Any]:
    message = {
        "type": "schedule_run_finished",
        "session_id": "s-1",
        "ts": 1760433760.0,
        "schedule_id": "point-lundi",
        "run_id": "r-12",
        "status": "ok",
        "badge": None,
        "report_path": "reports/planifie/point-lundi-2026-10-26.md",
        "error": None,
    }
    return {**message, **fields}


def test_a_failed_scheduled_run_has_an_error_and_no_report() -> None:
    failure = {"code": "OLLAMA_UNAVAILABLE", "message": "Ollama ne répond pas."}
    assert SERVER.is_valid(schedule_run_finished(status="error", report_path=None, error=failure))
    assert not SERVER.is_valid(schedule_run_finished(status="error", report_path=None))
    assert not SERVER.is_valid(schedule_run_finished(status="error", error=failure))
    assert not SERVER.is_valid(schedule_run_finished(error=failure))
