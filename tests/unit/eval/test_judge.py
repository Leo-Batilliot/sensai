"""LLM judge (EVAL-03): scores and badge computed by the code, structured call, fixtures.

The model is simulated with the stub_report of each case (tests/fixtures/judge/): these
tests prove the numeric check catches the injected numeric errors on its own, and that
the badge follows the verdicts. The last test runs the real model (marker ollama).
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from sensai.core.errors import OllamaTimeout, OllamaUnavailable
from sensai.eval.judge import (
    MAX_ROWS,
    Judge,
    JudgeInput,
    build_messages,
    compute_badge,
    faithfulness,
    load_prompt,
    load_report_schema,
)
from sensai.eval.numeric_check import NumericCheck
from sensai.llm.ollama_client import Chunk, Message, OllamaClient

MODEL = "qwen3:8b"
ROOT = Path(__file__).resolve().parents[3]
CASES = [
    json.loads(line)
    for line in (ROOT / "tests/fixtures/judge/cases.jsonl").read_text(encoding="utf-8").splitlines()
]
REPORT_VALIDATOR = Draft202012Validator(load_report_schema())
EVENT_VALIDATOR = Draft202012Validator(
    json.loads(
        (ROOT / "docs/contracts/schemas/events/judge_report.json").read_text(encoding="utf-8")
    )
)

OK = NumericCheck("ok")
MISMATCH = NumericCheck("mismatch", ("52 300 €",))
INCONCLUSIVE = NumericCheck("inconclusive", ("87",))


def claim(verdict: str) -> dict[str, Any]:
    expected = "48 712,40 €" if verdict == "contradicted" else None
    return {"text": "x", "verdict": verdict, "evidence": "e", "expected_value": expected}


def report(*verdicts: str) -> dict[str, Any]:
    return {
        "claims": [claim(verdict) for verdict in verdicts],
        "relevance": 5,
        "sql_answers_question": True,
        "key_figures": [],
        "assumptions": [],
        "followups": [],
    }


def as_input(case: dict[str, Any]) -> JudgeInput:
    return JudgeInput(
        question=case["question"],
        sql=case["sql"],
        columns=case["columns"],
        rows=case["rows"],
        answer=case["answer"],
        truncated=case["truncated"],
    )


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
            {"model": model, "messages": messages, "format": format, "options": options}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return Chunk(content=content, done=True, done_reason="stop")


def envelope_is_valid(verdict_dict: dict[str, Any]) -> bool:
    event = {"type": "judge_report", "session_id": "s-1", "turn_id": "t-1", "ts": 1.0}
    return EVENT_VALIDATOR.is_valid({**event, **verdict_dict})


# --- faithfulness and badge (judge.md §2) ------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (report(), None),
        (report("supported"), 1.0),
        (report("supported", "supported", "contradicted", "unverifiable"), 0.5),
        (report("unverifiable"), 0.0),
    ],
)
def test_faithfulness(value: dict[str, Any] | None, expected: float | None) -> None:
    assert faithfulness(value) == expected


@pytest.mark.parametrize(
    ("value", "numeric", "badge"),
    [
        # rule 1 comes first, even without a report
        (report("supported", "contradicted"), OK, "contradicted"),
        (report("supported"), MISMATCH, "contradicted"),
        (None, MISMATCH, "contradicted"),
        # rule 2
        (None, OK, "unavailable"),
        (None, INCONCLUSIVE, "unavailable"),
        # rule 3 needs at least one claim, all supported, and a green numeric check
        (report("supported", "supported"), OK, "verified"),
        (report(), OK, "needs_review"),
        (report("supported", "unverifiable"), OK, "needs_review"),
        # a truncated result is never contradicted by the numeric check
        (report("supported"), INCONCLUSIVE, "needs_review"),
    ],
)
def test_badge_rules(value: dict[str, Any] | None, numeric: NumericCheck, badge: str) -> None:
    assert compute_badge(value, numeric) == badge


# --- fixtures ----------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fixture_stub_reports_follow_the_contract(case: dict[str, Any]) -> None:
    REPORT_VALIDATOR.validate(case["stub_report"])
    for item in case["stub_report"]["claims"]:
        assert item["text"] in case["answer"], "claims[].text must be copied verbatim"
    assert (case["injected_error"] is None) == (case["expected_badge"] != "contradicted")


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_judge_gives_the_expected_badge(case: dict[str, Any]) -> None:
    judge = Judge(FakeChat(case["stub_report"]), MODEL)

    verdict = await judge.evaluate(as_input(case))

    assert verdict.badge == case["expected_badge"]
    assert verdict.report == case["stub_report"]
    assert envelope_is_valid(verdict.to_dict())


async def test_key_figures_absent_from_their_column_are_dropped() -> None:
    # qwen3:8b on 30/10: a card read 152 334 € for a margin of 1 523,34 €
    case = JudgeInput(
        question="Marge brute de Cabinet Roussel ce trimestre ?",
        sql="SELECT name, marge_brute FROM marges",
        columns=["name", "marge_brute"],
        rows=[["Cabinet Roussel", 1523.34]],
        answer="La marge brute de Cabinet Roussel est de 1 523,34 €.",
    )
    right = {
        "label": "Marge brute",
        "value": 1523.34,
        "unit": "EUR",
        "source_column": "marge_brute",
    }
    wrong = {**right, "value": 152334}
    stub = {**report("supported"), "key_figures": [wrong, right]}

    verdict = await Judge(FakeChat(stub), MODEL).evaluate(case)

    assert verdict.report is not None
    assert verdict.report["key_figures"] == [right]
    assert verdict.badge == "verified"  # the cards never change the badge
    assert envelope_is_valid(verdict.to_dict())


NUMERIC_CASES = [case for case in CASES if "numeric" in case["detected_by"]]


@pytest.mark.parametrize("case", NUMERIC_CASES, ids=[case["id"] for case in NUMERIC_CASES])
async def test_numeric_check_catches_what_a_blind_judge_misses(case: dict[str, Any]) -> None:
    blind = {
        **case["stub_report"],
        "claims": [
            {**item, "verdict": "supported", "expected_value": None}
            for item in case["stub_report"]["claims"]
        ],
    }
    verdict = await Judge(FakeChat(blind), MODEL).evaluate(as_input(case))

    assert verdict.badge == "contradicted"
    assert verdict.numeric_check.status == "mismatch"
    assert verdict.scores == {"faithfulness": 1.0}


@pytest.mark.parametrize("case", NUMERIC_CASES, ids=[case["id"] for case in NUMERIC_CASES])
async def test_numeric_errors_are_caught_without_the_model(case: dict[str, Any]) -> None:
    verdict = await Judge(FakeChat(OllamaUnavailable("http://x")), MODEL).evaluate(as_input(case))

    assert verdict.report is None
    assert verdict.badge == "contradicted"
    assert envelope_is_valid(verdict.to_dict())


# --- structured call: repair and clean degradation ---------------------------------------


async def test_invalid_json_is_repaired_once_with_the_error() -> None:
    case = CASES[0]
    chat = FakeChat("{pas du json", case["stub_report"])

    verdict = await Judge(chat, MODEL).evaluate(as_input(case))

    assert verdict.badge == "verified"
    assert len(chat.calls) == 2
    repair = chat.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": "{pas du json"}
    assert "JSON invalide" in repair[-1]["content"]


async def test_report_out_of_schema_is_repaired() -> None:
    case = CASES[0]
    chat = FakeChat({**case["stub_report"], "relevance": 7}, case["stub_report"])

    verdict = await Judge(chat, MODEL).evaluate(as_input(case))

    assert verdict.report == case["stub_report"]
    assert "relevance" in chat.calls[1]["messages"][-1]["content"]


async def test_report_still_invalid_after_repair_gives_unavailable() -> None:
    case = CASES[0]
    chat = FakeChat("{}", '{"claims": []}')

    verdict = await Judge(chat, MODEL).evaluate(as_input(case))

    assert len(chat.calls) == 2
    assert verdict.report is None
    assert verdict.badge == "unavailable"
    assert verdict.scores == {"faithfulness": None}
    assert envelope_is_valid(verdict.to_dict())


async def test_judge_counts_valid_json_for_the_harness() -> None:
    # "JSON valide" metric of EVAL-06: first try, after a repair, still invalid
    case = CASES[0]
    judge = Judge(
        FakeChat(case["stub_report"], "{pas du json", case["stub_report"], "{}", "{}"), MODEL
    )

    for _ in range(3):
        await judge.evaluate(as_input(case))

    assert judge.stats.to_dict() == {"first_try": 1, "repaired": 1, "failed": 1}


@pytest.mark.parametrize("error", [OllamaUnavailable("http://x"), OllamaTimeout(5)])
async def test_model_failure_never_raises(error: Exception) -> None:
    verdict = await Judge(FakeChat(error), MODEL).evaluate(as_input(CASES[0]))

    assert verdict.report is None
    assert verdict.badge == "unavailable"


# --- what the model receives -------------------------------------------------------------


def test_prompt_describes_the_schema_and_the_verbatim_rule() -> None:
    prompt = load_prompt()
    for key in REPORT_VALIDATOR.schema["required"]:
        assert key in prompt
    assert "mot pour mot" in prompt


def test_messages_hold_question_sql_rows_and_answer() -> None:
    case = CASES[0]
    system, user = build_messages(as_input(case), "PROMPT")

    assert system == {"role": "system", "content": "PROMPT"}
    for part in (case["question"], case["sql"], case["answer"], "Brasserie Lemaire", "48712.4"):
        assert part in user["content"]
    assert "résultat complet : 3 lignes" in user["content"]


def test_rows_are_capped_and_the_model_is_told() -> None:
    rows = [[f"client {index}", index] for index in range(MAX_ROWS + 30)]
    case = JudgeInput("q", "SELECT 1", ["name", "n"], rows, "réponse")

    content = build_messages(case, "PROMPT")[1]["content"]

    assert f"client {MAX_ROWS - 1}" in content
    assert f'client {MAX_ROWS}"' not in content
    assert f"seules les {MAX_ROWS} premières lignes" in content


async def test_http_request_uses_format_schema_and_temperature_zero() -> None:
    case = CASES[0]
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = {
            "message": {"role": "assistant", "content": json.dumps(case["stub_report"])},
            "done": True,
            "done_reason": "stop",
        }
        return httpx.Response(200, json=body)

    async with OllamaClient("http://ollama.test", transport=httpx.MockTransport(handler)) as client:
        verdict = await Judge(client, MODEL, num_ctx=8192).evaluate(as_input(case))

    assert verdict.badge == "verified"
    (payload,) = requests
    assert payload["model"] == MODEL
    assert payload["stream"] is False
    assert payload["format"] == load_report_schema()
    assert payload["options"] == {"temperature": 0, "num_ctx": 8192}
    assert payload["messages"][0] == {"role": "system", "content": load_prompt()}


# --- real model --------------------------------------------------------------------------

INJECTED = [case for case in CASES if case["injected_error"] is not None]


@pytest.mark.ollama
@pytest.mark.parametrize("case", INJECTED, ids=[case["id"] for case in INJECTED])
async def test_real_judge_detects_injected_errors(case: dict[str, Any]) -> None:
    host = os.environ.get("SENSAI_OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("SENSAI_OLLAMA_MODEL", MODEL)

    async with OllamaClient(host) as client:
        verdict = await Judge(client, model).evaluate(as_input(case))

    assert verdict.report is not None, "rapport invalide après réparation"
    verdicts = [item["verdict"] for item in verdict.report["claims"]]
    assert "contradicted" in verdicts, case["injected_error"]
    assert verdict.badge == "contradicted"
