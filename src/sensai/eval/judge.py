"""LLM judge of an answer against the SQL rows (EVAL-03, EV1).

The model only classifies claims (judge_report.v1.json); the badge and the scores are
computed here, never asked to the model (docs/contracts/judge.md §2). The deterministic
numeric check (EVAL-04) runs first and does not depend on the model: a number absent
from the rows gives 'contradicted' even when the judge is unavailable.

The judge never raises: an unreachable model, a timeout or a report still invalid after
one repair gives report=None. Emitting judge_started / judge_report is EVAL-05.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from sensai.core.errors import SensaiError
from sensai.core.events import Badge
from sensai.eval.numeric_check import DEFAULT_TOLERANCE, NumericCheck, check_numbers
from sensai.llm.ollama_client import Chunk, Message

logger = logging.getLogger(__name__)

# The package is installed in editable mode: the contract schema is read from the repo
REPORT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "contracts" / "schemas" / "judge_report.v1.json"
)
PROMPT_FILE = "prompts/judge.md"

MAX_ROWS = 50  # rows shown to the judge, to stay well within num_ctx
MAX_REPAIRS = 1  # judge.md §1: one repair, then 'unavailable'


def load_report_schema() -> dict[str, Any]:
    return json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_prompt() -> str:
    return resources.files("sensai.core").joinpath(PROMPT_FILE).read_text(encoding="utf-8")


class ChatClient(Protocol):
    """What the judge needs from OllamaClient."""

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk: ...


# --- structured call: stand-in for llm/structured.py (CORE-07) ---------------------------


@dataclass(frozen=True)
class StructuredResult:
    """Same fields as StructuredResult of PROJECT §6.2."""

    ok: bool
    data: dict[str, Any] | None
    raw: str
    errors: tuple[str, ...]
    attempts: int


def _schema_errors(raw: str, validator: Draft202012Validator) -> tuple[Any, list[str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"JSON invalide : {exc}"]
    errors = [
        f"{'/'.join(str(part) for part in error.absolute_path) or '(racine)'} : {error.message}"
        for error in validator.iter_errors(data)
    ]
    return data, errors


async def generate_structured(
    client: ChatClient,
    messages: list[Message],
    schema: dict[str, Any],
    model: str,
    max_repairs: int = MAX_REPAIRS,
    options: dict[str, Any] | None = None,
) -> StructuredResult:
    """Call with format=schema, validate, repair once with the exact errors.

    Minimal stand-in until CORE-07 delivers llm/structured.py, with the same signature.
    """
    validator = Draft202012Validator(schema)
    history = list(messages)
    raw, errors = "", ["aucun appel"]
    for attempt in range(1, max_repairs + 2):
        chunk = await client.chat(model, history, format=schema, options=options)
        raw = chunk.content
        data, errors = _schema_errors(raw, validator)
        if not errors:
            return StructuredResult(True, data, raw, (), attempt)
        history = [
            *history,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": "Ta réponse ne respecte pas le schéma JSON demandé :\n- "
                + "\n- ".join(errors)
                + "\nRenvoie uniquement l'objet JSON corrigé.",
            },
        ]
    return StructuredResult(False, None, raw, tuple(errors), max_repairs + 1)


# --- scores and badge, computed by the code (judge.md §2) --------------------------------


def faithfulness(report: dict[str, Any] | None) -> float | None:
    """supported / number of claims; None without a report or without any claim."""
    if report is None or not report["claims"]:
        return None
    claims = report["claims"]
    return sum(claim["verdict"] == "supported" for claim in claims) / len(claims)


def compute_badge(report: dict[str, Any] | None, numeric: NumericCheck) -> Badge:
    """First rule that applies (judge.md §2): rule 1 holds even without a report."""
    verdicts = [claim["verdict"] for claim in report["claims"]] if report else []
    if "contradicted" in verdicts or numeric.status == "mismatch":
        return "contradicted"
    if report is None:
        return "unavailable"
    if verdicts and all(v == "supported" for v in verdicts) and numeric.status == "ok":
        return "verified"
    return "needs_review"


# --- judge -------------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeInput:
    """One numeric answer to check: the last successful run_select of the turn."""

    question: str
    sql: str
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    answer: str
    truncated: bool = False  # tool_result.data.truncated (tools.md §7.2)


@dataclass(frozen=True)
class Verdict:
    """Envelope of the judge_report event (events.md §3.11), without the common fields."""

    badge: Badge
    numeric_check: NumericCheck
    report: dict[str, Any] | None
    duration_ms: int

    @property
    def scores(self) -> dict[str, float | None]:
        return {"faithfulness": faithfulness(self.report)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "badge": self.badge,
            "scores": self.scores,
            "numeric_check": self.numeric_check.to_dict(),
            "report": self.report,
            "duration_ms": self.duration_ms,
        }


def build_messages(case: JudgeInput, system_prompt: str, max_rows: int = MAX_ROWS) -> list[Message]:
    """System prompt, then the question, the SQL, the rows (capped) and the answer."""
    shown = [list(row) for row in case.rows[:max_rows]]
    if case.truncated or len(case.rows) > max_rows:
        note = f"résultat tronqué : seules les {len(shown)} premières lignes sont fournies"
    else:
        note = f"résultat complet : {len(shown)} lignes"
    result = json.dumps(
        {"columns": list(case.columns), "rows": shown}, ensure_ascii=False, default=str
    )
    content = (
        f"## Question\n{case.question}\n\n"
        f"## SQL exécuté\n```sql\n{case.sql}\n```\n\n"
        f"## Résultat ({note})\n```json\n{result}\n```\n\n"
        f"## Réponse à vérifier\n{case.answer}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]


class Judge:
    """Checks an answer: numeric check, then one structured call to the judge model."""

    def __init__(
        self,
        client: ChatClient,
        model: str,
        *,
        tolerance: float = DEFAULT_TOLERANCE,
        num_ctx: int | None = None,
        max_rows: int = MAX_ROWS,
    ) -> None:
        self.client = client
        self.model = model
        self.tolerance = tolerance
        self.max_rows = max_rows
        self.options: dict[str, Any] = {"temperature": 0}
        if num_ctx is not None:
            self.options["num_ctx"] = num_ctx
        self._schema = load_report_schema()
        self._prompt = load_prompt()

    async def evaluate(self, case: JudgeInput) -> Verdict:
        started = time.monotonic()
        numeric = check_numbers(
            case.answer,
            case.rows,
            question=case.question,
            sql=case.sql,
            truncated=case.truncated,
            tolerance=self.tolerance,
        )
        report = await self._report(case)
        duration_ms = round((time.monotonic() - started) * 1000)
        return Verdict(compute_badge(report, numeric), numeric, report, duration_ms)

    async def _report(self, case: JudgeInput) -> dict[str, Any] | None:
        messages = build_messages(case, self._prompt, self.max_rows)
        try:
            result = await generate_structured(
                self.client, messages, self._schema, self.model, options=self.options
            )
        except SensaiError as exc:
            # Never log the answer or the rows: they may hold confidential business data
            logger.warning("judge unavailable: %s", type(exc).__name__)
            return None
        if not result.ok:
            logger.warning("judge report invalid after %d attempts", result.attempts)
            return None
        return result.data
