"""Deterministic numeric check of an answer against the SQL rows (EVAL-04, EV1).

Every number written in the answer must be found in the data the agent received. No
LLM: it is fast, and it catches what a judge model misses on figures.

- French formats: '12 345,6', '48 712,40 €', '12,3 k€', '45 %', '1,2 M€', and the
  English ones a model sometimes writes ('48,712.40').
- A rounded figure matches: '48,7 k€' is consistent with 48 712.4. The slack is the
  larger of the relative tolerance (eval.numeric_tolerance) and half a unit of the
  last digit written.
- '45 %' matches 45 as well as the ratio 0.45. Signs are ignored ('une baisse de 5 %').
- Not checked: dates ('2026-10-01', '14 août'), years, ordinals ('1er', '3e'), list
  markers ('1.'), numbers glued to letters ('T4', 'qwen3'), and numbers already in the
  question or the SQL ('top 5', 'plus de 18 mois').
- Also accepted: the row count and the sum of each numeric column ('au total').

Result, as in judge.md §2 and the judge_report event: status 'ok', 'mismatch' (a number
absent from the rows), or 'inconclusive' (absent, but the rows were truncated: it may
be in the rows that were cut).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

DEFAULT_TOLERANCE = 0.01  # same default as eval.numeric_tolerance

Status = Literal["ok", "mismatch", "inconclusive"]

_SPACES = "   "  # space, no-break space, narrow no-break space (French thousands)

_NUMBER = re.compile(
    rf"""
    (?<![\w.,/\-−])
    (?P<sign>[-−+]\s?)?
    (?P<core>
        \d{{1,3}}(?:[{_SPACES}]\d{{3}})+(?:,\d+)?    # 12 345,6
      | \d{{1,3}}(?:\.\d{{3}})+,\d+                  # 1.234,5
      | \d{{1,3}}(?:,\d{{3}})+\.\d+                  # 48,712.40
      | \d+(?:[.,]\d+)?                              # 12345,6 · 12345.6 · 45
    )
    (?![\d\-/]|[.,]\d)
    (?P<unit>[{_SPACES}]?
        (?:k€|K€|M€|Mds?€|€|EUR|%|pts?\b|points?\b|milliards?\b|millions?\b|mille\b|Mds?\b|[kKM]\b)
    )?
    (?![^\W\d_])
    """,
    re.VERBOSE,
)
_LIST_MARKER = re.compile(r"[.)]\s")
# A day before a month name: "le 14 août", "le 2 oct. 2026"
_MONTH = re.compile(
    rf"[{_SPACES}]+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre"
    r"|octobre|novembre|d[ée]cembre|janv|f[ée]vr|avr|juil|sept|oct|nov|d[ée]c)\b",
    re.IGNORECASE,
)
_SQL_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")

_MULTIPLIERS = {"k": 1e3, "mille": 1e3, "m": 1e6, "million": 1e6, "md": 1e9, "milliard": 1e9}


@dataclass(frozen=True)
class NumberMention:
    """A number of the answer: its text copied verbatim and the values it may stand for."""

    text: str
    values: tuple[float, ...]
    slack: tuple[float, ...]  # rounding slack of each value, from the digits written


@dataclass(frozen=True)
class NumericCheck:
    """Shape of judge_report.numeric_check (events.md §3.11)."""

    status: Status
    unmatched: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "unmatched": list(self.unmatched)}


def _readings(core: str) -> list[tuple[float, int]]:
    """Possible (value, decimals) of a number as written, French reading first."""
    compact = re.sub(f"[{_SPACES}]", "", core)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", compact):
        integer, decimals = compact.replace(".", "").split(",")
        return [(float(f"{integer}.{decimals}"), len(decimals))]
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", compact):
        integer, decimals = compact.replace(",", "").split(".")
        return [(float(f"{integer}.{decimals}"), len(decimals))]
    if "," in compact or "." in compact:
        integer, decimals = re.split(r"[.,]", compact)
        readings = [(float(f"{integer}.{decimals}"), len(decimals))]
        if len(decimals) == 3 and core == compact:  # '48,712' or '12.345': maybe thousands
            readings.append((float(integer + decimals), 0))
        return readings
    return [(float(compact), 0)]


def _unit_factors(unit: str) -> tuple[float, bool]:
    """(multiplier, is_percent) of the unit written after a number."""
    word = unit.strip(_SPACES).rstrip("€").casefold().rstrip("s")
    if word in ("%", "pt", "point"):
        return 1.0, True
    return _MULTIPLIERS.get(word, 1.0), False


def _is_skipped(text: str, match: re.Match[str]) -> bool:
    """Years, days of a date and list markers are not claims about the data."""
    core, unit = match.group("core"), match.group("unit")
    if not unit and core.isdigit() and len(core) == 4 and 1900 <= int(core) <= 2100:
        return True
    if not unit and core.isdigit() and 1 <= int(core) <= 31 and _MONTH.match(text, match.end()):
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    return not text[line_start : match.start()].strip() and bool(
        _LIST_MARKER.match(text, match.end("core"))
    )


def extract_numbers(text: str) -> list[NumberMention]:
    """Numbers of a French answer that must be found in the data, in order."""
    return [mention for _, mention in locate_numbers(text)]


def locate_numbers(text: str) -> list[tuple[re.Match[str], NumberMention]]:
    """Like extract_numbers, with where each number is in the text: the `core` group of
    the match is the number without its sign and unit (the demo mode of CORE-20 rewrites
    it)."""
    mentions = []
    for match in _NUMBER.finditer(text):
        if _is_skipped(text, match):
            continue
        multiplier, percent = _unit_factors(match.group("unit") or "")
        values: list[float] = []
        slack: list[float] = []
        for value, decimals in _readings(match.group("core")):
            half_unit = 0.5 * 10**-decimals * multiplier
            values.append(value * multiplier)
            slack.append(half_unit)
            if percent:  # stored as a ratio: 45 % = 0.45
                values.append(value / 100)
                slack.append(half_unit / 100)
        mention = NumberMention(match.group(0).strip(_SPACES), tuple(values), tuple(slack))
        mentions.append((match, mention))
    return mentions


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def data_values(rows: Sequence[Sequence[Any]]) -> list[float]:
    """Numbers an answer may quote: every cell, the row count, each numeric column sum."""
    values: list[float] = [float(len(rows))]
    columns: dict[int, list[float | None]] = {}
    for row in rows:
        for index, cell in enumerate(row):
            number = _as_number(cell)
            columns.setdefault(index, []).append(number)
            if number is not None:
                values.append(number)
    for cells in columns.values():
        if len(cells) > 1 and all(cell is not None for cell in cells):
            values.append(sum(cell for cell in cells if cell is not None))
    return values


def _context_values(question: str, sql: str) -> list[float]:
    values = [value for mention in extract_numbers(question) for value in mention.values]
    return values + [float(number) for number in _SQL_NUMBER.findall(sql)]


def matches(mention: NumberMention, known: Iterable[float], tolerance: float) -> bool:
    """The mention quotes one of the known values, rounding and tolerance included."""
    for value, slack in zip(mention.values, mention.slack, strict=True):
        for candidate in known:
            gap = abs(abs(value) - abs(candidate))
            if gap <= max(tolerance * max(abs(value), abs(candidate)), slack):
                return True
    return False


def check_numbers(
    answer: str,
    rows: Sequence[Sequence[Any]],
    *,
    question: str = "",
    sql: str = "",
    truncated: bool = False,
    tolerance: float = DEFAULT_TOLERANCE,
) -> NumericCheck:
    """Compare every number of the answer with the rows (judge.md §2)."""
    known = data_values(rows) + _context_values(question, sql)
    unmatched: list[str] = []
    for mention in extract_numbers(answer):
        if not matches(mention, known, tolerance) and mention.text not in unmatched:
            unmatched.append(mention.text)
    if not unmatched:
        return NumericCheck("ok")
    return NumericCheck("inconclusive" if truncated else "mismatch", tuple(unmatched))
