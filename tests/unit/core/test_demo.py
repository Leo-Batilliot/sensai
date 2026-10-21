"""Demo mode of act 3 (CORE-20): one figure of the answer is made wrong before the judge.

The engine tests use a judge model that never answers: the badge then comes from the
numeric check alone (judge.md §2, rule 1), so the acceptance test needs no LLM.
"""

import contextlib
import logging
from pathlib import Path
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, FakeToolRegistry, answer, select_tool, tool_request
from sensai.core import events
from sensai.core.config import Config, DemoConfig, load_config
from sensai.core.demo import AnswerAlteration, demo_notice
from sensai.core.engine import Engine
from sensai.eval.judge import Judge
from sensai.eval.online import OnlineJudge, SelectResult
from sensai.llm.ollama_client import Chunk, Message

QUESTION = "Quelle est la marge de Brasserie Lemaire ce trimestre ?"
ANSWER = "La marge de Brasserie Lemaire ce trimestre est de 48 712,40 €."
ALTERED = "La marge de Brasserie Lemaire ce trimestre est de 56 019,26 €."
ROWS = [["Brasserie Lemaire", 48712.4]]
DEMO = Config(demo=DemoConfig(alter_answer=True))


def alteration(rows: list[list[Any]] = ROWS, question: str = QUESTION) -> AnswerAlteration:
    select = SelectResult("SELECT name, marge_eur FROM clients", ["name", "marge_eur"], rows, False)
    alteration = AnswerAlteration.for_turn(DEMO, question, [select])
    assert alteration is not None
    return alteration


def altered(text: str, rows: list[list[Any]] = ROWS, question: str = QUESTION) -> str:
    demo = alteration(rows, question)
    return demo.feed(text) + demo.flush()


# --- the alteration ---


def test_the_first_figure_of_the_rows_is_raised_by_15_percent() -> None:
    assert altered(ANSWER) == ALTERED


def test_streamed_one_character_at_a_time_gives_the_same_text() -> None:
    demo = alteration()
    shown = [demo.feed(char) for char in ANSWER] + [demo.flush()]
    assert "".join(shown) == ALTERED
    # The number is held back until complete: no piece of the true figure is ever shown
    assert not any("48" in piece for piece in shown)


@pytest.mark.parametrize(
    ("written", "cell", "expected"),
    [
        ("48 712,40 €", 48712.4, "56 019,26 €"),
        ("48 712,40 €", 48712.4, "56 019,26 €"),  # French spaces kept
        ("18,4 k€", 18412.3, "21,2 k€"),
        ("45 %", 0.45, "52 %"),
        ("48,712.40", 48712.4, "56,019.26"),
        ("47,3 jours", 47.3, "54,4 jours"),
    ],
)
def test_the_figure_keeps_its_format(written: str, cell: float, expected: str) -> None:
    assert altered(f"Brasserie Lemaire : {written}.", rows=[[cell]]) == (
        f"Brasserie Lemaire : {expected}."
    )


def test_only_the_first_figure_is_altered() -> None:
    text = "Marge : 48 712,40 €, soit 48 712,40 € sur le trimestre."
    assert altered(text) == "Marge : 56 019,26 €, soit 48 712,40 € sur le trimestre."


def test_a_number_of_the_question_is_kept() -> None:
    question = "Brasserie Lemaire : quelle marge sur 3 mois ?"
    text = "Sur 3 mois, la marge est de 1 200 €."
    assert altered(text, rows=[[3, 1200]], question=question) == (
        "Sur 3 mois, la marge est de 1 380 €."
    )


def test_a_figure_that_rounds_to_itself_is_skipped() -> None:
    text = "1 facture en retard, pour 2 400 €."  # 1 x 1.15 is still written 1
    assert altered(text, rows=[[1, 2400]]) == "1 facture en retard, pour 2 760 €."


