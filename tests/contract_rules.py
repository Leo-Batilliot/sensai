"""Rules of the engine events and WebSocket contracts, shared by the tests.

- One event at a time: the official JSON schemas of docs/contracts/schemas/events/ (P1,
  CORE-05), and judge_report.v1.json (P3) for the report inside a judge_report event.
- A whole turn (order, tokens = content, confirmations resolved...): `check_turns`, which
  transcribes events.md §4.3, since a schema only sees one event.

Used by the contract tests (WEB-01) and the server tests (WEB-04).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"
FIXTURES = ROOT / "tests" / "fixtures" / "events"


# --- loading ------------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_schema(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


EVENT_SCHEMAS = {
    path.stem: load_schema(path)
    for path in sorted((CONTRACTS / "schemas" / "events").glob("*.json"))
}
EVENT_TYPES = frozenset(EVENT_SCHEMAS)
JUDGE_REPORT = load_schema(CONTRACTS / "schemas" / "judge_report.v1.json")

CLIENT = load_schema(CONTRACTS / "schemas" / "ws_client.v1.json")
SERVER = load_schema(CONTRACTS / "schemas" / "ws_server.v1.json")
CLIENT_TYPES = {
    "user_message",
    "ui_action",
    "confirm",
    "cancel",
    "reset",
    "set_model",
    "set_config",
}
SERVER_TYPES = {"session_ready", "session_reset", "model_changed", "config_updated"}

# Emitted before the first model call of a turn, in this order (events.md §4.1, point 2)
PRELUDE = ("context_injected", "context_compacted", "examples_injected")
# Native tools whose successful result is followed by document_updated (events.md §3.17)
DOCUMENT_TOOLS = {"document_patch", "document_delete_section", "document_restore"}


# --- one event ----------------------------------------------------------------------------


def check_event(event: dict[str, Any]) -> None:
    """The event follows its official schema (events.md §3)."""
    kind = event.get("type")
    assert kind in EVENT_SCHEMAS, f"type inconnu : {kind!r}"
    _first_error(EVENT_SCHEMAS[kind], event, kind)
    if kind == "judge_report" and event["report"] is not None:
        _first_error(JUDGE_REPORT, event["report"], "judge_report.report")
    # What a schema cannot compare (events.md §4.3)
    if kind == "context_compacted":
        assert event["tokens_after"] < event["tokens_before"], "compression sans gain"


def _first_error(validator: Draft202012Validator, value: Any, where: str) -> None:
    error = next(iter(sorted(validator.iter_errors(value), key=str)), None)
    if error is not None:
        path = "/".join(str(part) for part in error.absolute_path) or "(racine)"
        raise AssertionError(f"{where} : {path} : {error.message}")


# --- a whole turn -------------------------------------------------------------------------


def check_turns(events: list[dict[str, Any]], max_questions: int = 1) -> None:
    """events.md §4: order and invariants of each turn.

    `max_questions` is `[agent] max_questions_per_turn` (1 by default).
    """
    turns: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("turn_id") is not None:
            turns.setdefault(event["turn_id"], []).append(event)

    for turn_id, turn in turns.items():
        types = [event["type"] for event in turn]
        where = f"tour {turn_id}"
        assert types[0] == "turn_started", f"{where} : turn_started doit être le premier"
        assert types.count("turn_started") == 1, f"{where} : un seul turn_started"
        prelude = [kind for kind in PRELUDE if kind in types]
        for kind in prelude:
            assert types.count(kind) == 1, f"{where} : un seul {kind}"
        assert types[1 : 1 + len(prelude)] == prelude, f"{where} : ordre de {', '.join(PRELUDE)}"

        ends = [i for i, t in enumerate(types) if t in ("message_done", "error")]
        assert len(ends) == 1, f"{where} : une seule fin (message_done ou error)"
        after_end = set(types[ends[0] + 1 :])
        assert after_end <= {"judge_started", "judge_report"}, f"{where} : événements après la fin"

        start = turn[0]
        from_ui = start["origin"] == "ui"
        assert (start["user_message"] is None) == from_ui, f"{where} : user_message et origin"

        tools: dict[str, str] = {}
        results: set[str] = set()
        required: set[str] = set()
        asked: dict[str, str] = {}  # question_id -> call_id of its ask_user call
        pending: set[str] = set()
        last_result: dict[str, Any] | None = None
        run_select_ok = False
        for event in turn:
            kind = event["type"]
            if kind == "tool_call":
                tools[event["call_id"]] = event["tool"]
            elif kind in ("sql_generated", "confirmation_required"):
                assert event["call_id"] in tools, f"{where} : {kind} sans tool_call"
                assert event["call_id"] not in results, f"{where} : {kind} après tool_result"
                assert tools[event["call_id"]] != "ask_user", f"{where} : {kind} pour ask_user"
                if kind == "confirmation_required":
                    required.add(event["action_id"])
            elif kind == "confirmation_resolved":
                assert event["action_id"] in required, f"{where} : résolution sans demande"
                required.discard(event["action_id"])
            elif kind == "question_asked":
                call_id = event["call_id"]
                assert tools.get(call_id) == "ask_user", f"{where} : question sans appel ask_user"
                assert call_id not in results, f"{where} : question après tool_result"
                asked[event["question_id"]] = call_id
                pending.add(event["question_id"])
            elif kind == "question_answered":
                assert event["question_id"] in pending, f"{where} : réponse sans question"
                pending.discard(event["question_id"])
            elif kind == "tool_result":
                assert event["call_id"] in tools, f"{where} : tool_result sans tool_call"
                waiting = [q for q in pending if asked[q] == event["call_id"]]
                assert not waiting, f"{where} : tool_result avant question_answered"
                results.add(event["call_id"])
                last_result = event
                if event["ok"] and tools[event["call_id"]].endswith("run_select"):
                    run_select_ok = True
            elif kind == "document_updated":
                written = last_result is not None and last_result["ok"]
                assert written, f"{where} : {kind} sans écriture"
                tool = tools[last_result["call_id"]]
                assert tool in DOCUMENT_TOOLS, f"{where} : {kind} après {tool}"
        assert not required, f"{where} : confirmation jamais résolue"
        assert not pending, f"{where} : question jamais résolue"
        assert len(asked) <= max_questions, f"{where} : plus de {max_questions} question(s)"

        done = next((e for e in turn if e["type"] == "message_done"), None)
        judged = types.count("judge_started")
        assert judged <= 1 and types.count("judge_report") == judged, f"{where} : juge"
        if judged:
            assert types.index("judge_started") < types.index("judge_report"), f"{where} : juge"
            assert done is not None and not done["interrupted"], f"{where} : juge sans réponse"
            assert not from_ui, f"{where} : pas de juge pour un tour ui"
            assert run_select_ok, f"{where} : juge sans run_select réussi"

        if done is not None:
            tokens = "".join(e["delta"] for e in turn if e["type"] == "token")
            assert tokens == done["content"], f"{where} : token ≠ message_done.content"
            # Ollama only gives the counters in the last chunk: none for a ui turn or after cancel
            no_counters = from_ui or done["interrupted"]
            assert (done["usage"] is None) == no_counters, f"{where} : usage, origin et interrupted"
            report = next((e for e in turn if e["type"] == "judge_report"), None)
            if report is not None:
                quoted = list(report["numeric_check"]["unmatched"])
                if isinstance(report["report"], dict) and "claims" in report["report"]:
                    quoted += [claim["text"] for claim in report["report"]["claims"]]
                for text in quoted:
                    assert text in done["content"], f"{where} : {text!r} absent de la réponse"
