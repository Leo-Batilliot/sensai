"""Golden set (EVAL-02, eval/README.md): reading eval/golden.jsonl and its date parameters.

Relative dates ("ce trimestre", "le mois dernier") are named parameters of gold_sql,
computed from the reference date of the evaluation (--today), never from the clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

GOLDEN_PATH = Path("eval/golden.jsonl")
REFERENCE_DATE = date(2026, 10, 20)  # PROJECT §6.6: --today 2026-10-20 by default


@dataclass(frozen=True)
class GoldenEntry:
    """One question of the golden set (field meanings in eval/README.md)."""

    id: str
    category: str
    question: str
    gold_sql: str | None
    expects_write: bool
    requires_memory: bool
    notes: str


def load_golden(path: str | Path = GOLDEN_PATH) -> list[GoldenEntry]:
    """Every entry of the file, in order; blank lines are skipped."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [GoldenEntry(**json.loads(line)) for line in lines if line.strip()]


def shift_months(day: date, months: int) -> date:
    """First day of the month `months` away from the month of `day`."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def reference_params(today: date) -> dict[str, str]:
    """Named parameters of gold_sql, derived from the reference date (eval/README.md)."""
    quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    return {
        "today": today.isoformat(),
        "m_start": today.replace(day=1).isoformat(),
        "prev_m_start": shift_months(today, -1).isoformat(),
        "q_start": quarter_start.isoformat(),
        "prev_q_start": shift_months(quarter_start, -3).isoformat(),
        "y_start": date(today.year, 1, 1).isoformat(),
    }


__all__ = [
    "GOLDEN_PATH",
    "REFERENCE_DATE",
    "GoldenEntry",
    "load_golden",
    "reference_params",
    "shift_months",
]