def test_an_answer_without_a_figure_of_the_rows_is_intact() -> None:
    text = "Brasserie Lemaire n'a aucune facture ce trimestre."
    assert altered(text) == text


def test_before_any_run_select_nothing_is_held_back() -> None:
    demo = AnswerAlteration.for_turn(DEMO, QUESTION, [])
    assert demo is not None
    assert demo.feed("Je cherche les 3 dernières factures") == (
        "Je cherche les 3 dernières factures"
    )


@pytest.mark.parametrize(
    ("config", "question", "applies"),
    [
        (DEMO, QUESTION, True),
        (DEMO, "quelle est la marge de brasserie lemaire ?", True),  # any case
        (DEMO, "Quels sont nos 5 meilleurs clients ?", False),
        (Config(), QUESTION, False),  # off by default
    ],
)
def test_the_demo_mode_applies_to_the_trigger_question_only(
    config: Config, question: str, applies: bool
) -> None:
    assert (AnswerAlteration.for_turn(config, question, []) is not None) == applies


def test_the_notice_names_the_trigger() -> None:
    assert demo_notice(Config()) is None
    notice = demo_notice(DEMO)
    assert notice is not None
    assert "Mode démo actif" in notice and "Brasserie Lemaire" in notice


def test_the_demo_mode_can_be_switched_on_from_the_environment(tmp_path: Path) -> None:
    empty = tmp_path / "config.toml"
    empty.write_text("", encoding="utf-8")
    config = load_config(empty, environ={"SENSAI_DEMO_ALTER_ANSWER": "true"})
    assert config.demo == DemoConfig(alter_answer=True, alter_trigger="Brasserie Lemaire")


# --- in the Engine ---


class DownJudge:
    """A judge model that never answers: the badge comes from the numeric check alone."""

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        raise RuntimeError("judge down")


async def run_engine(config: Config, question: str = QUESTION) -> tuple[Engine, list[Any]]:
    sql = "SELECT name, marge_eur FROM clients WHERE name = 'Brasserie Lemaire'"
    engine = Engine(
        config,
        # The figure is cut across two tokens, as a model streams it
        FakeClient(
            tool_request("sql_toolbox__run_select", sql=sql), answer(ANSWER[:-9], ANSWER[-9:])
        ),
        registry=FakeToolRegistry(select_tool()),
        judge=OnlineJudge(Judge(DownJudge(), "qwen3:8b")),
        session_id="s-1",
    )
    async with contextlib.aclosing(engine.run_turn(question)) as turn:
        return engine, [event async for event in turn]


def outcome(emitted: list[Any]) -> tuple[events.MessageDone, events.JudgeReport]:
    done = next(event for event in emitted if isinstance(event, events.MessageDone))
    report = next(event for event in emitted if isinstance(event, events.JudgeReport))
    return done, report


async def test_the_numeric_check_alone_contradicts_the_altered_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="sensai.core.demo"):
        engine, emitted = await run_engine(DEMO)

    done, report = outcome(emitted)
    check_turn(emitted[: emitted.index(done) + 1])  # tokens = content, schemas, order
    assert done.content == ALTERED
    assert report.badge == "contradicted"
    assert report.numeric_check == {"status": "mismatch", "unmatched": ["56 019,26 €"]}
    # The history keeps what the user saw
    assert engine.history.messages_for("suite")[-2] == {"role": "assistant", "content": ALTERED}
    assert "mode démo : réponse altérée" in caplog.text


async def test_another_question_is_intact_in_demo_mode() -> None:
    _, emitted = await run_engine(DEMO, question="Quelle est notre marge ce trimestre ?")
    done, report = outcome(emitted)
    assert done.content == ANSWER
    assert report.numeric_check == {"status": "ok", "unmatched": []}


async def test_without_the_demo_mode_the_answer_is_intact() -> None:
    _, emitted = await run_engine(Config())
    done, report = outcome(emitted)
    assert done.content == ANSWER
    assert report.numeric_check == {"status": "ok", "unmatched": []}
