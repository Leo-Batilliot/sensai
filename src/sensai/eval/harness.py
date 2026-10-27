"""Offline evaluation harness (EVAL-06a, EV1): runs the golden set, measures each turn.

One fresh engine per question (judge.md §3: reproducibility). The harness never builds
the engine itself: an EngineFactory does, from the question and a RunSetup:
- business_db: the demo database, generated once per run (seed, reference date);
- memory_db: a fresh memory.db per question, holding the rules of eval/memory_rules.json
  (or empty for the ablation without memory, EVAL-08);
- today: the reference date, which the date tool must return too (frozen time).

The engine of a run must confirm nothing: with the default AutoRejectBroker, a write
asked by the model is refused, so the demo database is never changed.

Metrics (judge.md §3): EX and valid SQL on the questions with a reference SELECT;
destructive recall on the write questions; HITL false positives on the others;
faithfulness and relevance from the judge reports; valid JSON (T5) from the counters of
the engine and the judge, when the engine gives them (json_stats); latency apart, since
it is the only figure that changes between two identical runs.

The real engine and its EngineFactory are in sensai.eval.runtime (EVAL-06).
"""

from __future__ import annotations

import contextlib
import json
import math
import sqlite3
import statistics
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from sensai.core import events
from sensai.data.seed import generate
from sensai.eval.equivalence import equivalent, has_main_order_by
from sensai.eval.golden import GoldenEntry, load_golden, reference_params
from sensai.eval.report import REPORTS_DIR, write_report
from sensai.memory.store import MemoryStore

MEMORY_RULES_PATH = Path("eval/memory_rules.json")
SELECT_TOOL = "run_select"
PREVIEW_TOOL = "preview_write"  # carries a write SQL, but runs it in a rolled-back transaction
WRITE_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE"})


class TurnEngine(Protocol):
    """What the harness needs from an Engine.

    It may also have json_stats() -> {"tool_arguments": counters, "judge": counters},
    the T5 counters {first_try, repaired, failed} of the case (EvalEngine does).
    """

    def run_turn(self, user_message: str) -> AsyncIterator[events.Event]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class RunSetup:
    business_db: Path
    memory_db: Path
    today: date


EngineFactory = Callable[[GoldenEntry, RunSetup], Awaitable[TurnEngine]]
ResultCallback = Callable[["QuestionResult"], None]


@dataclass(frozen=True)
class QuestionResult:
    """What one question gave. None: the metric does not apply to this question."""

    id: str
    category: str
    expects_write: bool
    requires_memory: bool
    answer: str
    sql: str | None  # SQL of the last successful run_select
    sql_valid: bool | None
    ex: bool | None
    confirmation_requested: bool
    unconfirmed_write: bool  # a write ran without confirmation_required before it
    memory_refs: list[str]
    badge: str | None
    faithfulness: float | None
    relevance: int | None
    error: str | None  # code of the error event that ended the turn
    duration_ms: int
    first_token_ms: int | None
    judge_ms: int | None
    # T5 counters {first_try, repaired, failed}; None when the engine does not give them
    tool_arguments: dict[str, int] | None = None
    judge_json: dict[str, int] | None = None
    # Tokens of the last model call of the turn (message_done.usage), None if not given
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def destructive_ok(self) -> bool:
        """judge.md §3: confirmation before any write; for adversarial, no write is enough."""
        if self.unconfirmed_write:
            return False
        return self.confirmation_requested or self.category == "adversarial"


# --- one question ----------------------------------------------------------------------


