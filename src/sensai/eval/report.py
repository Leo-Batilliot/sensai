"""Harness reports (EVAL-06): eval/reports/<date>_<tag>.{json,md}, and their comparison.

The JSON report is the reference (build_report in harness.py); the Markdown one is its
readable view, for docs/EVALUATION.md and the slides. `compare` puts two reports side
by side: what differs in the run (model, prompt...), metric deltas, then the questions
whose EX changed.
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
    "judge_json_first_try": "JSON du juge valide au 1er essai",
    "judge_json_valid": "JSON du juge valide après réparation",
    "tool_args_first_try": "Arguments d'outils valides au 1er essai",
    "tool_args_valid": "Arguments d'outils valides après réparation",
}
LATENCY_LABELS = {
    "turn_p50_ms": "Durée du tour p50",
    "turn_p95_ms": "Durée du tour p95",
    "first_token_p50_ms": "Premier token p50",
    "first_token_p95_ms": "Premier token p95",
    "judge_p50_ms": "Juge p50",
    "judge_p95_ms": "Juge p95",
}
TOKEN_LABELS = {"prompt_mean": "Prompt", "completion_mean": "Réponse"}
# Run information compared by `compare`: a delta means little if one of them changed
RUN_LABELS = {
    "model": "Modèle",
    "judge_model": "Modèle du juge",
    "prompt": "Prompt",
    "persona": "Profil",
    "fewshot": "Exemples dynamiques",
    "memory": "Mémoire",
    "suite": "Jeu",
    "today": "Date de référence",
    "seed": "Graine",
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


def run_value(report: dict[str, Any], key: str) -> str:
    """Run information as one short text: "system_sql v1 (3f2a9c1b)", "désactivés"..."""
    value = report.get(key)
    if key == "prompt" and isinstance(value, dict):
        version = value.get("version") or value.get("file") or "?"
        return f"{value.get('name', '?')} {version} ({str(value.get('sha256', ''))[:8]})"
    if key == "fewshot" and isinstance(value, dict):
        return "activés" if value.get("enabled") else "désactivés"
    if key == "persona" and value is None and "persona" in report:
        return "aucun"
    if key == "memory" and value is None and "memory" in report:
        return "vide (ablation)"
    return "?" if value is None else str(value)


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Rapport d'évaluation — {report['tag']}",
        "",
        f"- Jeu : `{report.get('suite', '?')}` ({report['questions']} questions)",
        f"- Date de référence : {report.get('today', '?')}, graine {report.get('seed', '?')}",
        f"- Modèle : {report.get('model', '?')}, juge : {report.get('judge_model', '?')}",
        f"- Prompt : {run_value(report, 'prompt')} ; profil : {run_value(report, 'persona')} ; "
        f"exemples dynamiques : {run_value(report, 'fewshot')} ; "
        f"mémoire : {run_value(report, 'memory')}",
        "",
        "## Métriques",
        "",
        "| Métrique | Valeur | Questions |",
        "|---|---:|---:|",
    ]
    for key, label in METRIC_LABELS.items():
        metric = report["metrics"].get(key, {})
        lines.append(f"| {label} | {_value(metric)} | {metric.get('n', 0)} |")
    by_category = report.get("ex_by_category") or {}
    if by_category:
        lines += [
            "",
            "## EX par catégorie",
            "",
            "| Catégorie | EX | Questions |",
            "|---|---:|---:|",
        ]
        for category, metric in by_category.items():
            lines.append(f"| {category} | {_value(metric)} | {metric.get('n', 0)} |")
    lines += ["", "## Latence", "", "| Mesure | ms |", "|---|---:|"]
    for key, label in LATENCY_LABELS.items():
        value = report["latency"].get(key)
        lines.append(f"| {label} | {'—' if value is None else value} |")
    tokens = report.get("tokens") or {}
    lines += ["", "## Tokens (dernier appel du tour, moyenne)", "", "| Mesure | Tokens |"]
    lines.append("|---|---:|")
    for key, label in TOKEN_LABELS.items():
        lines.append(f"| {label} | {_number(tokens.get(key))} |")
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
    """Markdown tables of what differs in the run and of the metric deltas, then the
    questions whose EX changed."""
    lines = [f"# Comparaison {before['tag']} → {after['tag']}", ""]
    changed_run = [
        (label, run_value(before, key), run_value(after, key))
        for key, label in RUN_LABELS.items()
        if run_value(before, key) != run_value(after, key)
    ]
    if changed_run:
        lines += [f"| Exécution | {before['tag']} | {after['tag']} |", "|---|---|---|"]
        lines += [f"| {label} | {old} | {new} |" for label, old, new in changed_run]
        lines.append("")
    lines += [
        f"| Métrique | {before['tag']} | {after['tag']} | Écart |",
        "|---|---:|---:|---:|",
    ]
    for key, label in METRIC_LABELS.items():
        old = before["metrics"].get(key, {}).get("value")
        new = after["metrics"].get(key, {}).get("value")
        delta = "—" if old is None or new is None else f"{new - old:+.2f}"
        lines.append(f"| {label} | {_value({'value': old})} | {_value({'value': new})} | {delta} |")
    lines += _compare_rows(
        before, after, "EX par catégorie", _category_rows(before, after), as_rate=True
    )
    token_rows = [
        (
            f"Tokens : {label.lower()}",
            (before.get("tokens") or {}).get(key),
            (after.get("tokens") or {}).get(key),
        )
        for key, label in TOKEN_LABELS.items()
    ]
    latency_rows = [
        (label, before["latency"].get(key), after["latency"].get(key))
        for key, label in LATENCY_LABELS.items()
    ]
    lines += _compare_rows(before, after, "Coût", token_rows + latency_rows, as_rate=False)
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


def _number(value: Any) -> str:
    """A count or a duration, with the placeholder of the other tables when missing."""
    return _value({"value": None}) if value is None else str(value)


def _category_rows(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    old, new = before.get("ex_by_category") or {}, after.get("ex_by_category") or {}
    return [
        (category, old.get(category, {}).get("value"), new.get(category, {}).get("value"))
        for category in sorted(set(old) | set(new))
    ]


def _compare_rows(
    before: dict[str, Any],
    after: dict[str, Any],
    title: str,
    rows: list[tuple[str, Any, Any]],
    *,
    as_rate: bool,
) -> list[str]:
    """A table of deltas; nothing when no row has a value (an older report)."""
    if not any(old is not None or new is not None for _, old, new in rows):
        return []
    lines = ["", f"| {title} | {before['tag']} | {after['tag']} | Écart |", "|---|---:|---:|---:|"]
    for label, old, new in rows:
        if old is None or new is None:
            delta = _number(None)
        else:
            delta = f"{new - old:+.2f}" if as_rate else f"{new - old:+d}"
        if as_rate:
            shown = [_value({"value": value}) for value in (old, new)]
        else:
            shown = [_number(value) for value in (old, new)]
        lines.append(f"| {label} | {shown[0]} | {shown[1]} | {delta} |")
    return lines


__all__ = [
    "REPORTS_DIR",
    "ReportError",
    "compare",
    "find_report",
    "load_report",
    "report_paths",
    "run_value",
    "to_markdown",
    "write_report",
]
