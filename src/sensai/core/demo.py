"""Demo mode of act 3 (scenario S-1, CORE-20): one figure of the answer is made wrong.

A model that is wrong on demand does not exist, so the demo forces the mistake. With
[demo] alter_answer, and only for a question that contains [demo] alter_trigger, the
first number of the answer that quotes a cell of the last run_select is raised by 15 %,
written the same way ('48 712,40 €' -> '56 019,26 €'). The judge is not told: the
numeric check and the judge model have to catch it on their own. The mode is announced
on screen ("Mode démo actif", demo_notice) and aloud.

The answer is altered while it streams, so that the tokens stay the displayed text
(events.md §4.3): a number at the end of the text is held back until it is complete.
The history and the judge get the altered answer, since it is what the user saw.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from sensai.core.config import Config
from sensai.eval.numeric_check import check_numbers, extract_numbers, locate_numbers, matches
from sensai.eval.online import SelectResult

logger = logging.getLogger(__name__)

RAISE = 1.15  # +15 %: far beyond the tolerance of the numeric check (1 %)
NOTICE = (
    "Mode démo actif : pour une question qui contient « {trigger} », un chiffre de la "
    "réponse est faussé avant le juge (acte 3). Annoncez-le au jury."
)

_SPACES = "   "  # space, no-break space, narrow no-break space (as in numeric_check)
# A number that the next tokens may still extend: digits, separators, then a unit
_OPEN_TAIL = re.compile(rf"\d[\d{_SPACES}.,]*[^\s\d]{{0,9}}$")


def demo_notice(config: Config) -> str | None:
    """What the CLI and the web server print at startup, or None outside the demo mode."""
    if not config.demo.alter_answer:
        return None
    return NOTICE.format(trigger=config.demo.alter_trigger)


class AnswerAlteration:
    """Alters the answer of one turn while it streams (see the module docstring).

    The Engine passes every token through feed(), and calls flush() at the end of each
    model reply. Before the first successful run_select, the text goes through as is.
    """

    def __init__(self, selects: Sequence[SelectResult], question: str, tolerance: float) -> None:
        self._selects = selects  # filled by the Engine during the turn
        self._question = question
        self._tolerance = tolerance
        self._text = ""
        self._sent = 0  # characters of _text already released
        self.done = False

    @classmethod
    def for_turn(
        cls, config: Config, question: str, selects: Sequence[SelectResult]
    ) -> AnswerAlteration | None:
        """The alteration of this turn, or None when the demo mode does not apply to it."""
        demo = config.demo
        if not demo.alter_answer or demo.alter_trigger.casefold() not in question.casefold():
            return None
        return cls(selects, question, config.eval.numeric_tolerance)

    def feed(self, delta: str) -> str:
        """The text that can be shown now: all of it but a number that may be incomplete."""
        self._text += delta
        waiting = not self.done and self._selects  # nothing to alter otherwise: no delay
        held = _OPEN_TAIL.search(self._text, self._sent) if waiting else None
        return self._release(held.start() if held else len(self._text))

    def flush(self) -> str:
        """End of a model reply: the rest of the text, last number included."""
        return self._release(len(self._text))

    def _release(self, end: int) -> str:
        if not self.done and self._selects:
            end = self._alter(end)
        released, self._sent = self._text[self._sent : end], end
        return released

    def _alter(self, end: int) -> int:
        """Rewrite the first number before `end` that quotes a cell; returns the new end."""
        select = self._selects[-1]
        cells = _cells(select.rows)
        asked = [value for mention in extract_numbers(self._question) for value in mention.values]
        for match, mention in locate_numbers(self._text[:end]):
            if match.start() < self._sent:
                continue  # already shown
            if not matches(mention, cells, self._tolerance) or matches(
                mention, asked, self._tolerance
            ):
                continue  # not a figure of the data, or one of the question ("top 5")
            start, stop = match.span("core")
            raised = _raised(match.group("core"))
            written = self._text[match.start() : start] + raised + self._text[stop : match.end()]
            check = check_numbers(
                written,
                select.rows,
                question=self._question,
                sql=select.sql,
                tolerance=self._tolerance,
            )
            if check.status == "ok":
                continue  # still consistent with the data once rounded ('1' -> '1')
            logger.warning("mode démo : réponse altérée (%s -> %s)", mention.text, written.strip())
            self._text = self._text[:start] + raised + self._text[stop:]
            self.done = True
            return end + len(raised) - len(match.group("core"))
        return end


def _cells(rows: Sequence[Sequence[Any]]) -> list[float]:
    """Numeric cells of the rows (not the row count nor the column sums)."""
    return [
        float(cell)
        for row in rows
        for cell in row
        if isinstance(cell, (int, float)) and not isinstance(cell, bool)
    ]


def _raised(core: str) -> str:
    """The number `core` raised by RAISE, with the same separators and decimals."""
    thousands, decimal = _separators(core)
    integer, _, decimals = core.rpartition(decimal) if decimal else (core, "", "")
    digits = integer.replace(thousands, "") if thousands else integer
    value = float(f"{digits}.{decimals or 0}") * RAISE
    new_integer, _, new_decimals = f"{value:.{len(decimals)}f}".partition(".")
    if thousands:
        new_integer = f"{int(new_integer):,}".replace(",", thousands)
    return f"{new_integer}{decimal}{new_decimals}" if decimals else new_integer


def _separators(core: str) -> tuple[str, str]:
    """(thousands, decimal) separators of a number as written; '' when there is none."""
    spaces = [char for char in core if char in _SPACES]
    if spaces:
        return spaces[0], "," if "," in core else ""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", core):  # 1.234,5
        return ".", ","
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", core):  # 48,712.40
        return ",", "."
    return "", "," if "," in core else "." if "." in core else ""
