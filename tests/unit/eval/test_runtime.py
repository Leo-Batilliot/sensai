"""Real engine of the offline runs (EVAL-06): frozen inputs, seed, report information."""

import hashlib
import json
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from sensai.core.config import Config, McpServerConfig
from sensai.core.prompts import DATE_TOOL_SUFFIX, SYSTEM_SQL
from sensai.eval.harness import QuestionResult, compute_latency, compute_metrics
from sensai.eval.report import compare, to_markdown
from sensai.eval.runtime import (
    FROZEN_CLOCK,
    FrozenClock,
    SeededOllamaClient,
    check_models,
    eval_config,
    frozen_now,
    prompt_info,
)
from sensai.llm.ollama_client import OllamaClient

TODAY = date(2026, 10, 20)
TOOLBOX = McpServerConfig(
    command="python", args=("-m", "sensai.mcp_servers.sql_toolbox", "--db", "data/business.db")
)
TIME = McpServerConfig(command="uvx", args=("mcp-server-time", "--local-timezone=Europe/Paris"))


# --- configuration of a run ------------------------------------------------------------


def test_a_run_has_temperature_zero_and_its_own_database(tmp_path: Path) -> None:
    config = Config(mcp={"servers": {"sql_toolbox": TOOLBOX, "horloge": TIME}})

    run = eval_config(config, tmp_path / "business.db", tmp_path / "memory.db")

    assert run.agent.temperature == 0.0
    assert run.database.business_path == tmp_path / "business.db"
    assert run.database.memory_path == tmp_path / "memory.db"
    # The time server is left out (frozen clock), whatever its name
    assert list(run.mcp.servers) == ["sql_toolbox"]
    # A --db of config.toml would read the real database: the McpManager adds the run's
    assert "--db" not in run.mcp.servers["sql_toolbox"].args
    assert config.agent.temperature == 0.1  # the base configuration is not changed


def test_the_time_server_is_recognised_by_its_name_too() -> None:
    other = McpServerConfig(command="my-clock", args=())
    config = Config(mcp={"servers": {"time": other, "sql_toolbox": TOOLBOX}})

    assert list(eval_config(config, Path("b.db")).mcp.servers) == ["sql_toolbox"]


# --- frozen clock ----------------------------------------------------------------------


async def test_the_frozen_clock_answers_the_reference_date() -> None:
    clock = FrozenClock(TODAY)

    result = await clock.call({"timezone": "Europe/Paris"})

    assert result.ok
    data = json.loads(result.text)
    assert data == result.data
    assert data["datetime"].startswith("2026-10-20T09:00:00")
    assert data["day_of_week"] == "Tuesday"
    assert data["timezone"] == "Europe/Paris"


def test_the_frozen_clock_looks_like_the_time_server() -> None:
    # The prompt names the date tool by its suffix (MCP-06): the rule does not change
    assert FROZEN_CLOCK == "time__get_current_time"
    assert FROZEN_CLOCK.endswith(DATE_TOOL_SUFFIX)
    Draft202012Validator.check_schema(FrozenClock.input_schema)
    assert FrozenClock.annotations.read_only is True
    assert FrozenClock.annotations.destructive is False


def test_frozen_now_is_on_the_reference_date() -> None:
    assert frozen_now(TODAY).date() == TODAY


# --- reproducibility of the model calls ------------------------------------------------


async def test_every_chat_request_carries_the_seed() -> None:
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        line = {"message": {"role": "assistant", "content": "ok"}, "done": True}
        return httpx.Response(200, text=json.dumps(line) + "\n")

    client = SeededOllamaClient(
        "http://ollama.test", seed=7, transport=httpx.MockTransport(handler)
    )
    async with client:
        async for _chunk in client.chat_stream("m", [], options={"temperature": 0.0}):
            pass
        await client.chat("m", [], format={"type": "object"})

    assert payloads[0]["options"] == {"temperature": 0.0, "seed": 7}
    assert payloads[1]["options"] == {"seed": 7}  # the judge's structured calls too


# --- report information ----------------------------------------------------------------


