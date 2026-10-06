"""Rules of the engine events and WebSocket contracts, shared by the tests.

Transcribes docs/contracts/events.md (§3 fields, §4.3 invariants, §5 codes) and loads the
schemas of docs/contracts/websocket.md. Used by the contract tests (WEB-01) and the server
tests (WEB-04). Replace the event rules with the JSON schemas of schemas/events/ once
CORE-05 adds them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"
FIXTURES = ROOT / "tests" / "fixtures" / "events"

Check = Callable[[Any], bool]


# --- field checks ---------------------------------------------------------------------


def is_str(value: Any) -> bool:
    return isinstance(value, str)


def is_text(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_object(value: Any) -> bool:
    return isinstance(value, dict)


def nullable(check: Check) -> Check:
    return lambda value: value is None or check(value)


def one_of(*allowed: str) -> Check:
    return lambda value: value in allowed


def list_of(check: Check, max_items: int | None = None, min_items: int = 0) -> Check:
    def run(value: Any) -> bool:
        if not isinstance(value, list) or len(value) < min_items:
            return False
        if max_items is not None and len(value) > max_items:
            return False
        return all(check(item) for item in value)

    return run


def has_fields(**fields: Check) -> Check:
    return lambda value: (
        isinstance(value, dict)
        and all(name in value and check(value[name]) for name, check in fields.items())
    )


def between(low: float, high: float) -> Check:
    return lambda value: is_number(value) and low <= value <= high


def is_tool_name(value: Any) -> bool:
    # Always prefixed by the server, except native tools (events.md §3.4)
    return is_str(value) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is not None


KIND = one_of("glossary", "entity", "relation", "fact")
OPERATION = one_of("SELECT", "INSERT", "UPDATE", "DELETE", "OTHER")

# events.md §5.1
ERROR_CODES = (
    "OLLAMA_UNAVAILABLE",
    "OLLAMA_TIMEOUT",
    "MODEL_NOT_FOUND",
    "MODEL_NO_TOOLS",
    "STREAM_INTERRUPTED",
    "OLLAMA_ERROR",
    "MAX_ITERATIONS",
    "INTERNAL",
    "INVALID_MESSAGE",
    "BUSY",
    "UNKNOWN_ACTION",
    "INVALID_CONFIG",
    "UI_ACTION_FORBIDDEN",
)

IMPACT = has_fields(
    operation=OPERATION,
    rows_affected=is_count,
    per_table=lambda v: is_object(v) and all(isinstance(n, int) for n in v.values()),
    sample=list_of(is_object, max_items=5),
    blocked=list_of(has_fields(reason=is_text, count=nullable(is_count))),
)

USAGE = has_fields(
    prompt_tokens=is_count, completion_tokens=is_count, ctx_used=is_count, ctx_max=is_count
)

# judge_report.v1.json (judge.md §1, P3)
JUDGE_REPORT = has_fields(
    claims=list_of(
        has_fields(
            text=is_text,
            verdict=one_of("supported", "contradicted", "unverifiable"),
            evidence=is_str,
            expected_value=nullable(is_str),
        )
    ),
    relevance=lambda v: isinstance(v, int) and 1 <= v <= 5,
    sql_answers_question=is_bool,
    key_figures=list_of(
        has_fields(label=is_text, value=is_number, unit=is_str, source_column=nullable(is_str)),
        max_items=5,
    ),
    assumptions=list_of(is_text, max_items=3),
    followups=list_of(is_text, max_items=3),
)

# events.md §3: specific fields of each event type, all always present
EVENT_FIELDS: dict[str, dict[str, Check]] = {
    "turn_started": {"origin": one_of("user", "ui"), "user_message": nullable(is_text)},
    "context_injected": {
        "refs": list_of(has_fields(kind=KIND, ref=is_text, label=is_text), min_items=1)
    },
    "token": {"delta": is_text},
    "thinking": {"delta": is_text},
    "tool_call": {
        "call_id": is_text,
        "server": nullable(is_text),
        "tool": is_tool_name,
        "arguments": is_object,
    },
    "sql_generated": {"call_id": is_text, "sql": is_text, "operation": OPERATION},
    "confirmation_required": {
        "action_id": is_text,
        "call_id": is_text,
        "tool": is_tool_name,
        "sql": nullable(is_text),
        "impact": IMPACT,
        "expires_in_s": lambda v: is_number(v) and v > 0,
    },
    "confirmation_resolved": {
        "action_id": is_text,
        "decision": one_of("approve", "reject", "timeout"),
        "reason": one_of("user", "cancelled", "disconnected", "timeout"),
        "comment": nullable(is_str),
    },
    "tool_result": {
        "call_id": is_text,
        "ok": is_bool,
        "summary": is_str,
        "data": nullable(is_object),
        "error": nullable(has_fields(code=is_text, message=is_text)),
        "duration_ms": is_count,
    },
    "memory_updated": {
        "op": one_of("create", "update", "delete"),
        "kind": KIND,
        "ref": is_text,
        "summary": is_str,
    },
    "message_done": {
        "content": is_str,
        "interrupted": is_bool,
        "usage": nullable(USAGE),
        "duration_ms": is_count,
    },
    "judge_started": {},
    "judge_report": {
        "badge": one_of("verified", "needs_review", "contradicted", "unavailable"),
        "scores": has_fields(faithfulness=nullable(between(0, 1))),
        "numeric_check": has_fields(
            status=one_of("ok", "mismatch", "inconclusive"), unmatched=list_of(is_text)
        ),
        "report": nullable(JUDGE_REPORT),
        "duration_ms": is_count,
    },
    "error": {"code": one_of(*ERROR_CODES), "message": is_text, "recoverable": is_bool},
}
COMMON = {"type", "session_id", "turn_id", "ts"}
# Protocol errors emitted by the web layer carry reply_to (websocket.md §7.1)
OPTIONAL = {"error": {"reply_to"}}
# events.md §3.7
REASONS_BY_DECISION = {
    "approve": {"user"},
    "reject": {"user", "cancelled", "disconnected"},
    "timeout": {"timeout"},
}


def check_event(event: dict[str, Any]) -> None:
    kind = event.get("type")
    assert kind in EVENT_FIELDS, f"type inconnu : {kind!r}"
    spec = EVENT_FIELDS[kind]
    allowed = COMMON | set(spec) | OPTIONAL.get(kind, set())
    missing = (COMMON | set(spec)) - set(event)
    assert not missing, f"{kind} : champs manquants {sorted(missing)}"
    extra = set(event) - allowed
    assert not extra, f"{kind} : champs inconnus {sorted(extra)}"
    assert is_text(event["session_id"]), f"{kind}.session_id"
    assert is_number(event["ts"]), f"{kind}.ts"
    if kind == "error":
        assert nullable(is_text)(event["turn_id"]), "error.turn_id"
    else:
        assert is_text(event["turn_id"]), f"{kind}.turn_id"
    for name, check in spec.items():
        assert check(event[name]), f"{kind}.{name} invalide : {event[name]!r}"
    if kind == "tool_result":
        assert (event["error"] is None) == event["ok"], "tool_result : error doit être null si ok"
    if kind == "confirmation_resolved":
        assert event["reason"] in REASONS_BY_DECISION[event["decision"]], (
            f"confirmation_resolved : reason {event['reason']!r} impossible avec "
            f"decision {event['decision']!r}"
        )


def check_turns(events: list[dict[str, Any]]) -> None:
    """events.md §4: order and invariants of each turn."""
    turns: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("turn_id") is not None:
            turns.setdefault(event["turn_id"], []).append(event)

    for turn_id, turn in turns.items():
        types = [event["type"] for event in turn]
        where = f"tour {turn_id}"
        assert types[0] == "turn_started", f"{where} : turn_started doit être le premier"
        assert types.count("turn_started") == 1, f"{where} : un seul turn_started"
        if "context_injected" in types:
            assert types.index("context_injected") == 1, f"{where} : context_injected en 2e"

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
        run_select_ok = False
        for event in turn:
            kind = event["type"]
            if kind == "tool_call":
                tools[event["call_id"]] = event["tool"]
            elif kind in ("sql_generated", "confirmation_required"):
                assert event["call_id"] in tools, f"{where} : {kind} sans tool_call"
                assert event["call_id"] not in results, f"{where} : {kind} après tool_result"
                if kind == "confirmation_required":
                    required.add(event["action_id"])
            elif kind == "confirmation_resolved":
                assert event["action_id"] in required, f"{where} : résolution sans demande"
                required.discard(event["action_id"])
            elif kind == "tool_result":
                assert event["call_id"] in tools, f"{where} : tool_result sans tool_call"
                results.add(event["call_id"])
                if event["ok"] and tools[event["call_id"]].endswith("run_select"):
                    run_select_ok = True
        assert not required, f"{where} : confirmation jamais résolue"

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


# --- loading ----------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_schema(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "schemas" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


CLIENT = load_schema("ws_client.v1.json")
SERVER = load_schema("ws_server.v1.json")
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
