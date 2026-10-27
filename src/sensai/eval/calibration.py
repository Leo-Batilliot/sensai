"""Calibration of the judge (EVAL-07, EV1): does it really catch the hallucinations?

eval/judge_calibration.jsonl holds answers checked by hand: each claim of an answer has a
human label (supported, contradicted, unverifiable). An answer is a hallucination, the
positive class, when one of its claims is contradicted. Three detectors are measured on
the same cases (PROJECT §11.3, point 4):
- numeric: the deterministic numeric check alone (no model: reproducible offline);
- judge: the judge model alone, an answer being flagged when its report has a
  contradicted claim;
- judge_numeric: both, as in the product (badge "contradicted").

For each: precision, recall and the confusion matrix, then the cases it gets wrong, for
docs/EVALUATION.md. The badge given to each answer is also compared with the badge its
human labels imply (an unverifiable claim should give "needs_review", not "verified").
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sensai.core.events import Badge
from sensai.eval.judge import Judge, JudgeInput, compute_badge
from sensai.eval.numeric_check import DEFAULT_TOLERANCE, check_numbers

CALIBRATION_PATH = Path("eval/judge_calibration.jsonl")
DETECTORS = {
    "numeric": "Contrôle numérique seul",
    "judge": "Juge seul",
    "judge_numeric": "Juge + contrôle numérique",
}
BADGES: tuple[Badge, ...] = ("verified", "needs_review", "contradicted", "unavailable")


@dataclass(frozen=True)
class CalibrationCase:
    """One answer of the calibration set, with the human label of each of its claims."""

    id: str
    category: str  # correct, wrong_number, wrong_entity, unsourced
    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    answer: str
    claims: list[dict[str, str]]  # {text, label}
    error: str | None  # what is wrong in the answer, None if nothing

    @property
    def hallucinated(self) -> bool:
        return any(claim["label"] == "contradicted" for claim in self.claims)

    @property
    def expected_badge(self) -> Badge:
        """The badge the human labels imply (judge.md §2)."""
        labels = {claim["label"] for claim in self.claims}
        if "contradicted" in labels:
            return "contradicted"
        return "needs_review" if "unverifiable" in labels else "verified"

    def judge_input(self) -> JudgeInput:
        return JudgeInput(
            self.question, self.sql, self.columns, self.rows, self.answer, self.truncated
        )


def load_cases(path: str | Path = CALIBRATION_PATH) -> list[CalibrationCase]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [CalibrationCase(**json.loads(line)) for line in lines if line.strip()]


@dataclass(frozen=True)
class Outcome:
    """What the detectors said about one case."""

    id: str
    category: str
    hallucinated: bool
    expected_badge: Badge
    badge: Badge
    numeric_status: str
    unmatched: list[str]
    judge_ran: bool  # False for the numeric check alone
    # None: no judge in this run, or no valid report from the model (unavailable)
    judge_flagged: bool | None
    judge_claims: list[dict[str, Any]] | None

    def flagged(self, detector: str) -> bool:
        if detector == "numeric":
            return self.numeric_status == "mismatch"
        if detector == "judge":
            return bool(self.judge_flagged)
        return self.badge == "contradicted"


def numeric_outcome(case: CalibrationCase, tolerance: float = DEFAULT_TOLERANCE) -> Outcome:
    """The numeric check alone: no model, the same result on every machine."""
    numeric = check_numbers(
        case.answer,
        case.rows,
        question=case.question,
        sql=case.sql,
        truncated=case.truncated,
        tolerance=tolerance,
    )
    return Outcome(
        id=case.id,
        category=case.category,
        hallucinated=case.hallucinated,
        expected_badge=case.expected_badge,
        badge=compute_badge(None, numeric),
        numeric_status=numeric.status,
        unmatched=list(numeric.unmatched),
        judge_ran=False,
        judge_flagged=None,
        judge_claims=None,
    )


async def judge_outcome(case: CalibrationCase, judge: Judge) -> Outcome:
    """The judge of the product on one case: numeric check, then the model."""
    verdict = await judge.evaluate(case.judge_input())
    claims = verdict.report["claims"] if verdict.report else None
    return Outcome(
        id=case.id,
        category=case.category,
        hallucinated=case.hallucinated,
        expected_badge=case.expected_badge,
        badge=verdict.badge,
        numeric_status=verdict.numeric_check.status,
        unmatched=list(verdict.numeric_check.unmatched),
        judge_ran=True,
        judge_flagged=(
            None if claims is None else any(c["verdict"] == "contradicted" for c in claims)
        ),
        judge_claims=claims,
    )


async def run_calibration(
    cases: Sequence[CalibrationCase], judge: Judge | None, tolerance: float = DEFAULT_TOLERANCE
) -> list[Outcome]:
    """Every case in order; without a judge, the numeric check only."""
    if judge is None:
        return [numeric_outcome(case, tolerance) for case in cases]
    return [await judge_outcome(case, judge) for case in cases]


def confusion(outcomes: Sequence[Outcome], detector: str) -> dict[str, Any]:
    """Confusion matrix of a detector, the positive class being a hallucinated answer."""
    tp = sum(o.hallucinated and o.flagged(detector) for o in outcomes)
    fp = sum(not o.hallucinated and o.flagged(detector) for o in outcomes)
    fn = sum(o.hallucinated and not o.flagged(detector) for o in outcomes)
    tn = len(outcomes) - tp - fp - fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "n": len(outcomes),
    }


def badge_matrix(outcomes: Sequence[Outcome]) -> dict[str, dict[str, int]]:
    """Expected badge (human labels) -> given badge -> number of answers."""
    return {
        expected: {given: sum(o.expected_badge == expected and o.badge == given for o in outcomes)
                   for given in BADGES}
        for expected in BADGES[:3]
    }  # fmt: skip


def build_calibration_report(
    outcomes: Sequence[Outcome], *, tag: str, info: dict[str, Any]
) -> dict[str, Any]:
    """The JSON report: detectors measured (all three when the judge ran), their errors."""
    judged = any(o.judge_ran for o in outcomes)
    detectors = list(DETECTORS) if judged else ["numeric"]
    return {
        "version": 1,
        "tag": tag,
        **info,
        "cases": len(outcomes),
        "hallucinated": sum(o.hallucinated for o in outcomes),
        "judge_unavailable": (
            sum(o.judge_ran and o.judge_flagged is None for o in outcomes) if judged else None
        ),
        "detectors": {name: confusion(outcomes, name) for name in detectors},
        "badges": badge_matrix(outcomes) if judged else None,
        "errors": {
            name: [
                {"id": o.id, "category": o.category, "hallucinated": o.hallucinated}
                for o in outcomes
                if o.flagged(name) != o.hallucinated
            ]
            for name in detectors
        },
        "results": [asdict(outcome) for outcome in outcomes],
    }


def _rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def calibration_markdown(report: dict[str, Any]) -> str:
    """Tables ready for docs/EVALUATION.md and the slides."""
    lines = [
        f"# Calibration du juge ({report['tag']})",
        "",
        f"- Jeu : `{report.get('suite', '?')}` ({report['cases']} réponses, "
        f"dont {report['hallucinated']} avec une affirmation contredite)",
        f"- Modèle du juge : {report.get('judge_model') or 'aucun (contrôle numérique seul)'}",
        "",
        "## Détection des réponses fausses",
        "",
        "| Détecteur | Précision | Rappel | VP | FP | FN | VN |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, matrix in report["detectors"].items():
        lines.append(
            f"| {DETECTORS[name]} | {_rate(matrix['precision'])} | {_rate(matrix['recall'])} "
            f"| {matrix['tp']} | {matrix['fp']} | {matrix['fn']} | {matrix['tn']} |"
        )
    if report.get("badges"):
        lines += [
            "",
            "## Badge attendu / badge donné",
            "",
            "| Attendu | " + " | ".join(BADGES) + " |",
        ]
        lines.append("|---|" + "---:|" * len(BADGES))
        for expected, given in report["badges"].items():
            lines.append(f"| {expected} | " + " | ".join(str(given[b]) for b in BADGES) + " |")
    lines += ["", "## Erreurs des détecteurs", ""]
    for name, errors in report["errors"].items():
        listed = ", ".join(
            f"{e['id']} ({e['category']}, {'manquée' if e['hallucinated'] else 'fausse alerte'})"
            for e in errors
        )
        lines.append(f"- {DETECTORS[name]} : {listed or 'aucune'}")
    return "\n".join(lines) + "\n"


def write_calibration_report(report: dict[str, Any], json_path: Path, md_path: Path) -> Path:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(calibration_markdown(report), encoding="utf-8")
    return json_path


__all__ = [
    "CALIBRATION_PATH",
    "DETECTORS",
    "CalibrationCase",
    "Outcome",
    "badge_matrix",
    "build_calibration_report",
    "calibration_markdown",
    "confusion",
    "judge_outcome",
    "load_cases",
    "numeric_outcome",
    "run_calibration",
    "write_calibration_report",
]
