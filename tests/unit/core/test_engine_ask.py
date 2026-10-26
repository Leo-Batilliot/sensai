"""Clarifying questions in the tool loop of the Engine (ask_user, core/ask.py)."""

import asyncio
import contextlib
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, answer, tool_request
from sensai.core import events
from sensai.core.ask import NO_ANSWER_HINT, Answer, AskUserTool, PendingQuestion
from sensai.core.config import AgentConfig, Config, HitlConfig
from sensai.core.engine import Engine
from sensai.tools.registry import Registry

PERIOD = {
    "question": "Le délai moyen de paiement doit-il porter sur ce trimestre ou sur tout l'historique ?",
    "reason": "Les deux périodes donnent des délais très différents.",
    "options": [
        {"id": "trimestre", "label": "Ce trimestre"},
        {"id": "historique", "label": "Tout l'historique"},
    ],
}


class Questions:
    """A QuestionBroker that gives a scripted answer (None: never answers)."""

    def __init__(self, reply: Answer | None) -> None:
        self.reply = reply
        self.asked: list[PendingQuestion] = []

    async def ask(self, question: PendingQuestion) -> Answer:
        self.asked.append(question)
        if self.reply is None:
            await asyncio.Event().wait()
        assert self.reply is not None
        return self.reply


def engine(
    *replies: list[Any],
    questions: Questions | None = None,
    agent: AgentConfig | None = None,
    hitl: HitlConfig | None = None,
) -> tuple[Engine, FakeClient]:
    client = FakeClient(*replies)
    built = Engine(
        Config(agent=agent or AgentConfig(), hitl=hitl or HitlConfig()),
        client,
        registry=Registry(native_tools=[AskUserTool()]),
        questions=questions,
        session_id="s-1",
        sql_classifier=lambda sql: None,
    )
    return built, client


async def run(built: Engine, question: str = "Délai moyen de paiement ?") -> list[events.Event]:
    async with contextlib.aclosing(built.run_turn(question)) as turn:
        emitted = [event async for event in turn]
    check_turn(emitted)
    return emitted


def of(emitted: list[events.Event], kind: type) -> list[Any]:
    return [event for event in emitted if isinstance(event, kind)]


def tool_messages(client: FakeClient) -> list[str]:
    _, messages, _ = client.calls[-1]
    return [message["content"] for message in messages if message["role"] == "tool"]


async def test_the_answer_reaches_the_model() -> None:
    questions = Questions(Answer("answered", option_id="trimestre"))
    built, client = engine(
        tool_request("ask_user", **PERIOD),
        answer("Sur ce trimestre : 41 jours."),
        questions=questions,
    )
    emitted = await run(built)

    assert [event.type for event in emitted] == [
        "turn_started",
        "tool_call",
        "question_asked",
        "question_answered",
        "tool_result",
        "token",
        "message_done",
    ]
    (asked,) = of(emitted, events.QuestionAsked)
    assert asked.options == PERIOD["options"] and asked.allow_free_text
    assert asked.expires_in_s == 120 and asked.question_id == questions.asked[0].question_id
    (answered,) = of(emitted, events.QuestionAnswered)
    assert (answered.status, answered.option_id, answered.text) == ("answered", "trimestre", None)
    (result,) = of(emitted, events.ToolResult)
    assert result.ok and result.summary == "Réponse : Ce trimestre"
    (sent,) = tool_messages(client)
    assert '"option": {"id": "trimestre", "label": "Ce trimestre"}' in sent
    assert NO_ANSWER_HINT not in sent


async def test_a_free_answer_reaches_the_model() -> None:
    questions = Questions(Answer("answered", text="  Depuis janvier  "))
    built, client = engine(tool_request("ask_user", **PERIOD), answer("Ok."), questions=questions)
    emitted = await run(built)

    (answered,) = of(emitted, events.QuestionAnswered)
    assert (answered.option_id, answered.text) == (None, "Depuis janvier")
    assert '"text": "Depuis janvier"' in tool_messages(client)[0]


async def test_one_question_per_turn() -> None:
    questions = Questions(Answer("answered", option_id="historique"))
    built, client = engine(
        tool_request("ask_user", **PERIOD),
        tool_request("ask_user", question="Et pour quels clients ?", reason="Périmètre"),
        answer("Voilà."),
        questions=questions,
    )
    emitted = await run(built)

    assert len(questions.asked) == 1 and len(of(emitted, events.QuestionAsked)) == 1
    second = of(emitted, events.ToolResult)[1]
    assert second.error is not None and second.error.code == "QUESTION_LIMIT"
    assert "hypothèse" in tool_messages(client)[1]


async def test_the_limit_starts_again_at_each_turn() -> None:
    questions = Questions(Answer("answered", option_id="trimestre"))
    built, _ = engine(
        tool_request("ask_user", **PERIOD),
        answer("Un."),
        tool_request("ask_user", **PERIOD),
        answer("Deux."),
        questions=questions,
    )
    await run(built)
    await run(built)
    assert len(questions.asked) == 2


