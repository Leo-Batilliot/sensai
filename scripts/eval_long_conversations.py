"""EVAL-10, reduced: what a long session keeps, truncation against summary (M2, ADR-008).

Each conversation of eval/long_conversations.jsonl plants facts in its first turns (a
figure given by the agent, a figure or a definition given by the user, a scope), asks
ordinary questions so that the history outgrows its budget, then asks control questions
checked by the code: the expected number must be in the answer. A fact that the agent
gave itself only counts as kept if the control turn did not query the database again.

The run isolates the history (pitfall of EVAL-10): no memory tools, so the memory context
stays empty and a fact can only survive through the history or its summary. The budget is
lowered ([context] budget_ratio) so that the history overflows within a dozen turns, while
num_ctx stays at 16384, which qwen3:8b needs to think.

    .venv\\Scripts\\python scripts\\eval_long_conversations.py
    .venv\\Scripts\\python scripts\\eval_long_conversations.py --only conv-1 --strategy compress

Report: eval/reports/<date>_long-conversations.json and .md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sensai.core import events
from sensai.core.config import Config, load_config
from sensai.data.seed import generate
from sensai.documents.store import DocumentStore
from sensai.eval.golden import REFERENCE_DATE, reference_params
from sensai.eval.numeric_check import extract_numbers, matches
from sensai.eval.runtime import EvalRuntime, FrozenClock, check_models
from sensai.memory.store import MemoryStore
from sensai.tools.base import Tool
from sensai.tools.document_tools import DELETE_SECTION, document_tools
from sensai.tools.registry import Registry

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval" / "long_conversations.jsonl"
REPORTS = ROOT / "eval" / "reports"
STRATEGIES = ("truncate", "compress")
READ_TOOL = "sql_toolbox__run_select"
INTERRUPTED = "STREAM_INTERRUPTED"  # the answer did not come within [ollama] timeout_s
TOLERANCE = 0.01  # same tolerance as the numeric check of the judge


class NoMemoryRuntime(EvalRuntime):
    """The runtime of the harness without the memory tools: nothing reaches the memory."""

    def _registry(
        self, store: MemoryStore, documents: DocumentStore, session_id: str | None, today: date
    ) -> Registry:
        tools: list[Tool] = [*document_tools(documents, session_id), FrozenClock(today)]
        return Registry(
            self.manager,
            tools,
            previewers={DELETE_SECTION: lambda args: documents.delete_impact(args["section_id"])},
        )


@dataclass
class Turn:
    index: int
    role: str
    fact: str | None
    question: str
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    badge: str | None = None
    prompt_tokens: int | None = None
    ctx_used: int | None = None
    seconds: float = 0.0
    compaction: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    # The history after the turn, as the Compactor will count it at the next one
    history_tokens: int | None = None
    scale: float | None = None
    # Control turns only
    expected: float | None = None
    found: bool | None = None
    requeried: bool | None = None
    verdict: str | None = None


def expected_value(check: dict[str, Any], business_db: Path) -> float:
    """The number a control answer must quote: given, or computed on the run's database."""
    if "value" in check:
        return float(check["value"])
    with sqlite3.connect(business_db) as conn:
        (value,) = conn.execute(check["sql"], reference_params(REFERENCE_DATE)).fetchone()
    return float(value)


def judge_control(turn: Turn, check: dict[str, Any], business_db: Path) -> None:
    turn.expected = expected_value(check, business_db)
    turn.found = any(
        matches(mention, [turn.expected], TOLERANCE) for mention in extract_numbers(turn.answer)
    )
    turn.requeried = READ_TOOL in turn.tools
    turn.verdict = verdict(turn.found, turn.requeried, check["from_context"], turn.errors)


def verdict(found: bool, requeried: bool, from_context: bool, errors: list[str]) -> str:
    """A turn cut by the timeout tells nothing about the history: counted apart."""
    if not found and INTERRUPTED in errors:
        return "interrompu"
    if not found:
        return "perdu"
    if from_context and requeried:
        return "retrouvé par une nouvelle requête"
    return "retrouvé"


