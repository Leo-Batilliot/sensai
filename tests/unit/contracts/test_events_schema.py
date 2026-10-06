"""Contract checks: sensai.core.events against docs/contracts/schemas/events/ (CORE-05)."""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from sensai.core import errors
from sensai.core import events as ev

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "docs/contracts/schemas/events"
SCHEMAS: dict[str, dict[str, Any]] = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(SCHEMAS_DIR.glob("*.json"))
}
# Fields that the web layer may add on top of the engine event (websocket.md §7.1)
OPTIONAL_FIELDS = {"error": {"reply_to"}}

COMMON = {"session_id": "s-1", "turn_id": "t-1", "ts": 1760433000.0}
SAMPLES: list[ev.Event] = [
    ev.TurnStarted(**COMMON, origin="user", user_message="Quels sont nos 5 meilleurs clients ?"),
    ev.TurnStarted(**COMMON, origin="ui", user_message=None),
    ev.ContextInjected(
        **COMMON,
        refs=[ev.MemoryRef(kind="glossary", ref="client rentable", label="client rentable")],
    ),
    ev.Token(**COMMON, delta="Voici"),
    ev.Thinking(**COMMON, delta="L'utilisateur veut"),
    ev.ToolCall(
        **COMMON,
        call_id="k-2",
        server="sql_toolbox",
        tool="sql_toolbox__run_select",
        arguments={"sql": "SELECT 1"},
    ),
    ev.ToolCall(
        **COMMON, call_id="k-9", server=None, tool="memory_delete", arguments={"kind": "fact"}
    ),
    ev.SqlGenerated(**COMMON, call_id="k-2", sql="SELECT 1", operation="SELECT"),
    ev.ConfirmationRequired(
        **COMMON,
        action_id="a-3",
        call_id="k-7",
        tool="sql_toolbox__execute_write",
        sql="DELETE FROM clients WHERE status = 'inactive'",
        impact={
            "operation": "DELETE",
            "rows_affected": 27,
            "per_table": {"clients": -27, "contacts": -41},
            "sample": [{"id": 12, "name": "Atelier Morel"}],
            "blocked": [{"reason": "factures à conserver", "count": None}],
        },
        expires_in_s=120,
    ),
    ev.ConfirmationResolved(
        **COMMON, action_id="a-3", decision="reject", reason="user", comment="Pas avant la clôture."
    ),
    ev.ConfirmationResolved(
        **COMMON, action_id="a-3", decision="timeout", reason="timeout", comment=None
    ),
    ev.ToolResult(
        **COMMON,
        call_id="k-2",
        ok=True,
        summary="1 ligne",
        data={"columns": ["n"], "rows": [[1]], "row_count": 1, "truncated": False},
        error=None,
        duration_ms=380,
    ),
    ev.ToolResult(
        **COMMON,
        call_id="k-7",
        ok=False,
        summary="Action refusée",
        data=None,
        error=ev.ToolError(code="ACTION_REJECTED", message="Suppression refusée."),
        duration_ms=0,
    ),
    ev.MemoryUpdated(**COMMON, op="delete", kind="fact", ref="17", summary="Fait n°17 supprimé"),
    ev.MessageDone(
        **COMMON,
        content="Voici",
        interrupted=False,
        usage=ev.ContextUsage(
            prompt_tokens=2900, completion_tokens=512, ctx_used=3412, ctx_max=8192
        ),
        duration_ms=4100,
    ),
    ev.MessageDone(**COMMON, content="Voi", interrupted=True, usage=None, duration_ms=900),
    ev.JudgeStarted(**COMMON),
    ev.JudgeReport(
        **COMMON,
        badge="needs_review",
        scores={"faithfulness": None},
        numeric_check={"status": "inconclusive", "unmatched": ["52 300 €"]},
        report=None,
        duration_ms=2100,
    ),
    ev.Error(**COMMON, code="OLLAMA_UNAVAILABLE", message="Ollama injoignable.", recoverable=True),
    ev.Error(
        session_id="s-1", turn_id=None, ts=1.0, code="BUSY", message="Occupé.", recoverable=True
    ),
]


