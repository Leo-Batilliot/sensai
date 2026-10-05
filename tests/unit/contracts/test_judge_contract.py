"""Contract checks on docs/contracts/schemas/judge_report.v1.json (EVAL-01)."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs/contracts/schemas/judge_report.v1.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)

VALID_REPORT: dict[str, Any] = {
    "claims": [
        {
            "text": "Brasserie Lemaire arrive en tête avec 48 712,40 € de marge",
            "verdict": "supported",
            "evidence": "ligne 1 : marge_eur = 48712.4",
            "expected_value": None,
        },
        {
            "text": "Atelier Morel suit avec 45 000 €",
            "verdict": "contradicted",
            "evidence": "ligne 2 : marge_eur = 41220.0",
            "expected_value": "41 220,00 €",
        },
    ],
    "relevance": 5,
    "sql_answers_question": True,
    "key_figures": [
        {"label": "Marge", "value": 48712.4, "unit": "EUR", "source_column": "marge_eur"}
    ],
    "assumptions": ["Délai moyen calculé sur tout l'historique"],
    "followups": ["Comparer avec le trimestre précédent ?"],
}

DELETE = object()


def mutated(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    report = copy.deepcopy(VALID_REPORT)
    target: Any = report
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return report


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_valid_report_is_accepted() -> None:
    VALIDATOR.validate(VALID_REPORT)


def test_empty_lists_are_accepted() -> None:
    VALIDATOR.validate(
        {**VALID_REPORT, "claims": [], "key_figures": [], "assumptions": [], "followups": []}
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("assumptions",), DELETE),
        (("claims", 0, "expected_value"), DELETE),
        (("claims", 0, "verdict"), "probable"),
        (("claims", 0, "text"), ""),
        (("claims", 0, "confidence"), 0.9),
        (("relevance",), 0),
        (("relevance",), 6),
        (("relevance",), 4.5),
        (("key_figures", 0, "value"), "48 712,4"),
        (("key_figures",), [VALID_REPORT["key_figures"][0]] * 6),
        (("assumptions",), ["a", "b", "c", "d"]),
        (("followups",), ["a", "b", "c", "d"]),
        (("faithfulness",), 0.9),  # scores are computed by code, never by the model
    ],
)
def test_invalid_report_is_rejected(path: tuple[Any, ...], value: Any) -> None:
    assert list(VALIDATOR.iter_errors(mutated(path, value)))