def test_prompt_info_gives_the_version_and_fingerprint_of_the_file() -> None:
    data = resources.files("sensai.core").joinpath("prompts", SYSTEM_SQL).read_bytes()

    assert prompt_info() == {
        "name": "system_sql",
        "version": "v1",
        "file": SYSTEM_SQL,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


async def test_check_models_when_ollama_is_down() -> None:
    config = Config(ollama={"host": "http://127.0.0.1:9", "timeout_s": 2})

    problem = await check_models(config)

    assert problem is not None and "Ollama injoignable" in problem


async def test_check_models_names_the_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def installed(self: OllamaClient) -> list[str]:
        return ["qwen3:8b", "llama3.1:latest"]

    monkeypatch.setattr(OllamaClient, "list_models", installed)

    assert await check_models(Config(ollama={"model": "qwen3:8b"})) is None
    assert await check_models(Config(ollama={"model": "llama3.1"})) is None  # :latest
    problem = await check_models(Config(ollama={"model": "qwen3:8b", "judge_model": "mistral"}))
    assert problem is not None and "ollama pull mistral" in problem


# --- valid JSON (T5) and latency -------------------------------------------------------


def question(
    question_id: str,
    tool_arguments: dict[str, int] | None,
    judge_json: dict[str, int] | None,
    *,
    first_token_ms: int | None = None,
) -> QuestionResult:
    return QuestionResult(
        id=question_id,
        category="aggregate",
        expects_write=False,
        requires_memory=False,
        answer="",
        sql=None,
        sql_valid=None,
        ex=None,
        confirmation_requested=False,
        unconfirmed_write=False,
        memory_refs=[],
        badge=None,
        faithfulness=None,
        relevance=None,
        error=None,
        duration_ms=100,
        first_token_ms=first_token_ms,
        judge_ms=None,
        tool_arguments=tool_arguments,
        judge_json=judge_json,
    )


def test_json_metrics_sum_the_counters_of_every_question() -> None:
    results = [
        question("q1", {"first_try": 2, "repaired": 1, "failed": 0}, {"first_try": 1, "repaired": 0, "failed": 0}),
        question("q2", {"first_try": 0, "repaired": 0, "failed": 1}, {"first_try": 0, "repaired": 1, "failed": 0}),
        question("q3", None, None),  # an engine without counters
    ]  # fmt: skip

    metrics = compute_metrics(results)

    assert metrics["tool_args_first_try"] == {"value": 0.5, "n": 4}
    assert metrics["tool_args_valid"] == {"value": 0.75, "n": 4}
    assert metrics["judge_json_first_try"] == {"value": 0.5, "n": 2}
    assert metrics["judge_json_valid"] == {"value": 1.0, "n": 2}


def test_json_metrics_without_any_call() -> None:
    metrics = compute_metrics([question("q1", None, None)])

    assert metrics["judge_json_valid"] == {"value": None, "n": 0}


def test_latency_has_p95_of_the_first_token() -> None:
    results = [question(f"q{i}", None, None, first_token_ms=i * 10) for i in range(1, 21)]

    latency = compute_latency(results)

    assert (latency["first_token_p50_ms"], latency["first_token_p95_ms"]) == (100, 190)
    assert latency["judge_p95_ms"] is None


# --- the run information in the reports ------------------------------------------------


def report(tag: str, **info: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "tag": tag,
        "suite": "eval/golden.jsonl",
        "today": "2026-10-20",
        "seed": 42,
        "model": "qwen3:8b",
        "judge_model": "qwen3:8b",
        "persona": None,
        "prompt": {"name": "system_sql", "version": "v1", "file": "x", "sha256": "3f2a9c1b77"},
        "fewshot": {"enabled": False, "bank": None},
        "questions": 0,
        "metrics": {},
        "latency": {},
        "results": [],
        **info,
    }


def test_markdown_names_the_prompt_profile_and_examples() -> None:
    markdown = to_markdown(report("v0.8"))

    assert (
        "- Prompt : system_sql v1 (3f2a9c1b) ; profil : aucun ; exemples dynamiques : désactivés"
    ) in markdown
    assert "| JSON du juge valide au 1er essai | — | 0 |" in markdown


def test_compare_says_what_changed_in_the_run() -> None:
    after = report(
        "v0.8-a4",
        prompt={"name": "system_sql", "version": "v2", "file": "y", "sha256": "77aa00bb11"},
        fewshot={"enabled": True, "bank": "examples.jsonl"},
    )

    table = compare(report("v0.8"), after)

    assert "| Prompt | system_sql v1 (3f2a9c1b) | system_sql v2 (77aa00bb) |" in table
    assert "| Exemples dynamiques | désactivés | activés |" in table
    assert "| Modèle |" not in table  # unchanged: not repeated


def test_compare_of_identical_runs_has_no_run_table() -> None:
    assert "| Exécution |" not in compare(report("a"), report("b"))
