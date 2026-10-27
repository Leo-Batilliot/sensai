"""Conversation history sent to the model on every turn.

An exchange is recorded only once its answer is complete: an interrupted answer (Ctrl+C,
stream cut) never enters the history, so the model never sees a half-written reply
(PROJECT.md §6.1).

Between exchanges, a system note may mark a change of the session, at the place where it
happened: "Profil actif : ADV (…)" when the agent profile changes (A5, point V-5).

A long session does not keep every exchange (M2, core/context.py): the oldest ones leave
the history, and a rolling summary of them comes first, right after the system prompt.
An exchange keeps the key figures that the judge checked in its answer, so that the code
can make sure the summary does not lose them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sensai.llm.ollama_client import Message


@dataclass
class Exchange:
    """A question and its complete answer."""

    question: str
    answer: str
    turn_id: str | None = None
    # key_figures of the judge_report of the answer, known once the judge is done (EVAL-05)
    key_figures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        return [
            {"role": "user", "content": self.question},
            {"role": "assistant", "content": self.answer},
        ]


@dataclass(frozen=True)
class Summary:
    """What stands for the exchanges taken out of the history (M2)."""

    text: str  # what the model reads, right after the system prompt
    data: dict[str, Any]  # the structured summary, given back at the next compaction
    # Figures checked by the judge in the summarized exchanges: always kept by the code
    verified: tuple[dict[str, Any], ...] = ()


class History:
    """Completed exchanges of one session, oldest first, after the summary of older ones."""

    def __init__(self) -> None:
        self._items: list[Exchange | str] = []  # a str is a system note
        self.summary: Summary | None = None

    def __len__(self) -> int:
        return len(self.messages)

    @property
    def messages(self) -> list[Message]:
        return _messages(self._items, self.summary)

    @property
    def exchanges(self) -> list[Exchange]:
        return [item for item in self._items if isinstance(item, Exchange)]

    def messages_for(self, question: str) -> list[Message]:
        """Messages to send for a new question: the whole history, then the question."""
        return [*self.messages, {"role": "user", "content": question}]

    def commit(self, question: str, answer: str, turn_id: str | None = None) -> None:
        """Record a completed exchange. Only the answer text is kept, never the thinking."""
        self._items.append(Exchange(question, answer, turn_id))

    def note(self, text: str) -> None:
        """Record a system note after the last exchange."""
        self._items.append(text)

    def add_key_figures(self, turn_id: str, figures: Sequence[dict[str, Any]]) -> None:
        """The key figures of the judge, which come after the answer; ignored if the
        exchange has already left the history."""
        for exchange in self.exchanges:
            if exchange.turn_id == turn_id:
                exchange.key_figures = list(figures)
                return

    def oldest(self, keep: int) -> list[Exchange]:
        """Every exchange but the `keep` most recent ones."""
        exchanges = self.exchanges
        return exchanges[: max(len(exchanges) - keep, 0)]

    def messages_without(self, count: int, summary: Summary | None = None) -> list[Message]:
        """The messages that drop(count, summary) would leave, without dropping anything."""
        return _messages(self._items[self._cut(count) :], summary or self.summary)

    def drop(self, count: int, summary: Summary | None = None) -> None:
        """Take the `count` oldest exchanges out, with the notes before them. A new summary
        replaces the previous one; without one, the previous summary stays."""
        self._items = self._items[self._cut(count) :]
        if summary is not None:
            self.summary = summary

    def clear(self) -> None:
        self._items.clear()
        self.summary = None

    def _cut(self, count: int) -> int:
        """Index of the first item kept when the `count` oldest exchanges leave."""
        seen = 0
        for index, item in enumerate(self._items):
            if seen == count:
                return index
            if isinstance(item, Exchange):
                seen += 1
        return len(self._items)


def _messages(items: Sequence[Exchange | str], summary: Summary | None) -> list[Message]:
    messages: list[Message] = [{"role": "system", "content": summary.text}] if summary else []
    for item in items:
        if isinstance(item, Exchange):
            messages += item.messages
        else:
            messages.append({"role": "system", "content": item})
    return messages
