import asyncio
import io
import threading
from collections.abc import Iterator

import pytest
from rich.console import Console

from fakes import FakeClient, answer, tool_request
from sensai.cli.app import ChatSession
from sensai.cli.ask import RETRY_PROMPT, TEXT_PROMPT, CliQuestionBroker
from sensai.cli.render import Renderer
from sensai.core.ask import Answer, AskUserTool, PendingQuestion
from sensai.core.config import Config
from sensai.core.engine import Engine
from sensai.tools.registry import Registry

QUESTION = PendingQuestion(
    question_id="q-1",
    call_id="k-1",
    question="Le délai doit-il porter sur ce trimestre ou sur tout l'historique ?",
    reason="Les deux périodes donnent des délais très différents.",
    options=[
        {"id": "trimestre", "label": "Ce trimestre"},
        {"id": "historique", "label": "Tout l'historique"},
    ],
    allow_free_text=True,
    expires_in_s=120,
)


def renderer() -> tuple[Renderer, io.StringIO]:
    out = io.StringIO()
    return Renderer(Console(file=out, width=100, color_system=None, highlight=False)), out


def typing(*lines: str) -> tuple[CliQuestionBroker, list[str]]:
    """A broker fed with these lines; the list keeps the prompts it showed."""
    remaining: Iterator[str] = iter(lines)
    prompts: list[str] = []

    def read_line(prompt: str) -> str:
        prompts.append(prompt)
        return next(remaining)

    return CliQuestionBroker(renderer()[0], read_line), prompts


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["1"], Answer("answered", option_id="trimestre")),
        ([" 2 "], Answer("answered", option_id="historique")),
        (["0", "Depuis janvier"], Answer("answered", text="Depuis janvier")),
        (["depuis janvier"], Answer("answered", text="depuis janvier")),
        (["7", "1"], Answer("answered", option_id="trimestre")),
        ([""], Answer("cancelled")),
        (["0", "  "], Answer("cancelled")),
    ],
    ids=["first", "second", "zero-then-text", "text-at-once", "retry", "empty", "empty-text"],
)
async def test_answers_from_the_keyboard(lines: list[str], expected: Answer) -> None:
    broker, _ = typing(*lines)
    assert await broker.ask(QUESTION) == expected


async def test_an_unknown_number_asks_again() -> None:
    broker, prompts = typing("9", "2")
    await broker.ask(QUESTION)
    assert prompts[1] == RETRY_PROMPT


async def test_text_is_refused_when_only_options_are_allowed() -> None:
    closed = PendingQuestion(**{**QUESTION.__dict__, "allow_free_text": False})
    broker, prompts = typing("les deux", "0", "1")
    assert await broker.ask(closed) == Answer("answered", option_id="trimestre")
    assert prompts[1:] == [RETRY_PROMPT, RETRY_PROMPT]


async def test_without_options_the_answer_is_free() -> None:
    free = PendingQuestion(**{**QUESTION.__dict__, "options": []})
    broker, prompts = typing("Les clients actifs")
    assert await broker.ask(free) == Answer("answered", text="Les clients actifs")
    assert prompts == [TEXT_PROMPT]


async def test_end_of_input_cancels() -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    assert await CliQuestionBroker(renderer()[0], closed).ask(QUESTION) == Answer("cancelled")


async def test_ctrl_c_cancels_the_question() -> None:
    release = threading.Event()
    render, out = renderer()
    broker = CliQuestionBroker(render, lambda prompt: release.wait(5) and "1")
    assert not broker.interrupt()  # nothing waiting yet
    pending = asyncio.ensure_future(broker.ask(QUESTION))
    await asyncio.sleep(0.05)
    assert broker.interrupt()
    assert await pending == Answer("cancelled")
    assert "interruption" in out.getvalue()
    release.set()  # the late "1" is ignored


async def test_timeout_is_reported() -> None:
    release = threading.Event()
    render, out = renderer()
    broker = CliQuestionBroker(render, lambda prompt: release.wait(5) and "1")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.ask(QUESTION), 0.05)
    assert "hypothèse" in out.getvalue()
    release.set()


def test_panel_numbers_the_choices() -> None:
    render, out = renderer()
    render.question(QUESTION.question, QUESTION.reason, QUESTION.options, True, 120)
    text = out.getvalue()
    for expected in (
        "Question de l'agent",
        "Pourquoi : Les deux périodes",
        "1. Ce trimestre",
        "2. Tout l'historique",
        "0. Autre réponse",
        "120 s",
    ):
        assert expected in text


async def test_the_period_question_in_the_chat() -> None:
    render, out = renderer()
    questions = CliQuestionBroker(render, lambda prompt: "1")
    client = FakeClient(
        tool_request(
            "ask_user", question=QUESTION.question, reason=QUESTION.reason, options=QUESTION.options
        ),
        answer("Sur ce trimestre, le délai moyen est de 41 jours."),
    )
    engine = Engine(
        Config(),
        client,
        registry=Registry(native_tools=[AskUserTool()]),
        questions=questions,
        sql_classifier=lambda sql: None,
    )
    session = ChatSession(engine, render, questions=questions)
    await session.ask("Quel est le délai moyen de paiement ?")
    text = out.getvalue()
    assert "1. Ce trimestre" in text
    assert "résultat : Réponse : Ce trimestre" in text
    assert "41 jours" in text