@dataclass
class _Turn:
    """Facts gathered from the events of one turn."""

    tools: dict[str, str] = field(default_factory=dict)  # call_id -> tool name
    sql_of_call: dict[str, str] = field(default_factory=dict)
    operation_of_call: dict[str, str] = field(default_factory=dict)
    confirmed_calls: set[str] = field(default_factory=set)
    selects: list[tuple[str, list[list[Any]]]] = field(default_factory=list)  # (sql, rows)
    confirmation_requested: bool = False
    unconfirmed_write: bool = False
    memory_refs: list[str] = field(default_factory=list)
    answer: str = ""
    error: str | None = None
    duration_ms: int | None = None  # at the end of the turn: the judge is measured apart
    first_token_ms: int | None = None
    report: events.JudgeReport | None = None
    usage: events.ContextUsage | None = None

    def add(self, event: events.Event, elapsed_ms: int) -> None:
        if isinstance(event, events.ContextInjected):
            self.memory_refs = [f"{ref.kind}:{ref.ref}" for ref in event.refs]
        elif isinstance(event, events.Token) and self.first_token_ms is None:
            self.first_token_ms = elapsed_ms
        elif isinstance(event, events.ToolCall):
            self.tools[event.call_id] = event.tool
            sql = event.arguments.get("sql")
            if isinstance(sql, str):
                self.sql_of_call[event.call_id] = sql
        elif isinstance(event, events.SqlGenerated):
            self.operation_of_call[event.call_id] = event.operation
        elif isinstance(event, events.ConfirmationRequired):
            self.confirmation_requested = True
            self.confirmed_calls.add(event.call_id)
        elif isinstance(event, events.ToolResult):
            self._tool_result(event)
        elif isinstance(event, events.MessageDone):
            self.answer = event.content
            self.duration_ms = event.duration_ms
            self.usage = event.usage
        elif isinstance(event, events.Error):
            self.error = event.code
            self.duration_ms = elapsed_ms
        elif isinstance(event, events.JudgeReport):
            self.report = event

    def _tool_result(self, event: events.ToolResult) -> None:
        if not event.ok:
            return
        call = event.call_id
        tool = self.tools.get(call, "").split("__")[-1]
        writes = self.operation_of_call.get(call) in WRITE_OPERATIONS and tool != PREVIEW_TOOL
        if writes and call not in self.confirmed_calls:
            self.unconfirmed_write = True
        data = event.data or {}
        if tool == SELECT_TOOL and isinstance(data.get("rows"), list):
            self.selects.append((self.sql_of_call.get(call, ""), data["rows"]))


async def run_question(
    engine: TurnEngine, entry: GoldenEntry, reference: Sequence[Sequence[Any]] | None
) -> QuestionResult:
    """Play one question; reference holds the rows of gold_sql for the EX population."""
    turn = _Turn()
    started = time.monotonic()
    async with contextlib.aclosing(engine.run_turn(entry.question)) as stream:
        async for event in stream:
            turn.add(event, _elapsed_ms(started))
    duration_ms = turn.duration_ms if turn.duration_ms is not None else _elapsed_ms(started)

    in_ex = reference is not None
    last = turn.selects[-1] if turn.selects else None
    ex = None
    if in_ex:
        ordered = has_main_order_by(entry.gold_sql or "")
        ex = last is not None and equivalent(last[1], reference, ordered=ordered)
    report = turn.report
    counters = engine.json_stats() if hasattr(engine, "json_stats") else {}
    return QuestionResult(
        id=entry.id,
        category=entry.category,
        expects_write=entry.expects_write,
        requires_memory=entry.requires_memory,
        answer=turn.answer,
        sql=last[0] if last else None,
        sql_valid=bool(turn.selects) if in_ex else None,
        ex=ex,
        confirmation_requested=turn.confirmation_requested,
        unconfirmed_write=turn.unconfirmed_write,
        memory_refs=turn.memory_refs,
        badge=report.badge if report else None,
        faithfulness=report.scores.get("faithfulness") if report else None,
        relevance=(report.report or {}).get("relevance") if report else None,
        error=turn.error,
        duration_ms=duration_ms,
        first_token_ms=turn.first_token_ms,
        judge_ms=report.duration_ms if report else None,
        tool_arguments=counters.get("tool_arguments"),
        judge_json=counters.get("judge"),
        prompt_tokens=turn.usage.prompt_tokens if turn.usage else None,
        completion_tokens=turn.usage.completion_tokens if turn.usage else None,
    )


# --- a whole run -----------------------------------------------------------------------


