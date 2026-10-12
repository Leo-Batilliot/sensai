"""Harness reports (EVAL-06): eval/reports/<date>_<tag>.{json,md}, and their comparison.

The JSON report is the reference (build_report in harness.py); the Markdown one is its
readable view, for docs/EVALUATION.md and the slides. `compare` puts two reports side
by side: metric deltas, then the questions whose EX changed.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

REPORTS_DIR = Path("eval/reports")
TAG = re.compile(r"^[A-Za-z0-9._-]{1,40}$")

METRIC_LABELS = {
    "ex": "EX (exactitude d'exécution)",
    "sql_valid": "SQL valide",
    "destructive_recall": "Rappel destructif",
    "hitl_false_positives": "Faux positifs HITL",
    "faithfulness": "Fidélité (juge)",
    "relevance": "Pertinence (juge, 1 à 5)",
    "turn_errors": "Tours terminés en erreur",
}
LATENCY_LABELS = {
    "turn_p50_ms": "Durée du tour p50",
    "turn_p95_ms": "Durée du tour p95",
    "first_token_p50_ms": "Premier token p50",
    "judge_p50_ms": "Juge p50",
}


class ReportError(Exception):
    """Unknown tag or unreadable report. The message is shown to the user as is."""


def check_tag(tag: str) -> str:
    if not TAG.match(tag):
        raise ReportError(f"tag invalide : {tag!r} (lettres, chiffres, « . », « _ », « - »)")
    return tag


def report_paths(tag: str, day: date, directory: Path = REPORTS_DIR) -> tuple[Path, Path]:
    stem = f"{day.isoformat()}_{check_tag(tag)}"
    return directory / f"{stem}.json", directory / f"{stem}.md"


def write_report(report: dict[str, Any], day: date, directory: Path = REPORTS_DIR) -> Path:
    """Write the JSON and Markdown reports; return the JSON path."""
    json_path, md_path = report_paths(report["tag"], day, directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path


def find_report(tag_or_path: str, directory: Path = REPORTS_DIR) -> Path:
    """A report path as given, or the latest report of a tag in the reports folder."""
    path = Path(tag_or_path)
    if path.suffix == ".json":
        if not path.is_file():
            raise ReportError(f"rapport introuvable : {path}")
        return path
    matches = sorted(directory.glob(f"*_{check_tag(tag_or_path)}.json"))
    if not matches:
        raise ReportError(f"aucun rapport pour le tag « {tag_or_path} » dans {directory}")
    return matches[-1]  # names start with the date: the last one is the latest


def load_report(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"rapport illisible : {path} ({exc})") from exc


# --- Markdown --------------------------------------------------------------------------


def _value(metric: dict[str, Any]) -> str:
    value = metric.get("value")
    return "—" if value is None else f"{value:.2f}"


def _flag(value: bool | None) -> str:
    return "—" if value is None else ("✅" if value else "❌")


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Rapport d'évaluation — {report['tag']}",
        "",
        f"- Jeu : `{report.get('suite', '?')}` ({report['questions']} questions)",
        f"- Date de référence : {report.get('today', '?')}, graine {report.get('seed', '?')}",
        f"- Modèle : {report.get('model', '?')}, juge : {report.get('judge_model', '?')}",
        "",
        "## Métriques",
        "",
        "| Métrique | Valeur | Questions |",
        "|---|---:|---:|",
    ]
    for key, label in METRIC_LABELS.items():
        metric = report["metrics"].get(key, {})
        lines.append(f"| {label} | {_value(metric)} | {metric.get('n', 0)} |")
    lines += ["", "## Latence", "", "| Mesure | ms |", "|---|---:|"]
    for key, label in LATENCY_LABELS.items():
        value = report["latency"].get(key)
        lines.append(f"| {label} | {'—' if value is None else value} |")
    lines += [
        "",
        "## Questions",
        "",
        "| Id | Catégorie | EX | SQL valide | Confirmation | Badge | Erreur |",
        "|---|---|:---:|:---:|:---:|---|---|",
    ]
    for result in report["results"]:
        lines.append(
            f"| {result['id']} | {result['category']} | {_flag(result['ex'])} "
            f"| {_flag(result['sql_valid'])} | {_flag(result['confirmation_requested'])} "
            f"| {result['badge'] or '—'} | {result['error'] or '—'} |"
        )
    return "\n".join(lines) + "\n"


# --- comparison ------------------------------------------------------------------------


def compare(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Markdown table of the metric deltas, then the questions whose EX changed."""
    lines = [
        f"# Comparaison {before['tag']} → {after['tag']}",
        "",
        f"| Métrique | {before['tag']} | {after['tag']} | Écart |",
        "|---|---:|---:|---:|",
    ]
    for key, label in METRIC_LABELS.items():
        old = before["metrics"].get(key, {}).get("value")
        new = after["metrics"].get(key, {}).get("value")
        delta = "—" if old is None or new is None else f"{new - old:+.2f}"
        lines.append(f"| {label} | {_value({'value': old})} | {_value({'value': new})} | {delta} |")
    old_ex = {result["id"]: result["ex"] for result in before["results"]}
    changed = [
        (result["id"], old_ex[result["id"]], result["ex"])
        for result in after["results"]
        if result["id"] in old_ex and old_ex[result["id"]] != result["ex"]
    ]
    lines += ["", "## Questions dont l'EX a changé", ""]
    if not changed:
        lines.append("Aucune.")
    for question_id, old, new in changed:
        lines.append(f"- {question_id} : {_flag(old)} → {_flag(new)}")
    return "\n".join(lines) + "\n"


__all__ = [
    "REPORTS_DIR",
    "ReportError",
    "compare",
    "find_report",
    "load_report",
    "report_paths",
    "to_markdown",
    "write_report",
]