async def run_conversation(
    runtime: EvalRuntime, conversation: dict[str, Any], work: Path, business_db: Path
) -> list[Turn]:
    wired = runtime.new_engine(work / f"{conversation['id']}-memory.db", REFERENCE_DATE)
    turns: list[Turn] = []
    try:
        for index, spec in enumerate(conversation["turns"], 1):
            turn = Turn(index, spec["role"], spec.get("fact"), spec["text"])
            started_at = time.monotonic()
            turn_started = 0.0
            async for event in wired.run_turn(spec["text"]):
                if isinstance(event, events.TurnStarted):
                    turn_started = event.ts
                elif isinstance(event, events.ContextCompacted):
                    turn.compaction = {
                        "turns": event.turns,
                        "tokens_before": event.tokens_before,
                        "tokens_after": event.tokens_after,
                        "seconds": round(event.ts - turn_started, 1),
                        "summary": event.summary,
                    }
                elif isinstance(event, events.ToolCall):
                    turn.tools.append(event.tool)
                elif isinstance(event, events.MessageDone):
                    turn.answer = event.content
                    if event.usage is not None:
                        turn.prompt_tokens = event.usage.prompt_tokens
                        turn.ctx_used = event.usage.ctx_used
                elif isinstance(event, events.JudgeReport):
                    turn.badge = event.badge
                elif isinstance(event, events.Error):
                    turn.errors.append(event.code)
            turn.seconds = round(time.monotonic() - started_at, 1)
            budget = wired.engine.budget
            turn.history_tokens = budget.estimate(wired.engine.history.messages)
            turn.scale = round(budget.scale, 2)
            summary = wired.engine.history.summary
            if turn.compaction and summary is not None:  # the event only gives a preview
                turn.compaction["summary_text"] = summary.text
            if spec["role"] == "control":
                judge_control(turn, spec["check"], business_db)
            turns.append(turn)
            print(_progress(conversation["id"], turn), flush=True)
    finally:
        await wired.aclose()
    return turns


def _progress(conversation_id: str, turn: Turn) -> str:
    parts = [
        f"  {conversation_id} tour {turn.index:>2} ({turn.role}) {turn.seconds:>5.1f} s",
        f"historique ≈ {turn.history_tokens} tokens, échelle {turn.scale}, "
        f"dernier appel {turn.ctx_used}",
    ]
    if turn.compaction:
        c = turn.compaction
        parts.append(f"compaction {c['turns']} échanges {c['tokens_before']}→{c['tokens_after']}")
    if turn.verdict:
        parts.append(f"{turn.fact} : {turn.verdict}")
    if turn.errors:
        parts.append(f"erreurs {turn.errors}")
    return " | ".join(parts)


def summarize(turns: list[Turn]) -> dict[str, Any]:
    controls = [turn for turn in turns if turn.role == "control"]
    compactions = [turn.compaction for turn in turns if turn.compaction]
    durations = [turn.seconds for turn in turns]
    prompts = [turn.prompt_tokens for turn in turns if turn.prompt_tokens is not None]
    return {
        "facts": len(controls),
        "kept": sum(turn.verdict == "retrouvé" for turn in controls),
        "kept_by_requery": sum(
            turn.verdict == "retrouvé par une nouvelle requête" for turn in controls
        ),
        "lost": sum(turn.verdict == "perdu" for turn in controls),
        "interrupted": sum(turn.verdict == "interrompu" for turn in controls),
        "compactions": len(compactions),
        "compaction_seconds_mean": _mean([c["seconds"] for c in compactions]),
        "exchanges_taken_out": sum(c["turns"] for c in compactions),
        "prompt_tokens_mean": _mean(prompts),
        "ctx_used_max": max((turn.ctx_used or 0 for turn in turns), default=0),
        "turn_seconds_p50": round(statistics.median(durations), 1) if durations else None,
        "turns": len(turns),
        "errors": sum(len(turn.errors) for turn in turns),
    }


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None


def markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    lines = [
        f"# Conversations longues : troncature contre résumé ({report['date']})",
        "",
        f"- Jeu : `{run['suite']}` ({run['conversations']} conversations, {run['facts']} faits plantés)",
        f"- Modèle : {run['model']}, juge : {run['judge_model']}, `num_ctx` {run['num_ctx']}, "
        f"prompt {run['prompt_version']}, graine {run['seed']}, date de référence {run['today']}",
        f"- Budget : `budget_ratio` {run['budget_ratio']} (limite estimée {run['limit']} tokens), "
        f"`keep_recent_turns` {run['keep_recent_turns']}, `summary_max_tokens` {run['summary_max_tokens']}",
        "- Mémoire : outils mémoire retirés, aucun contexte mémoire injecté (seul l'historique "
        "ou son résumé peut porter un fait)",
        "",
        "## Résultats",
        "",
        "| Stratégie | Faits retrouvés | Par nouvelle requête | Perdus | Interrompus (délai) | "
        "Compactions | Durée moyenne d'une compaction | Tokens du prompt (moyenne) | Tour p50 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, data in report["strategies"].items():
        s = data["summary"]
        lines.append(
            f"| `{strategy}` | {s['kept']} / {s['facts']} | {s['kept_by_requery']} | {s['lost']} | "
            f"{s['interrupted']} | "
            f"{s['compactions']} | {_seconds(s['compaction_seconds_mean'])} | "
            f"{s['prompt_tokens_mean']} | {_seconds(s['turn_seconds_p50'])} |"
        )
    lines += [
        "",
        "## Faits",
        "",
        "| Stratégie | Conversation | Fait | Attendu | Verdict | Extrait de la réponse |",
    ]
    lines.append("|---|---|---|---:|---|---|")
    for strategy, data in report["strategies"].items():
        for conversation in data["conversations"]:
            for turn in conversation["turns"]:
                if turn["role"] != "control":
                    continue
                excerpt = " ".join(turn["answer"].split())[:140].replace("|", "\\|")
                lines.append(
                    f"| `{strategy}` | {conversation['id']} | {turn['fact']} | "
                    f"{turn['expected']:g} | {turn['verdict']} | {excerpt} |"
                )
    lines += [
        "",
        "## Compactions",
        "",
        "| Stratégie | Conversation | Tour | Échanges retirés | Tokens avant → après | Durée | Début du résumé |",
    ]
    lines.append("|---|---|---:|---:|---|---:|---|")
    for strategy, data in report["strategies"].items():
        for conversation in data["conversations"]:
            for turn in conversation["turns"]:
                c = turn["compaction"]
                if not c:
                    continue
                summary = " ".join(c["summary"].split())[:120].replace("|", "\\|")
                lines.append(
                    f"| `{strategy}` | {conversation['id']} | {turn['index']} | {c['turns']} | "
                    f"{c['tokens_before']} → {c['tokens_after']} | {_seconds(c['seconds'])} | {summary} |"
                )
    return "\n".join(lines) + "\n"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} s".replace(".", ",")