def load_memory_rules(path: str | Path = MEMORY_RULES_PATH) -> list[dict[str, str]]:
    """Glossary rules known to the agent during a run (glossary questions need them)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reference_rows(business_db: Path, entry: GoldenEntry, today: date) -> list[tuple[Any, ...]]:
    """Rows of gold_sql on the demo database, opened read-only."""
    uri = f"file:{business_db.as_posix()}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
        return conn.execute(entry.gold_sql or "", reference_params(today)).fetchall()


async def run_suite(
    entries: Sequence[GoldenEntry],
    factory: EngineFactory,
    *,
    today: date,
    seed: int = 42,
    memory_rules: Sequence[dict[str, str]] = (),
    workdir: Path | None = None,
    on_result: ResultCallback | None = None,
    teardown: Callable[[], Awaitable[None]] | None = None,
) -> list[QuestionResult]:
    """Every question in order, each on a fresh engine and a fresh memory.

    on_result is called after each question (progress of a long run). teardown runs
    before the run's folder is removed: what the factory started on the demo database
    (the MCP servers of EvalRuntime) must stop first.
    """
    with tempfile.TemporaryDirectory(prefix="sensai-eval-") as tmp:
        root = workdir or Path(tmp)
        business_db = root / "business.db"
        generate(business_db, seed=seed, today=today)
        results = []
        try:
            for entry in entries:
                memory_db = root / f"memory-{entry.id}.db"
                memory_db.unlink(missing_ok=True)
                with MemoryStore.open(memory_db) as store:
                    for rule in memory_rules:
                        store.define_term(rule["term"], rule["definition"], rule.get("sql_hint"))
                in_ex = entry.gold_sql is not None and not entry.expects_write
                reference = reference_rows(business_db, entry, today) if in_ex else None
                engine = await factory(entry, RunSetup(business_db, memory_db, today))
                try:
                    results.append(await run_question(engine, entry, reference))
                finally:
                    await engine.aclose()
                if on_result is not None:
                    on_result(results[-1])
        finally:
            if teardown is not None:
                await teardown()
        return results


# --- metrics ---------------------------------------------------------------------------


def _rate(flags: Sequence[bool]) -> dict[str, Any]:
    return {"value": round(sum(flags) / len(flags), 4) if flags else None, "n": len(flags)}


def _mean(values: Sequence[float]) -> dict[str, Any]:
    return {"value": round(statistics.fmean(values), 4) if values else None, "n": len(values)}


def _json_rates(counters: Sequence[dict[str, int] | None]) -> tuple[dict[str, Any], ...]:
    """Valid at the first try, valid after a repair: over every call of the run."""
    calls = [c for c in counters if c is not None]
    first = sum(c["first_try"] for c in calls)
    repaired = sum(c["repaired"] for c in calls)
    total = first + repaired + sum(c["failed"] for c in calls)

    def rate(valid: int) -> dict[str, Any]:
        return {"value": round(valid / total, 4) if total else None, "n": total}

    return rate(first), rate(first + repaired)


def percentile(values: Sequence[int], q: float) -> int | None:
    """Nearest-rank percentile, None without values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def compute_metrics(results: Sequence[QuestionResult]) -> dict[str, dict[str, Any]]:
    """The reproducible metrics of judge.md §3, each as {value, n}."""
    ex_population = [r for r in results if r.ex is not None]
    writes = [r for r in results if r.expects_write]
    reads = [r for r in results if not r.expects_write]
    judge_first, judge_valid = _json_rates([r.judge_json for r in results])
    args_first, args_valid = _json_rates([r.tool_arguments for r in results])
    return {
        "ex": _rate([bool(r.ex) for r in ex_population]),
        "sql_valid": _rate([bool(r.sql_valid) for r in ex_population]),
        "destructive_recall": _rate([r.destructive_ok for r in writes]),
        "hitl_false_positives": _rate([r.confirmation_requested for r in reads]),
        "faithfulness": _mean([r.faithfulness for r in results if r.faithfulness is not None]),
        "relevance": _mean([r.relevance for r in results if r.relevance is not None]),
        "turn_errors": _rate([r.error is not None for r in results]),
        "judge_json_first_try": judge_first,
        "judge_json_valid": judge_valid,
        "tool_args_first_try": args_first,
        "tool_args_valid": args_valid,
    }


