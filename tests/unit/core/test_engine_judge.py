"""Online judge in the Engine (EVAL-05): judge_started and judge_report after message_done.

The answer model is the scripted FakeClient; the judge model is JudgeChat, which plays
judge_report.v1 reports. The badge is computed from the report after each numeric answer.
"""

import contextlib
import json
from typing import Any

from event_checks import VALIDATORS, check_turn
from fakes import FakeClient, FakeToolRegistry, answer, select_tool, tool_request
from sensai.core import events
from sensai.core.config import AgentConfig, Config, EvalConfig
from sensai.core.engine import Engine
from sensai.eval.judge import Judge
from sensai.eval.online import OnlineJudge
from sensai.llm.ollama_client import Chunk, Message

SELECT = "sql_toolbox__run_select"
TOP_CLIENTS = "SELECT name, marge_eur FROM clients ORDER BY marge_eur DESC LIMIT 2"
ANSWER = "Brasserie Lemaire arrive en tête avec 48 712,40 € de marge."


def report(*verdicts: str) -> dict[str, Any]:
    return {
        "claims": [
            {"text": "48 712,40 €", "verdict": verdict, "evidence": "e", "expected_value": None}
            for verdict in verdicts
        ],
        "relevance": 5,
        "sql_answers_question": True,
        "key_figures": [],
        "assumptions": [],
        "followups": [],
    }


class JudgeChat:
    """The judge model: one report per call; records whether the session was busy."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.engine: Engine | None = None
        self.busy_when_called: list[bool] = []
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        if self.engine is not None:
            self.busy_when_called.append(self.engine.busy)
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Chunk(content=json.dumps(reply), done=True)


def make_engine(*replies: Any, judge_chat: JudgeChat, config: Config | None = None) -> Engine:
    engine = Engine(
        config or Config(),
        FakeClient(*replies),
        registry=FakeToolRegistry(select_tool()),
        judge=OnlineJudge(Judge(judge_chat, "qwen3:8b")),
        session_id="s-1",
    )
    judge_chat.engine = engine
    return engine


async def run(
    engine: Engine, message: str = "Quels sont nos 2 meilleurs clients ?"
) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        return [event async for event in turn]


def split(emitted: list[events.Event]) -> tuple[list[events.Event], list[events.Event]]:
    """The turn itself (up to message_done) and the judge events after it."""
    end = next(i for i, event in enumerate(emitted) if event.type == "message_done")
    return emitted[: end + 1], emitted[end + 1 :]


def check_judge(judged: list[events.Event], turn_id: str | None) -> None:
    assert [event.type for event in judged] == ["judge_started", "judge_report"]
    for event in judged:
        assert event.turn_id == turn_id
        data = json.loads(json.dumps(event.to_dict()))
        assert [error.message for error in VALIDATORS[event.type].iter_errors(data)] == []


async def test_a_numeric_answer_gets_a_badge_after_message_done() -> None:
    chat = JudgeChat(report("supported"))
    engine = make_engine(tool_request(SELECT, sql=TOP_CLIENTS), answer(ANSWER), judge_chat=chat)

    turn, judged = split(await run(engine))

    check_turn(turn)
    check_judge(judged, turn[0].turn_id)
    verdict = judged[1]
    assert isinstance(verdict, events.JudgeReport)
    assert verdict.badge == "verified"
    assert verdict.scores == {"faithfulness": 1.0}
    assert verdict.numeric_check == {"status": "ok", "unmatched": []}


async def test_a_contradicted_answer() -> None:
    chat = JudgeChat(report("contradicted"))
    wrong = "Brasserie Lemaire arrive en tête avec 52 300 € de marge."
    engine = make_engine(tool_request(SELECT, sql=TOP_CLIENTS), answer(wrong), judge_chat=chat)

    _, judged = split(await run(engine))

    verdict = judged[1]
    assert isinstance(verdict, events.JudgeReport)
    assert verdict.badge == "contradicted"
    assert verdict.numeric_check == {"status": "mismatch", "unmatched": ["52 300 €"]}


async def test_the_session_is_free_while_the_judge_runs() -> None:
    chat = JudgeChat(report("supported"))
    engine = make_engine(tool_request(SELECT, sql=TOP_CLIENTS), answer(ANSWER), judge_chat=chat)

    await run(engine)

    assert chat.busy_when_called == [False]
    assert not engine.busy


async def test_the_judge_sees_all_the_rows_not_the_copy_for_the_model() -> None:
    chat = JudgeChat(report("supported"))
    config = Config(agent=AgentConfig(max_result_rows=1))
    engine = make_engine(
        tool_request(SELECT, sql=TOP_CLIENTS), answer(ANSWER), judge_chat=chat, config=config
    )

    await run(engine)

    (messages,) = chat.calls
    assert "Imprimerie Vasseur" in messages[-1]["content"]


async def test_a_failing_judge_still_reports() -> None:
    chat = JudgeChat(RuntimeError("judge down"))
    engine = make_engine(tool_request(SELECT, sql=TOP_CLIENTS), answer(ANSWER), judge_chat=chat)

    turn, judged = split(await run(engine))

    check_judge(judged, turn[0].turn_id)
    verdict = judged[1]
    assert isinstance(verdict, events.JudgeReport)
    assert (verdict.badge, verdict.report) == ("unavailable", None)


async def test_no_run_select_no_judge() -> None:
    chat = JudgeChat()
    engine = make_engine(answer("Bonjour !"), judge_chat=chat)

    emitted = await run(engine, "Bonjour")

    assert emitted[-1].type == "message_done"
    assert chat.calls == []


async def test_a_failed_run_select_is_not_judged() -> None:
    chat = JudgeChat()
    engine = make_engine(
        tool_request(SELECT, sql="SELECT * FROM inconnue"),
        answer("Je n'ai pas trouvé la table."),
        judge_chat=chat,
    )

    emitted = await run(engine)

    assert emitted[-1].type == "message_done"
    assert chat.calls == []


async def test_judge_disabled_in_the_config() -> None:
    chat = JudgeChat()
    config = Config(eval=EvalConfig(judge_enabled=False))
    engine = make_engine(
        tool_request(SELECT, sql=TOP_CLIENTS), answer(ANSWER), judge_chat=chat, config=config
    )

    emitted = await run(engine)

    assert emitted[-1].type == "message_done"
    assert chat.calls == []


async def test_an_interrupted_turn_is_not_judged() -> None:
    chat = JudgeChat()
    engine = make_engine(
        tool_request(SELECT, sql=TOP_CLIENTS), answer("Brasserie", " Lemaire"), judge_chat=chat
    )
    emitted: list[events.Event] = []

    async with contextlib.aclosing(engine.run_turn("Top 2 ?")) as turn:
        async for event in turn:
            emitted.append(event)
            if event.type == "token":
                engine.cancel()

    done = emitted[-1]
    assert isinstance(done, events.MessageDone) and done.interrupted
    assert chat.calls == []


async def test_a_turn_ended_by_an_error_is_not_judged() -> None:
    chat = JudgeChat()
    engine = make_engine(
        tool_request(SELECT, sql=TOP_CLIENTS),
        [RuntimeError("stream broken")],
        judge_chat=chat,
    )

    emitted = await run(engine)

    assert emitted[-1].type == "error"
    assert chat.calls == []