async def test_no_question_at_all_when_the_limit_is_zero() -> None:
    questions = Questions(Answer("answered", option_id="trimestre"))
    built, _ = engine(
        tool_request("ask_user", **PERIOD),
        answer("Hypothèse : ce trimestre."),
        questions=questions,
        agent=AgentConfig(max_questions_per_turn=0),
    )
    emitted = await run(built)
    assert questions.asked == [] and of(emitted, events.QuestionAsked) == []


async def test_no_answer_leads_to_an_explicit_assumption() -> None:
    built, client = engine(
        tool_request("ask_user", **PERIOD),
        answer("Hypothèse : tout l'historique."),
        questions=Questions(None),
        agent=AgentConfig(question_timeout_s=0.05),
    )
    emitted = await run(built)

    (answered,) = of(emitted, events.QuestionAnswered)
    assert (answered.status, answered.option_id, answered.text) == ("timeout", None, None)
    (result,) = of(emitted, events.ToolResult)
    assert result.ok and result.summary == "Sans réponse : l'agent fait une hypothèse"
    assert NO_ANSWER_HINT in tool_messages(client)[0]
    assert of(emitted, events.MessageDone)[0].content == "Hypothèse : tout l'historique."


async def test_no_human_broker_never_waits() -> None:
    # No QuestionBroker given: nobody can answer, and the turn goes on at once
    built, client = engine(
        tool_request("ask_user", **PERIOD),
        answer("Hypothèse : ce trimestre."),
        agent=AgentConfig(question_timeout_s=3600),
    )
    emitted = await asyncio.wait_for(run(built), 5)

    (answered,) = of(emitted, events.QuestionAnswered)
    assert answered.status == "cancelled"
    assert NO_ANSWER_HINT in tool_messages(client)[0]
    assert not of(emitted, events.MessageDone)[0].interrupted


@pytest.mark.parametrize(
    "reply",
    [
        Answer("answered", option_id="semestre"),  # not one of the options
        Answer("answered", text="   "),
    ],
    ids=["unknown-option", "blank-text"],
)
async def test_an_answer_that_does_not_fit_counts_as_none(reply: Answer) -> None:
    built, _ = engine(tool_request("ask_user", **PERIOD), answer("Ok."), questions=Questions(reply))
    (answered,) = of(await run(built), events.QuestionAnswered)
    assert (answered.status, answered.option_id, answered.text) == ("cancelled", None, None)


async def test_free_text_refused_when_the_question_forbids_it() -> None:
    questions = Questions(Answer("answered", text="les deux"))
    built, _ = engine(
        tool_request("ask_user", **PERIOD, allow_free_text=False),
        answer("Ok."),
        questions=questions,
    )
    emitted = await run(built)
    assert not of(emitted, events.QuestionAsked)[0].allow_free_text
    assert of(emitted, events.QuestionAnswered)[0].status == "cancelled"


async def test_without_options_a_free_answer_is_always_allowed() -> None:
    questions = Questions(Answer("answered", text="Les clients actifs"))
    built, _ = engine(
        tool_request("ask_user", question="Quel périmètre ?", reason="", allow_free_text=False),
        answer("Ok."),
        questions=questions,
    )
    emitted = await run(built)
    asked = of(emitted, events.QuestionAsked)[0]
    assert asked.options == [] and asked.allow_free_text


async def test_invalid_arguments_ask_nothing() -> None:
    questions = Questions(Answer("answered", option_id="trimestre"))
    built, _ = engine(
        tool_request("ask_user", question="?", reason="x"), answer("Ok."), questions=questions
    )
    emitted = await run(built)
    assert questions.asked == [] and of(emitted, events.QuestionAsked) == []
    assert of(emitted, events.ToolResult)[0].error.code == "INVALID_ARGUMENTS"


async def test_a_denied_ask_user_asks_nothing() -> None:
    questions = Questions(Answer("answered", option_id="trimestre"))
    built, _ = engine(
        tool_request("ask_user", **PERIOD),
        answer("Ok."),
        questions=questions,
        hitl=HitlConfig(deny=("ask_user",)),
    )
    emitted = await run(built)
    assert questions.asked == []
    assert of(emitted, events.ToolResult)[0].error.code == "TOOL_DENIED"


async def test_cancel_during_a_question_ends_the_turn() -> None:
    """What the web layer does on cancel: the question is cancelled, then the turn."""

    class Cancelled:
        async def ask(self, question: PendingQuestion) -> Answer:
            await asyncio.sleep(0.01)
            built.cancel()
            return Answer("cancelled")

    built, client = engine(tool_request("ask_user", **PERIOD), answer("jamais"))
    built.questions = Cancelled()
    emitted = await run(built)

    assert of(emitted, events.QuestionAnswered)[0].status == "cancelled"
    done = of(emitted, events.MessageDone)[0]
    assert done.interrupted and len(client.calls) == 1  # the model is not called again
    assert built.history.messages_for("x")[:-1] == []  # an interrupted turn is not kept