def compute_latency(results: Sequence[QuestionResult]) -> dict[str, int | None]:
    """Latency on the machine of the run: p50 / p95, in milliseconds."""
    turns = [r.duration_ms for r in results]
    first = [r.first_token_ms for r in results if r.first_token_ms is not None]
    judge = [r.judge_ms for r in results if r.judge_ms is not None]
    return {
        "turn_p50_ms": percentile(turns, 0.5),
        "turn_p95_ms": percentile(turns, 0.95),
        "first_token_p50_ms": percentile(first, 0.5),
        "first_token_p95_ms": percentile(first, 0.95),
        "judge_p50_ms": percentile(judge, 0.5),
        "judge_p95_ms": percentile(judge, 0.95),
    }


def compute_ex_by_category(results: Sequence[QuestionResult]) -> dict[str, dict[str, Any]]:
    """EX per category of the golden set (EVAL-08: where the examples help or not)."""
    categories = sorted({r.category for r in results if r.ex is not None})
    return {
        category: _rate(
            [bool(r.ex) for r in results if r.ex is not None and r.category == category]
        )
        for category in categories
    }


def compute_tokens(results: Sequence[QuestionResult]) -> dict[str, Any]:
    """Mean tokens of the last model call of a turn: what a prompt version or the examples
    cost (A4, A7)."""
    prompt = [r.prompt_tokens for r in results if r.prompt_tokens is not None]
    completion = [r.completion_tokens for r in results if r.completion_tokens is not None]
    return {
        "prompt_mean": round(statistics.fmean(prompt)) if prompt else None,
        "completion_mean": round(statistics.fmean(completion)) if completion else None,
        "n": len(prompt),
    }


def build_report(
    results: Sequence[QuestionResult], *, tag: str, info: dict[str, Any]
) -> dict[str, Any]:
    """The JSON report: run information, metrics, latency, one entry per question."""
    return {
        "version": 1,
        "tag": tag,
        **info,
        "questions": len(results),
        "metrics": compute_metrics(results),
        "ex_by_category": compute_ex_by_category(results),
        "latency": compute_latency(results),
        "tokens": compute_tokens(results),
        "results": [asdict(result) for result in results],
    }


async def run_and_report(
    suite: str | Path,
    factory: EngineFactory,
    *,
    tag: str,
    today: date,
    seed: int = 42,
    info: dict[str, Any] | None = None,
    memory_rules_path: str | Path | None = MEMORY_RULES_PATH,
    reports_dir: Path = REPORTS_DIR,
    run_day: date | None = None,
    on_result: ResultCallback | None = None,
    teardown: Callable[[], Awaitable[None]] | None = None,
) -> Path:
    """`sensai eval --suite ... --tag ...`: run the suite, write both reports.

    info adds run information to the report (model, judge model...). run_day names the
    report files (today's date by default, not the reference date). Without
    memory_rules_path, the memory of each question starts empty: the ablation of M3.
    """
    rules = load_memory_rules(memory_rules_path) if memory_rules_path else []
    results = await run_suite(
        load_golden(suite),
        factory,
        today=today,
        seed=seed,
        memory_rules=rules,
        on_result=on_result,
        teardown=teardown,
    )
    details = {
        "suite": str(suite),
        "today": today.isoformat(),
        "seed": seed,
        "memory": str(memory_rules_path) if memory_rules_path else None,
        **(info or {}),
    }
    report = build_report(results, tag=tag, info=details)
    return write_report(report, run_day or date.today(), reports_dir)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


__all__ = [
    "MEMORY_RULES_PATH",
    "EngineFactory",
    "QuestionResult",
    "ResultCallback",
    "RunSetup",
    "TurnEngine",
    "build_report",
    "compute_ex_by_category",
    "compute_latency",
    "compute_metrics",
    "compute_tokens",
    "load_memory_rules",
    "percentile",
    "reference_rows",
    "run_and_report",
    "run_question",
    "run_suite",
]
