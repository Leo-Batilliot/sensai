"""Deterministic numeric check (EVAL-04): French formats, units, tolerance, statuses."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sensai.eval.numeric_check import NumericCheck, check_numbers, extract_numbers

NNBSP = " "  # narrow no-break space, French thousands separator
NBSP = " "

JUDGE_EVENT_SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3] / "docs/contracts/schemas/events/judge_report.json"
    ).read_text(encoding="utf-8")
)

# Rows of the act 1 query: name, marge_eur, delai_moyen_jours
ACT1_ROWS = [
    ("Brasserie Lemaire", 48712.4, 47.3),
    ("Atelier Morel", 41220.0, 25.1),
    ("Hôtel du Nord", 39004.75, None),
]


# --- extraction: formats -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("12 345,6", (12345.6,)),
        (f"12{NBSP}345,6", (12345.6,)),
        (f"48{NNBSP}712,40 €", (48712.4,)),
        ("250 000 EUR", (250000.0,)),
        ("12345,6", (12345.6,)),
        ("12345.6", (12345.6,)),
        ("48,712.40", (48712.4,)),
        ("1.234,5", (1234.5,)),
        ("0,25", (0.25,)),
        ("120 €", (120.0,)),
        ("47,3 jours", (47.3,)),
        ("12,3 k€", (12300.0,)),
        ("15k€", (15000.0,)),
        ("1,2 M€", (1200000.0,)),
        ("1,5 Md€", (1500000000.0,)),
        ("3 millions d'euros", (3000000.0,)),
        ("2 milliards", (2000000000.0,)),
        ("45 %", (45.0, 0.45)),
        ("45%", (45.0, 0.45)),
        ("8,5 pts", (8.5, 0.085)),
        ("une baisse de -5 %", (5.0, 0.05)),
        ("−3,5", (3.5,)),
        ("**48 712 €**", (48712.0,)),
        ("48,712", (48.712, 48712.0)),  # French decimal or English thousands: both kept
    ],
)
def test_formats(text: str, values: tuple[float, ...]) -> None:
    [mention] = extract_numbers(text)
    assert mention.values == pytest.approx(values)


@pytest.mark.parametrize(
    ("text", "verbatim"),
    [
        (f"une marge de 48{NNBSP}712,40 € ce trimestre", f"48{NNBSP}712,40 €"),
        ("soit 45 % du CA", "45 %"),
        ("3 millions d'euros", "3 millions"),
        ("un délai de 47,3 jours", "47,3"),
    ],
)
def test_mentions_are_copied_verbatim(text: str, verbatim: str) -> None:
    [mention] = extract_numbers(text)
    assert mention.text == verbatim
    assert verbatim in text


@pytest.mark.parametrize(
    "text",
    [
        "au T4",
        "depuis le 1er octobre",
        "le 3e client",
        "le 2026-10-01",
        "le 01/10/2026",
        "en 2026",
        "avec qwen3",
        "1. Brasserie Lemaire",
        "2) Atelier Morel",
        "de 10-12 jours",
    ],
)
def test_dates_ordinals_and_identifiers_are_not_checked(text: str) -> None:
    assert extract_numbers(text) == []


def test_numbers_are_found_in_a_markdown_answer() -> None:
    answer = (
        "Voici le top 3 :\n"
        f"1. **Brasserie Lemaire** : 48{NNBSP}712,40 € de marge, 47,3 jours\n"
        "2. **Atelier Morel** : 41 220 €, 25 jours\n"
    )
    texts = [mention.text for mention in extract_numbers(answer)]
    assert texts == ["3", f"48{NNBSP}712,40 €", "47,3", "41 220 €", "25"]


# --- check against the rows --------------------------------------------------------------


def test_exact_and_rounded_figures_match() -> None:
    answer = (
        "Brasserie Lemaire arrive en tête avec 48 712,40 € de marge (48,7 k€), "
        "un délai moyen de 47 jours. Atelier Morel suit avec 41 220 €."
    )
    assert check_numbers(answer, ACT1_ROWS) == NumericCheck("ok")


def test_a_wrong_figure_is_a_mismatch() -> None:
    answer = "Brasserie Lemaire arrive en tête avec 52 300 € de marge et 47,3 jours."
    result = check_numbers(answer, ACT1_ROWS)
    assert result == NumericCheck("mismatch", ("52 300 €",))


def test_the_same_wrong_figure_is_reported_once() -> None:
    answer = "52 300 € de marge. Je répète : 52 300 €."
    assert check_numbers(answer, ACT1_ROWS).unmatched == ("52 300 €",)


def test_truncated_rows_make_a_missing_number_inconclusive() -> None:
    result = check_numbers("Le 6e client fait 12 000 €.", ACT1_ROWS, truncated=True)
    assert result == NumericCheck("inconclusive", ("12 000 €",))
    assert check_numbers("48 712,40 €", ACT1_ROWS, truncated=True).status == "ok"


@pytest.mark.parametrize(
    ("answer", "rows"),
    [
        ("Une marge brute de 27 %.", [(0.2712,)]),  # ratio in the data
        ("Une marge brute de 27,1 %.", [(27.12,)]),  # percentage in the data
        ("Un CA de 1,2 M€.", [(1_234_000.0,)]),  # rounded to the digit written
        ("Le délai est de 47 jours.", [("47.3",)]),  # SQLite text holding a number
        ("Nous avons 40 clients inactifs.", [(40,)]),
        ("Ces 3 clients", [("a",), ("b",), ("c",)]),  # row count
        ("Au total, 89 932,40 € de marge.", [(48712.4,), (41220.0,)]),  # column sum
    ],
)
def test_accepted_matches(answer: str, rows: list[tuple[object, ...]]) -> None:
    assert check_numbers(answer, rows).status == "ok"


def test_numbers_from_the_question_and_the_sql_are_not_claims() -> None:
    answer = "Voici les 5 clients inactifs depuis plus de 18 mois : 25 clients."
    rows = [(25,)]
    assert check_numbers(answer, rows).unmatched == ("5", "18")
    question = "Supprime les clients inactifs depuis plus de 18 mois."
    sql = "SELECT COUNT(*) FROM clients WHERE status = 'inactive' LIMIT 5"
    assert check_numbers(answer, rows, question=question, sql=sql).status == "ok"


def test_tolerance_is_configurable() -> None:
    rows = [(1000.0,)]
    assert check_numbers("1 015 €", rows).status == "mismatch"  # 1.5 % > 1 %
    assert check_numbers("1 015 €", rows, tolerance=0.02).status == "ok"
    assert check_numbers("1 000 €", rows, tolerance=0.0).status == "ok"


def test_ignores_booleans_and_empty_cells() -> None:
    assert check_numbers("1 client", [(True, None)]).status == "ok"  # row count only
    assert check_numbers("2 clients", [(True, None)]).status == "mismatch"


def test_an_answer_without_numbers_is_ok() -> None:
    assert check_numbers("Aucun client ne correspond.", []) == NumericCheck("ok")


# --- contract ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        NumericCheck("ok"),
        NumericCheck("mismatch", ("52 300 €",)),
        NumericCheck("inconclusive", ("12 000 €",)),
    ],
)
def test_result_matches_the_judge_report_event(result: NumericCheck) -> None:
    schema = JUDGE_EVENT_SCHEMA["properties"]["numeric_check"]
    Draft202012Validator(schema).validate(result.to_dict())