def write_report(report: dict[str, Any], stem: Path) -> None:
    stem.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(markdown(report), encoding="utf-8")


def rerender(path: Path, conversations: list[dict[str, Any]]) -> int:
    """Verdicts, summaries and Markdown of a JSON report again, after a change of rules."""
    report = json.loads(path.read_text(encoding="utf-8"))
    specs = {c["id"]: c["turns"] for c in conversations}
    for data in report["strategies"].values():
        for conversation in data["conversations"]:
            for turn in conversation["turns"]:
                if turn["role"] == "control":
                    check = specs[conversation["id"]][turn["index"] - 1]["check"]
                    turn["verdict"] = verdict(
                        turn["found"], turn["requeried"], check["from_context"], turn["errors"]
                    )
        turns = [Turn(**t) for c in data["conversations"] for t in c["turns"]]
        data["summary"] = summarize(turns)
    write_report(report, path.with_suffix(""))
    print(f"Rapports : {path.with_suffix('')}.json et .md")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")
    parser.add_argument("--strategy", choices=STRATEGIES, action="append")
    parser.add_argument("--only", action="append", help="ne jouer que cette conversation")
    parser.add_argument("--budget-ratio", type=float, default=0.29)
    parser.add_argument("--keep-recent", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--render", help="refait le Markdown d'un rapport JSON, sans rien rejouer")
    args = parser.parse_args()

    conversations = [
        json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines() if line
    ]
    if args.render:
        return rerender(Path(args.render), conversations)
    if args.only:
        conversations = [c for c in conversations if c["id"] in args.only]
    work = Path(tempfile.mkdtemp(prefix="sensai-eval10-"))
    business_db = work / "business.db"
    generate(business_db, seed=args.seed, today=REFERENCE_DATE)

    report: dict[str, Any] = {"date": date.today().isoformat(), "strategies": {}}
    for strategy in args.strategy or STRATEGIES:
        config: Config = load_config(
            args.config,
            overrides={
                "context.strategy": strategy,
                "context.budget_ratio": args.budget_ratio,
                "context.keep_recent_turns": args.keep_recent,
            },
        )
        problem = await check_models(config)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        report["run"] = {
            "suite": SUITE.relative_to(ROOT).as_posix(),
            "conversations": len(conversations),
            "facts": sum(t["role"] == "control" for c in conversations for t in c["turns"]),
            "model": config.ollama.model,
            "judge_model": config.ollama.effective_judge_model,
            "num_ctx": config.ollama.num_ctx,
            "prompt_version": config.agent.prompt_version,
            "seed": args.seed,
            "today": REFERENCE_DATE.isoformat(),
            "budget_ratio": args.budget_ratio,
            "limit": int(args.budget_ratio * config.ollama.num_ctx),
            "keep_recent_turns": args.keep_recent,
            "summary_max_tokens": config.context.summary_max_tokens,
        }
        print(f"Stratégie {strategy}", flush=True)
        (work / strategy).mkdir()
        played = []
        async with NoMemoryRuntime(config, seed=args.seed) as runtime:
            await runtime.start(business_db)
            for conversation in conversations:
                turns = await run_conversation(runtime, conversation, work / strategy, business_db)
                played.append({"id": conversation["id"], "turns": [asdict(t) for t in turns]})
        all_turns = [Turn(**t) for c in played for t in c["turns"]]
        report["strategies"][strategy] = {"summary": summarize(all_turns), "conversations": played}
        print(f"  {report['strategies'][strategy]['summary']}", flush=True)

    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / f"{report['date']}_long-conversations"
    write_report(report, stem)
    print(f"Rapports : {stem}.json et {stem}.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
