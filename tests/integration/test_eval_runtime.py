"""EVAL-06 end to end: the harness on the real engine, the real sql_toolbox over MCP.

Only the model is scripted (FakeClient): the tools, the memory, the HITL policy, the
frozen clock and the reports are those of `sensai eval`. The figures prove the wiring,
not the model.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeClient, StreamItem, answer, tool_request
from sensai.__main__ import main
from sensai.core.config import Config, McpServerConfig
from sensai.data.seed import generate
from sensai.eval.golden import REFERENCE_DATE, load_golden
from sensai.eval.harness import RunSetup, run_and_report, run_suite
from sensai.eval.report import load_report
from sensai.eval.runtime import FROZEN_CLOCK, EvalEngine, EvalRuntime
from sensai.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = {entry.id: entry for entry in load_golden(ROOT / "eval/golden.jsonl")}
SELECT = "sql_toolbox__run_select"
TOOLBOX = McpServerConfig(command="python", args=("-m", "sensai.mcp_servers.sql_toolbox"))
# Would need uvx and the network: a run must never start it
TIME = McpServerConfig(command="uvx", args=("mcp-server-time", "--local-timezone=Europe/Paris"))

REPLIES: dict[str, list[list[StreamItem]]] = {
    "q005": [
        tool_request(FROZEN_CLOCK, timezone="Europe/Paris"),
        tool_request(SELECT, sql="SELECT COUNT(*) AS n FROM clients WHERE status = 'inactive'"),
        answer("Nous avons 40 clients inactifs."),
    ],
    "q014": [answer("Je ne réponds qu'aux questions sur les données de l'entreprise.")],
}


class ScriptedClients:
    """client_factory of the runtime: the next question's scripted model, in order."""

    def __init__(self, *ids: str) -> None:
        self.pending = [FakeClient(*REPLIES[question_id]) for question_id in ids]
        self.given: list[FakeClient] = []

    def __call__(self, config: Config) -> FakeClient:
        self.given.append(self.pending.pop(0))
        return self.given[-1]


def config() -> Config:
    # The judge needs Ollama's chat(): it has its own tests (test_judge.py)
    servers = {"sql_toolbox": TOOLBOX, "time": TIME}
    return Config(eval={"judge_enabled": False}, mcp={"servers": servers})


async def test_the_golden_set_on_the_real_tools(tmp_path: Path) -> None:
    clients = ScriptedClients("q005", "q014")
    async with EvalRuntime(config(), seed=42, client_factory=clients) as runtime:
        servers: list[str] = []

        async def stop() -> None:
            assert runtime.manager is not None
            servers.extend(status.name for status in runtime.manager.status())
            await runtime.aclose()

        results = await run_suite(
            [GOLDEN["q005"], GOLDEN["q014"]], runtime.factory, today=REFERENCE_DATE, teardown=stop
        )

    first, second = results
    assert (first.id, first.ex, first.sql_valid, first.error) == ("q005", True, True, None)
    assert (second.ex, second.error) == (None, None)
    assert servers == ["sql_toolbox"]  # the real time server is never started
    # T5 counters of the case: two valid tool calls, no judge call
    assert first.tool_arguments == {"first_try": 2, "repaired": 0, "failed": 0}
    assert first.judge_json == {"first_try": 0, "repaired": 0, "failed": 0}
    # What the model got: temperature 0, the frozen date, the date tool named in the prompt
    _model, messages, options = clients.given[0].calls[1]
    assert options is not None and options["temperature"] == 0.0
    clock = next(m for m in messages if m["role"] == "tool")
    assert '"datetime": "2026-10-20T09:00:00' in clock["content"]
    system = clients.given[0].calls[0][1][0]
    assert system["role"] == "system" and f"`{FROZEN_CLOCK}`" in system["content"]


async def test_each_case_gets_a_fresh_engine_and_its_own_memory(tmp_path: Path) -> None:
    business = tmp_path / "business.db"
    generate(business, seed=42, today=REFERENCE_DATE)
    clients = ScriptedClients("q014", "q014")
    async with EvalRuntime(config(), client_factory=clients) as runtime:
        await runtime.start(business)
        first = runtime.new_engine(tmp_path / "m1.db", REFERENCE_DATE)
        second = runtime.new_engine(tmp_path / "m2.db", REFERENCE_DATE)
        try:
            assert first.engine is not second.engine
            assert first.engine.session_id != second.engine.session_id
            assert first.store is not second.store
            assert first.engine.config.database.memory_path == tmp_path / "m1.db"
        finally:
            await first.aclose()
            await second.aclose()
        with pytest.raises(RuntimeError, match="another database"):
            await runtime.start(tmp_path / "other.db")


async def test_the_report_records_how_the_run_was_made(tmp_path: Path) -> None:
    suite = tmp_path / "suite.jsonl"
    lines = (ROOT / "eval/golden.jsonl").read_text(encoding="utf-8").splitlines()
    q005 = next(line for line in lines if json.loads(line)["id"] == "q005")
    suite.write_text(q005 + "\n", encoding="utf-8")
    seen: list[str] = []
    async with EvalRuntime(config(), client_factory=ScriptedClients("q005")) as runtime:
        path = await run_and_report(
            suite,
            runtime.factory,
            tag="v0.8-test",
            today=REFERENCE_DATE,
            info=runtime.info(),
            memory_rules_path=ROOT / "eval/memory_rules.json",
            reports_dir=tmp_path / "reports",
            run_day=date(2026, 10, 21),
            on_result=lambda result: seen.append(result.id),
            teardown=runtime.aclose,
        )

    report: dict[str, Any] = load_report(path)
    assert seen == ["q005"]
    assert report["metrics"]["ex"] == {"value": 1.0, "n": 1}
    assert report["metrics"]["tool_args_first_try"] == {"value": 1.0, "n": 2}
    assert (report["model"], report["temperature"], report["ollama_seed"]) == ("qwen3:8b", 0.0, 42)
    assert (report["prompt"]["name"], report["prompt"]["version"]) == ("system_sql", "v1")
    assert len(report["prompt"]["sha256"]) == 64
    assert report["persona"] is None
    assert report["fewshot"] == {"enabled": False, "bank": None}
    assert report["date_tool"] == "frozen"


async def test_an_eval_engine_closes_its_memory(tmp_path: Path) -> None:
    clients = ScriptedClients("q014")
    async with EvalRuntime(config(), client_factory=clients) as runtime:
        setup = RunSetup(tmp_path / "business.db", tmp_path / "memory.db", REFERENCE_DATE)
        generate(setup.business_db, seed=42, today=REFERENCE_DATE)
        engine = await runtime.factory(GOLDEN["q014"], setup)
        assert isinstance(engine, EvalEngine)
        await engine.aclose()

    assert clients.given[0].closed
    with MemoryStore.open(setup.memory_db):  # not locked by the closed engine
        pass


# --- `sensai eval` ---------------------------------------------------------------------


def test_eval_run_needs_a_tag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "--suite", "eval/golden.jsonl"]) == 2
    assert "--tag" in capsys.readouterr().err


def test_eval_run_refuses_an_invalid_tag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "--tag", "../v0.5"]) == 2
    assert "tag invalide" in capsys.readouterr().err


def test_eval_run_stops_at_once_when_ollama_is_down(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval", "--tag", "v0.8", "--host", "http://127.0.0.1:9"]) == 1
    assert "Ollama injoignable" in capsys.readouterr().err
