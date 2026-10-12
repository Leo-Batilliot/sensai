"""MEM-04 with the real model: a glossary rule in memory changes the generated SQL.

Needs Ollama with the configured model, so it is excluded from the CI (`-m "not ollama"`).
Run it with: python -m pytest -m ollama tests/integration/test_memory_rule.py
"""

import contextlib
from datetime import date
from pathlib import Path

import pytest

from fakes import FakeToolRegistry, select_tool, time_tool
from sensai.core import events
from sensai.core.config import load_config
from sensai.core.engine import Engine
from sensai.core.prompts import system_prompt_template
from sensai.data.seed import generate
from sensai.memory.context import MemoryContext
from sensai.memory.store import MemoryStore

pytestmark = pytest.mark.ollama

TODAY = date(2026, 10, 11)
QUESTION = "Quels sont nos clients rentables depuis le 1er juillet 2026 ?"
RULE = "Un client est rentable si sa marge brute dépasse 25 % de son chiffre d'affaires HT."
HINT = "SUM(ol.quantity * (ol.unit_price - ol.unit_cost)) > 0.25 * SUM(ol.quantity * ol.unit_price)"


async def generated_sql(db: Path, memory: MemoryContext | None) -> list[str]:
    registry = FakeToolRegistry(
        time_tool(now=f"{TODAY.isoformat()}T10:00:00+02:00"), select_tool(db_path=db)
    )
    template = system_prompt_template(db, [tool.name for tool in registry.tools()])
    engine = Engine(load_config(), registry=registry, system_prompt=template, memory=memory)
    try:
        async with contextlib.aclosing(engine.run_turn(QUESTION)) as turn:
            emitted = [event async for event in turn]
    finally:
        await engine.aclose()
    return [event.sql for event in emitted if isinstance(event, events.SqlGenerated)]


async def test_the_glossary_rule_shows_in_the_sql(tmp_path: Path) -> None:
    db = tmp_path / "business.db"
    generate(db, seed=42, today=TODAY)
    with MemoryStore.open(tmp_path / "memory.db") as store:
        store.define_term("client rentable", RULE, sql_hint=HINT)
        sqls = await generated_sql(db, MemoryContext(store))

    assert sqls, "aucun SQL généré"
    assert any("0.25" in sql or "25" in sql for sql in sqls), sqls
