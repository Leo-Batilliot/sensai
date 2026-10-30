"""Online judge (EVAL-05): which answer to check, and a judge call that never fails.

The Engine records the last successful run_select of a turn (select_result), and after
message_done emits judge_started then judge_report with OnlineJudge.verdict(). The judge
runs after the answer, without blocking it (events.md §4.1, point 6).

When the judge does not run (judge.md §2): no successful run_select in the turn, an
interrupted turn, a turn ended by an error, a turn from the interface, or
eval.judge_enabled = false. Those checks are the Engine's; this module only says whether
a tool result is a numeric answer to check.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sensai.core.config import Config
from sensai.eval.judge import ChatClient, Judge, JudgeInput, Verdict, compute_badge
from sensai.eval.numeric_check import NumericCheck, check_numbers
from sensai.tools.base import ToolInfo, ToolResult

logger = logging.getLogger(__name__)

SELECT_TOOL = "run_select"  # original name on sql_toolbox, whatever its prefix (tools.md §1)


@dataclass(frozen=True)
class SelectResult:
    """The SQL and the full rows of a successful run_select (tools.md §7.2)."""

    sql: str
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]
    truncated: bool


def select_result(
    info: ToolInfo | None, arguments: dict[str, Any], result: ToolResult
) -> SelectResult | None:
    """The rows to check the answer against, or None if this call is not a successful
    run_select. Uses ToolResult.data, never the copy cut down for the model."""
    if info is None or info.original_name != SELECT_TOOL or not result.ok:
        return None
    data = result.data or {}
    sql, columns, rows = arguments.get("sql"), data.get("columns"), data.get("rows")
    if not isinstance(sql, str) or not isinstance(columns, list) or not isinstance(rows, list):
        return None
    return SelectResult(sql, columns, rows, bool(data.get("truncated")))


class OnlineJudge:
    """Wraps the Judge for the Engine: a verdict for every judge_started, even on failure."""

    def __init__(self, judge: Judge) -> None:
        self.judge = judge

    @classmethod
    def from_config(cls, config: Config, client: ChatClient) -> OnlineJudge:
        judge = Judge(
            client,
            config.ollama.effective_judge_model,
            tolerance=config.eval.numeric_tolerance,
            num_ctx=config.ollama.num_ctx,
            num_predict=config.ollama.num_predict,
        )
        return cls(judge)

    async def verdict(self, question: str, select: SelectResult, answer: str) -> Verdict:
        """Check the answer. Never raises: judge_started is always followed by judge_report."""
        case = JudgeInput(
            question=question,
            sql=select.sql,
            columns=select.columns,
            rows=select.rows,
            answer=answer,
            truncated=select.truncated,
        )
        started = time.monotonic()
        try:
            return await self.judge.evaluate(case)
        except Exception:
            # Never log the answer or the rows: they may hold confidential business data
            logger.exception("judge failed")
            numeric = self._numeric_only(case)
            duration_ms = round((time.monotonic() - started) * 1000)
            return Verdict(compute_badge(None, numeric), numeric, None, duration_ms)

    def _numeric_only(self, case: JudgeInput) -> NumericCheck:
        """The deterministic check alone, which does not need the model (judge.md §2)."""
        try:
            return check_numbers(
                case.answer,
                case.rows,
                question=case.question,
                sql=case.sql,
                truncated=case.truncated,
                tolerance=self.judge.tolerance,
            )
        except Exception:
            logger.exception("numeric check failed")
            return NumericCheck("inconclusive")


__all__ = ["SELECT_TOOL", "OnlineJudge", "SelectResult", "select_result"]
