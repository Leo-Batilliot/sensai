"""Clarifying questions: the model asks the user one question, and waits for the answer.

ask_user is declared to the registry like any native tool, so that the model sees it, but
the registry never runs it: the Engine recognizes the call, emits question_asked, awaits
QuestionBroker.ask() for at most expires_in_s (Engine._ask), then emits
question_answered and sends the answer back to the model. Without an answer in time, or
without a human at all, the model is told to make an explicit assumption.

As for confirmations (core/confirm.py), the broker must be answered by a task other than
the one consuming the event generator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from sensai.core.events import AnswerStatus
from sensai.tools.base import ToolAnnotations, ToolResult

ASK_USER = "ask_user"
NO_ANSWER_HINT = "Pas de réponse : fais une hypothèse explicite et dis-la."
LIMIT_MESSAGE = (
    "Une seule question par tour : n'en pose pas d'autre, fais une hypothèse explicite "
    "et dis-la dans ta réponse."
)
MAX_TEXT_CHARS = 500  # a free answer, as in the `answer` message of the WebSocket

ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "reason"],
    "properties": {
        "question": {"type": "string", "minLength": 5, "maxLength": 300},
        "reason": {"type": "string", "maxLength": 200},
        "options": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["id", "label"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9_-]{1,32}$"},
                    "label": {"type": "string", "minLength": 1, "maxLength": 80},
                },
            },
        },
        "allow_free_text": {"type": "boolean", "default": True},
    },
}


@dataclass(frozen=True)
class PendingQuestion:
    """A question waiting for an answer: the payload of question_asked."""

    question_id: str
    call_id: str
    question: str
    reason: str
    options: list[dict[str, str]]
    allow_free_text: bool
    expires_in_s: float

    def option(self, option_id: str) -> dict[str, str] | None:
        return next((option for option in self.options if option["id"] == option_id), None)


@dataclass(frozen=True)
class Answer:
    """The answer to a PendingQuestion: the payload of question_answered."""

    status: AnswerStatus
    option_id: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        given = self.option_id is not None or self.text is not None
        if given != (self.status == "answered"):
            raise ValueError("only an answered question has an option or a text")


class QuestionBroker(Protocol):
    """Asks a human a clarifying question."""

    async def ask(self, question: PendingQuestion) -> Answer: ...


class NoHumanQuestionBroker:
    """Nobody to answer (harness, scheduled tasks, tests): cancelled at once, no wait."""

    async def ask(self, question: PendingQuestion) -> Answer:
        return Answer("cancelled")


def pending_question(
    question_id: str, call_id: str, arguments: dict[str, Any], expires_in_s: float
) -> PendingQuestion:
    """The question of a valid ask_user call. Without options, only a free answer is possible."""
    options = [
        {"id": str(option["id"]), "label": str(option["label"])}
        for option in arguments.get("options") or []
    ]
    return PendingQuestion(
        question_id=question_id,
        call_id=call_id,
        question=str(arguments["question"]),
        reason=str(arguments.get("reason", "")),
        options=options,
        allow_free_text=not options or bool(arguments.get("allow_free_text", True)),
        expires_in_s=expires_in_s,
    )


def checked(answer: Answer, question: PendingQuestion) -> Answer:
    """The broker's answer if it fits the question; anything else counts as no answer."""
    if answer.status != "answered":
        return Answer(answer.status)
    if answer.option_id is not None:
        if question.option(answer.option_id) is None:
            return Answer("cancelled")
        return Answer("answered", option_id=answer.option_id)
    text = (answer.text or "").strip()[:MAX_TEXT_CHARS]
    if not text or not question.allow_free_text:
        return Answer("cancelled")
    return Answer("answered", text=text)


def answer_result(answer: Answer, question: PendingQuestion) -> ToolResult:
    """What the model reads (v1.1-extensions.md §3.2) and what the timeline shows."""
    option = question.option(answer.option_id) if answer.option_id else None
    payload: dict[str, Any] = {"status": answer.status, "option": option, "text": answer.text}
    text = json.dumps(payload, ensure_ascii=False)
    if answer.status == "answered":
        summary = f"Réponse : {option['label'] if option else answer.text}"
    else:
        text += f"\n{NO_ANSWER_HINT}"
        summary = (
            "Sans réponse : l'agent fait une hypothèse"
            if answer.status == "timeout"
            else "Question annulée : l'agent fait une hypothèse"
        )
    return ToolResult(ok=True, text=text, summary=summary, data=payload)


class AskUserTool:
    """ask_user as the registry sees it. The Engine handles every call itself."""

    name = ASK_USER
    description = (
        "Pose UNE question à l'utilisateur quand la réponse change le résultat (période, "
        "définition, périmètre). Sinon, fais une hypothèse et dis-la."
    )
    input_schema = ASK_USER_SCHEMA
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=False, open_world=False
    )

    async def call(self, arguments: dict[str, Any]) -> ToolResult:
        # Reached only through an engine that does not know ask_user
        return ToolResult.failure("TOOL_FAILED", "Aucun utilisateur ne peut répondre ici.")