def validator(event_type: str) -> Draft202012Validator:
    return Draft202012Validator(SCHEMAS[event_type])


def is_valid(data: dict[str, Any]) -> bool:
    return validator(data["type"]).is_valid(data)


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(SCHEMAS[name])
    assert SCHEMAS[name]["properties"]["type"] == {"const": name}


def test_one_schema_per_event_class() -> None:
    assert set(SCHEMAS) == set(ev.EVENT_TYPES)


@pytest.mark.parametrize("name", sorted(ev.EVENT_TYPES))
def test_dataclass_fields_match_the_schema(name: str) -> None:
    fields = {"type"} | {field.name for field in dataclasses.fields(ev.EVENT_TYPES[name])}
    optional = OPTIONAL_FIELDS.get(name, set())
    schema = SCHEMAS[name]
    assert set(schema["properties"]) == fields | optional
    assert set(schema["required"]) == fields


def test_every_event_type_has_a_sample() -> None:
    assert {sample.type for sample in SAMPLES} == set(ev.EVENT_TYPES)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample.type)
def test_samples_match_their_schema(sample: ev.Event) -> None:
    data = json.loads(json.dumps(sample.to_dict()))  # what actually goes over the wire
    errors_found = [error.message for error in validator(sample.type).iter_errors(data)]
    assert errors_found == []


def test_protocol_error_may_carry_reply_to() -> None:
    data = {**SAMPLES[-1].to_dict(), "reply_to": "c-12"}
    assert is_valid(data)


def changed(sample_type: str, **fields: Any) -> dict[str, Any]:
    sample = next(sample for sample in SAMPLES if sample.type == sample_type)
    return {**sample.to_dict(), **fields}


@pytest.mark.parametrize(
    "data",
    [
        changed("turn_started", origin="ui", user_message="Bonjour"),
        changed("turn_started", origin="user", user_message=None),
        changed("confirmation_resolved", decision="approve", reason="cancelled"),
        changed("confirmation_resolved", decision="timeout", reason="user"),
        changed("tool_result", ok=True, error={"code": "TOOL_FAILED", "message": "boom"}),
        changed("tool_result", ok=False, error=None),
        changed("message_done", interrupted=True),  # usage of a full answer
        changed("error", code="NOT_IN_THE_LIST"),
        changed("token", delta=""),
        changed("tool_call", tool="nom avec espaces"),
        changed("token", extra="champ inconnu"),
        {k: v for k, v in SAMPLES[0].to_dict().items() if k != "user_message"},
    ],
    ids=[
        "ui-turn-with-message",
        "user-turn-without-message",
        "approve-not-by-user",
        "timeout-not-timeout",
        "ok-with-error",
        "failure-without-error",
        "interrupted-with-usage",
        "unknown-error-code",
        "empty-delta",
        "invalid-tool-name",
        "extra-field",
        "missing-field",
    ],
)
def test_contract_violations_are_rejected(data: dict[str, Any]) -> None:
    assert not is_valid(data)


# Errors that the Engine turns into `error` events: those of the Ollama client, plus the
# base class (INTERNAL). MCP errors (McpServerDied, McpTimeout...) never become `error`
# events: the registry turns them into failed tool_result events (MCP-04, events.md §5.2).
ENGINE_ERRORS = (
    errors.SensaiError,
    errors.OllamaUnavailable,
    errors.ModelNotFound,
    errors.OllamaTimeout,
    errors.StreamInterrupted,
    errors.OllamaError,
)


@pytest.mark.parametrize("error", ENGINE_ERRORS, ids=lambda cls: cls.__name__)
def test_engine_error_codes_are_in_the_closed_list(error: type[errors.SensaiError]) -> None:
    assert error.code in SCHEMAS["error"]["properties"]["code"]["enum"]
