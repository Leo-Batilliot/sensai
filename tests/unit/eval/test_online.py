"""Online judge (EVAL-05): which tool result is checked, and a verdict even on failure."""

import json
from typing import Any

from fakes import fake_tool
from sensai.core.config import Config, EvalConfig, OllamaConfig
from sensai.eval.judge import Judge
from sensai.eval.online import OnlineJudge, SelectResult, select_result
from sensai.llm.ollama_client import Chunk, Message
from sensai.tools.base import ToolResult

SQL = "SELECT name, marge_eur FROM clients ORDER BY marge_eur DESC LIMIT 2"
ROWS = [["Brasserie Lemaire", 48712.4], ["Imprimerie Vasseur", 41208.15]]
DATA = {"columns": ["name", "marge_eur"], "rows": ROWS, "row_count": 2, "truncated": False}
ANSWER = "Brasserie Lemaire arrive en tête avec 48 712,40 € de marge."


async def _noop(arguments: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, text="", summary="")


SELECT_INFO = fake_tool("sql_toolbox__run_select", _noop, server="sql_toolbox").info
OTHER_INFO = fake_tool("sql_toolbox__preview_write", _noop, server="sql_toolbox").info


def ok(data: dict[str, Any] | None = DATA) -> ToolResult:
    return ToolResult(ok=True, text="{}", summary="2 lignes", data=data)


def report(verdict: str = "supported") -> dict[str, Any]:
    return {
        "claims": [
            {"text": "48 712,40 €", "verdict": verdict, "evidence": "e", "expected_value": None}
        ],
        "relevance": 5,
        "sql_answers_question": True,
        "key_figures": [],
        "assumptions": [],
        "followups": [],
    }


class JudgeChat:
    """Stands in for OllamaClient.chat: one reply per call, or an exception."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Chunk(content=json.dumps(reply), done=True)


# --- select_result ---------------------------------------------------------------------


def test_a_successful_run_select_is_kept_with_its_full_rows() -> None:
    select = select_result(SELECT_INFO, {"sql": SQL, "purpose": "top 2"}, ok())

    assert select == SelectResult(SQL, DATA["columns"], ROWS, truncated=False)


def test_the_truncated_flag_is_kept() -> None:
    select = select_result(SELECT_INFO, {"sql": SQL}, ok({**DATA, "truncated": True}))

    assert select is not None and select.truncated


def test_other_calls_are_not_numeric_answers() -> None:
    failed = ToolResult.failure("SQL_ERROR", "no such table")

    assert select_result(OTHER_INFO, {"sql": SQL}, ok()) is None
    assert select_result(None, {"sql": SQL}, ok()) is None
    assert select_result(SELECT_INFO, {"sql": SQL}, failed) is None
    assert select_result(SELECT_INFO, {"sql": SQL}, ok(None)) is None
    assert select_result(SELECT_INFO, {}, ok()) is None


# --- OnlineJudge -----------------------------------------------------------------------


async def test_verdict_of_a_supported_answer() -> None:
    online = OnlineJudge(Judge(JudgeChat(report()), "qwen3:8b"))

    verdict = await online.verdict(
        "Top 2 ?", SelectResult(SQL, DATA["columns"], ROWS, False), ANSWER
    )

    assert verdict.badge == "verified"
    assert verdict.report == report()


async def test_an_unexpected_failure_still_gives_a_verdict() -> None:
    online = OnlineJudge(Judge(JudgeChat(RuntimeError("boom")), "qwen3:8b"))
    select = SelectResult(SQL, DATA["columns"], ROWS, False)

    good = await online.verdict("Top 2 ?", select, ANSWER)
    online.judge.client = JudgeChat(RuntimeError("boom"))
    wrong = await online.verdict("Top 2 ?", select, "La marge est de 52 300 €.")

    assert (good.badge, good.report, good.numeric_check.status) == ("unavailable", None, "ok")
    assert wrong.badge == "contradicted"  # the numeric check does not need the model
    assert wrong.numeric_check.unmatched == ("52 300 €",)


def test_from_config_uses_the_judge_model_and_tolerance() -> None:
    config = Config(
        ollama=OllamaConfig(model="qwen3:8b", judge_model="llama3.1:8b", num_ctx=4096),
        eval=EvalConfig(numeric_tolerance=0.05),
    )

    online = OnlineJudge.from_config(config, JudgeChat())

    assert online.judge.model == "llama3.1:8b"
    assert online.judge.tolerance == 0.05
    assert online.judge.options == {"temperature": 0, "num_ctx": 4096}
