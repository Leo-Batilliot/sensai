import asyncio

import pytest

from sensai.core.ask import Answer, PendingQuestion
from sensai.web.websocket.ask import WebQuestionBroker

QUESTION = PendingQuestion(
    question_id="q-1",
    call_id="k-1",
    question="Ce trimestre ou tout l'historique ?",
    reason="",
    options=[{"id": "trimestre", "label": "Ce trimestre"}],
    allow_free_text=True,
    expires_in_s=120,
)


async def waiting(broker: WebQuestionBroker, question: PendingQuestion = QUESTION) -> asyncio.Task:
    task = asyncio.ensure_future(broker.ask(question))
    await asyncio.sleep(0)
    assert question.question_id in broker.pending
    return task


async def test_an_option_answers() -> None:
    broker = WebQuestionBroker()
    task = await waiting(broker)
    assert broker.resolve("q-1", "trimestre", None) is None
    assert await task == Answer("answered", option_id="trimestre")
    assert broker.pending == frozenset()


async def test_a_free_text_answers_stripped() -> None:
    broker = WebQuestionBroker()
    task = await waiting(broker)
    assert broker.resolve("q-1", None, "  depuis janvier ") is None
    assert await task == Answer("answered", text="depuis janvier")


@pytest.mark.parametrize(
    ("option_id", "text", "allow_free_text"),
    [("semestre", None, True), (None, "   ", True), (None, "x" * 501, True), (None, "x", False)],
    ids=["unknown-option", "blank", "too-long", "free-text-not-allowed"],
)
async def test_an_answer_that_does_not_fit_keeps_the_question_pending(
    option_id: str | None, text: str | None, allow_free_text: bool
) -> None:
    broker = WebQuestionBroker()
    question = PendingQuestion(**{**QUESTION.__dict__, "allow_free_text": allow_free_text})
    task = await waiting(broker, question)
    refusal = broker.resolve("q-1", option_id, text)
    assert refusal is not None and refusal.code == "INVALID_MESSAGE"
    assert not task.done()
    assert broker.resolve("q-1", "trimestre", None) is None  # the user corrects it
    assert (await task).status == "answered"


async def test_unknown_or_answered_questions() -> None:
    broker = WebQuestionBroker()
    refusal = broker.resolve("q-404", "trimestre", None)
    assert refusal is not None and refusal.code == "UNKNOWN_QUESTION"
    task = await waiting(broker)
    broker.resolve("q-1", "trimestre", None)
    await task
    refusal = broker.resolve("q-1", "trimestre", None)
    assert refusal is not None and refusal.code == "UNKNOWN_QUESTION"


async def test_cancel_all() -> None:
    broker = WebQuestionBroker()
    task = await waiting(broker)
    broker.cancel_all()
    assert await task == Answer("cancelled")


async def test_the_engine_timeout_forgets_the_question() -> None:
    broker = WebQuestionBroker()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.ask(QUESTION), 0.01)
    assert broker.pending == frozenset()
    refusal = broker.resolve("q-1", "trimestre", None)
    assert refusal is not None and refusal.code == "UNKNOWN_QUESTION"
