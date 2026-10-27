"""Calibration of the judge (EVAL-07): the labelled set, the three detectors, the report."""

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from sensai.__main__ import main
from sensai.core.errors import OllamaUnavailable
from sensai.eval.calibration import (
    CalibrationCase,
    build_calibration_report,
    calibration_markdown,
    confusion,
    load_cases,
    run_calibration,
)
from sensai.eval.judge import Judge
from sensai.llm.ollama_client import Chunk, Message

ROOT = Path(__file__).resolve().parents[3]
CASES = load_cases(ROOT / "eval/judge_calibration.jsonl")
LABELS = {"supported", "contradicted", "unverifiable"}


class ScriptedJudgeModel:
    """Stands in for OllamaClient.chat: one judge report per case, or an error."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        answer = messages[-1]["content"].split("## Réponse à vérifier\n", 1)[1]
        case = next(case for case in CASES if case.answer == answer)
        reply = self.reply(case)
        if isinstance(reply, Exception):
            raise reply
        return Chunk(content=json.dumps(reply, ensure_ascii=False), done=True, done_reason="stop")


def report_with(case: CalibrationCase, verdict: Any) -> dict[str, Any]:
    claims = [
        {"text": c["text"], "verdict": verdict(c), "evidence": "lignes", "expected_value": None}
        for c in case.claims
    ]
    return {
        "claims": claims,
        "relevance": 5,
        "sql_answers_question": True,
        "key_figures": [],
        "assumptions": [],
        "followups": [],
    }


def judge(reply: Any) -> Judge:
    return Judge(ScriptedJudgeModel(reply), "juge")


# --- the labelled set -----------------------------------------------------------------


def test_the_set_has_the_planned_mix_of_errors() -> None:
    # EVAL-07: about 8 wrong figures, 3 wrong entities, 2 unsourced claims, correct answers
    assert len(CASES) == 20
    assert Counter(case.category for case in CASES) == {
        "correct": 7,
        "wrong_number": 8,
        "wrong_entity": 3,
        "unsourced": 2,
    }
    assert len({case.id for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_each_case_is_labelled_claim_by_claim(case: CalibrationCase) -> None:
    assert case.claims
    for claim in case.claims:
        assert claim["text"] in case.answer
        assert claim["label"] in LABELS
    assert case.hallucinated == (case.category in {"wrong_number", "wrong_entity"})
    assert (case.error is None) == (case.category == "correct")
    assert len(case.rows[0]) == len(case.columns)


# --- detectors -------------------------------------------------------------------------


async def test_the_numeric_check_alone_on_the_set() -> None:
    # Reproducible without a model: the figures of docs/EVALUATION.md
    outcomes = await run_calibration(CASES, None)

    assert confusion(outcomes, "numeric") == {
        "tp": 7,
        "fp": 0,
        "fn": 4,
        "tn": 9,
        "precision": 1.0,
        "recall": 0.6364,
        "n": 20,
    }
    missed = {o.id for o in outcomes if o.hallucinated and not o.flagged("numeric")}
    assert missed == {"c04", "c10", "c18", "c19"}  # swapped entities, and a figure within 1 %


async def test_a_perfect_judge_catches_everything() -> None:
    outcomes = await run_calibration(
        CASES, judge(lambda case: report_with(case, lambda c: c["label"]))
    )

    for detector in ("judge", "judge_numeric"):
        assert (
            confusion(outcomes, detector)["precision"],
            confusion(outcomes, detector)["recall"],
        ) == (1.0, 1.0)
    assert all(o.badge == o.expected_badge for o in outcomes)


async def test_a_blind_judge_leaves_only_the_numeric_check() -> None:
    blind = judge(lambda case: report_with(case, lambda c: "supported"))
    outcomes = await run_calibration(CASES, blind)

    assert confusion(outcomes, "judge")["tp"] == 0
    assert confusion(outcomes, "judge_numeric") == {**confusion(outcomes, "numeric")}


async def test_an_unavailable_judge_is_counted_apart() -> None:
    outcomes = await run_calibration(CASES, judge(lambda case: OllamaUnavailable("http://x")))
    report = build_calibration_report(outcomes, tag="t", info={})

    assert report["judge_unavailable"] == 20
    assert report["detectors"]["judge"]["tp"] == 0
    # The badge still says "contradicted" when the numeric check finds a wrong figure
    assert report["badges"]["contradicted"]["contradicted"] == 7
    assert report["badges"]["verified"]["unavailable"] == 7


async def test_report_and_tables() -> None:
    outcomes = await run_calibration(
        CASES, judge(lambda case: report_with(case, lambda c: c["label"]))
    )
    report = build_calibration_report(outcomes, tag="v0.8", info={"judge_model": "juge"})
    markdown = calibration_markdown(report)

    assert set(report["detectors"]) == {"numeric", "judge", "judge_numeric"}
    assert report["badges"]["needs_review"]["needs_review"] == 2  # the unsourced claims
    assert report["errors"]["judge"] == []
    assert "| Juge + contrôle numérique | 1.00 | 1.00 | 11 | 0 | 0 | 9 |" in markdown
    assert "- Contrôle numérique seul : c04 (wrong_entity, manquée)" in markdown


def test_calibrate_without_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "eval").mkdir()
    shutil.copy(ROOT / "eval/judge_calibration.jsonl", tmp_path / "eval")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SENSAI_CONFIG", raising=False)

    assert main(["eval", "calibrate", "--numeric-only", "--tag", "essai"]) == 0

    assert "numeric : précision 1.0, rappel 0.6364" in capsys.readouterr().out
    (written,) = (tmp_path / "eval/reports").glob("*_essai.json")
    assert json.loads(written.read_text(encoding="utf-8"))["detectors"]["numeric"]["tp"] == 7
