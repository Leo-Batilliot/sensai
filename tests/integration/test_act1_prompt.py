"""Act 1 with the real model (CORE-08): the agent's SQL gives the reference result (EX).

Needs Ollama with the configured model, so it is excluded from the CI (`-m "not ollama"`).
Run it with: python -m pytest -m ollama tests/integration/test_act1_prompt.py
"""

import contextlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeToolRegistry, select_tool, time_tool
from sensai.core import events
from sensai.core.config import load_config
from sensai.core.engine import Engine
from sensai.core.prompts import system_prompt
from sensai.data.seed import generate, quarter_start

pytestmark = pytest.mark.ollama

TODAY = date(2026, 10, 11)
GOLDEN = Path(__file__).resolve().parents[2] / "eval/golden.jsonl"


def golden(question_id: str) -> dict[str, Any]:
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["id"] == question_id:
            return entry
    raise KeyError(question_id)


def same_value(agent: Any, reference: Any) -> bool:
    """judge.md §3: numbers within max(0.01, 0.1 %), NULL only equals NULL, text as is."""
    if reference is None or agent is None:
        return agent is reference
    if isinstance(reference, (int, float)) and isinstance(agent, (int, float)):
        return abs(agent - reference) <= max(0.01, 0.001 * abs(reference))
    return bool(agent == reference)


def equivalent(agent: list[list[Any]], reference: list[tuple[Any, ...]]) -> bool:
    """judge.md §3 (ordered case): same rows, and each reference column matches a
    distinct agent column, whatever the names and extra columns."""
    if len(agent) != len(reference):
        return False
    free = set(range(len(agent[0]) if agent else 0))
    for ref_col in range(len(reference[0]) if reference else 0):
        match = next(
            (
                col
                for col in sorted(free)
                if all(
                    same_value(a[col], r[ref_col]) for a, r in zip(agent, reference, strict=True)
                )
            ),
            None,
        )
        if match is None:
            return False
        free.discard(match)
    return True


async def test_act1_question_gives_the_reference_result(tmp_path: Path) -> None:
    db = tmp_path / "business.db"
    generate(db, seed=42, today=TODAY)
    registry = FakeToolRegistry(
        time_tool(now=f"{TODAY.isoformat()}T10:00:00+02:00"), select_tool(db_path=db)
    )
    tools = [tool.name for tool in registry.tools()]
    engine = Engine(load_config(), registry=registry, system_prompt=system_prompt(db, tools))
    entry = golden("q001")

    try:
        async with contextlib.aclosing(engine.run_turn(entry["question"])) as turn:
            emitted = [event async for event in turn]
    finally:
        await engine.aclose()

    sqls = [event.sql for event in emitted if isinstance(event, events.SqlGenerated)]
    assert all("date('now')" not in sql.lower() for sql in sqls), sqls
    selects = [
        event
        for event in emitted
        if isinstance(event, events.ToolResult) and event.ok and event.data and "rows" in event.data
    ]
    assert selects, f"aucun run_select réussi ; SQL essayés : {sqls}"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        reference = conn.execute(
            entry["gold_sql"], {"q_start": quarter_start(TODAY).isoformat()}
        ).fetchall()
    agent_rows = selects[-1].data["rows"]  # judge.md §3: the last successful run_select
    assert equivalent(agent_rows, reference), f"SQL : {sqls[-1]}\nagent : {agent_rows}"
    assert isinstance(emitted[-1], events.MessageDone)
