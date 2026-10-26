"""Web QuestionBroker: a pending question is a Future resolved by an `answer` message.

It implements `sensai.core.ask.QuestionBroker`, like the CLI broker, and works as the
WebConfirmationBroker (confirm.py):
- the engine calls `ask(question)` inside its turn generator, and bounds the wait to
  `question.expires_in_s` itself (`Engine._ask`): no answer in time is its "timeout";
- the WebSocket receive loop, a concurrent task, calls `resolve()` when an `answer`
  message arrives (v1.1-extensions.md §4.1).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sensai.core.ask import MAX_TEXT_CHARS, Answer, PendingQuestion


@dataclass(frozen=True)
class Refusal:
    """Why an `answer` message was not applied: the code and message of the error sent."""

    code: str  # UNKNOWN_QUESTION, or INVALID_MESSAGE (the question stays pending)
    message: str


class WebQuestionBroker:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[PendingQuestion, asyncio.Future[Answer]]] = {}

    async def ask(self, question: PendingQuestion) -> Answer:
        """Wait for the user's answer, as long as the engine lets this call run."""
        future: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._pending[question.question_id] = (question, future)
        try:
            return await future
        finally:  # answered, cancelled, or cancelled by the engine's timeout
            self._pending.pop(question.question_id, None)

    def resolve(self, question_id: str, option_id: str | None, text: str | None) -> Refusal | None:
        """Apply an `answer` message. None when applied, else why it was refused.

        An answer that does not fit the question (unknown option, empty text, free text
        not allowed) leaves the question pending, so that the user can correct it.
        """
        pending = self._pending.get(question_id)
        if pending is None or pending[1].done():
            return Refusal(
                "UNKNOWN_QUESTION",
                "Cette question n'est plus active (déjà répondue, expirée ou annulée).",
            )
        question, future = pending
        if option_id is not None:
            if question.option(option_id) is None:
                return Refusal(
                    "INVALID_MESSAGE", f"Choix inconnu pour cette question : {option_id}."
                )
            future.set_result(Answer("answered", option_id=option_id))
            return None
        answer = (text or "").strip()
        if not question.allow_free_text:
            return Refusal("INVALID_MESSAGE", "Cette question attend un des choix proposés.")
        if not answer or len(answer) > MAX_TEXT_CHARS:
            return Refusal(
                "INVALID_MESSAGE",
                f"La réponse doit contenir de 1 à {MAX_TEXT_CHARS} caractères.",
            )
        future.set_result(Answer("answered", text=answer))
        return None

    def cancel_all(self) -> None:
        """`cancel`, `reset` or disconnection: every pending question ends as cancelled."""
        for _, future in self._pending.values():
            if not future.done():
                future.set_result(Answer("cancelled"))

    @property
    def pending(self) -> frozenset[str]:
        return frozenset(self._pending)
