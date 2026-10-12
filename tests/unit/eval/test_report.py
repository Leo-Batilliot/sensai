"""Harness reports (EVAL-06): files, Markdown view, comparison, `sensai eval` command."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sensai.__main__ import main
from sensai.eval.report import (
    ReportError,
    compare,
    find_report,
    load_report,
    report_paths,
    to_markdown,
    write_report,
)


def result(question_id: str, ex: bool | None) -> dict[str, Any]:
    return {
        "id": question_id,
        "category": "aggregate",
        "ex": ex,
        "sql_valid": ex is not None,
        "confirmation_requested": False,
        "badge": "verified" if ex else None,
        "error": None,
    }


def report(tag: str, ex: float | None, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "tag": tag,
        "suite": "eval/golden.jsonl",
        "today": "2026-10-20",
        "seed": 42,
        "model": "qwen3:8b",
        "judge_model": "qwen3:8b",
        "questions": len(results),
        "metrics": {"ex": {"value": ex, "n": 2}, "sql_valid": {"value": 1.0, "n": 2}},
        "latency": {"turn_p50_ms": 1200, "turn_p95_ms": None},
        "results": results,
    }


V05 = report("v0.5", 0.5, [result("q001", True), result("q002", False)])
V10 = report("v1.0", 1.0, [result("q001", True), result("q002", True)])


def test_report_files_are_named_by_day_and_tag(tmp_path: Path) -> None:
    path = write_report(V05, date(2026, 10, 13), tmp_path)

    assert path == tmp_path / "2026-10-13_v0.5.json"
    assert load_report(path) == V05
    assert path.with_suffix(".md").is_file()


def test_a_tag_cannot_escape_the_reports_folder() -> None:
    with pytest.raises(ReportError, match="tag invalide"):
        report_paths("../v0.5", date(2026, 10, 13))


def test_markdown_view() -> None:
    markdown = to_markdown(V05)

    assert markdown.startswith("# Rapport d'évaluation — v0.5\n")
    assert "| EX (exactitude d'exécution) | 0.50 | 2 |" in markdown
    assert "| Rappel destructif | — | 0 |" in markdown  # absent metric
    assert "| Durée du tour p50 | 1200 |" in markdown
    assert "| q002 | aggregate | ❌ | ✅ | ❌ | — | — |" in markdown


def test_compare_shows_deltas_and_changed_questions() -> None:
    table = compare(V05, V10)

    assert "| EX (exactitude d'exécution) | 0.50 | 1.00 | +0.50 |" in table
    assert "| SQL valide | 1.00 | 1.00 | +0.00 |" in table
    assert "| Fidélité (juge) | — | — | — |" in table
    assert "- q002 : ❌ → ✅" in table
    assert "q001" not in table


def test_find_report_takes_the_latest_of_a_tag(tmp_path: Path) -> None:
    write_report(V05, date(2026, 10, 13), tmp_path)
    latest = write_report(V05, date(2026, 10, 19), tmp_path)

    assert find_report("v0.5", tmp_path) == latest
    assert find_report(str(latest)) == latest
    with pytest.raises(ReportError, match="aucun rapport"):
        find_report("v2.0", tmp_path)
    with pytest.raises(ReportError, match="introuvable"):
        find_report(str(tmp_path / "absent.json"))


def test_unreadable_report(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ReportError, match="illisible"):
        load_report(path)


# --- `sensai eval` -------------------------------------------------------------------------


def test_eval_compare_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = write_report(V05, date(2026, 10, 13), tmp_path)
    after = write_report(V10, date(2026, 10, 19), tmp_path)

    assert main(["eval", "compare", str(before), str(after)]) == 0
    assert "# Comparaison v0.5 → v1.0" in capsys.readouterr().out


def test_eval_compare_needs_two_reports(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "compare", "v0.5"]) == 2
    assert "usage" in capsys.readouterr().err


def test_eval_compare_with_an_unknown_tag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "compare", "inconnu-1", "inconnu-2"]) == 1
    assert "aucun rapport" in capsys.readouterr().err


def test_eval_run_says_what_it_waits_for(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "--suite", "eval/golden.jsonl", "--tag", "v0.5"]) == 1
    assert "MCP-04, CORE-10" in capsys.readouterr().err
