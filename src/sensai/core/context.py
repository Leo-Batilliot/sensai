"""Token budget and rolling summary (M2, CORE-13): a long session never overflows the context.

Beyond num_ctx, Ollama cuts the prompt silently, from the start: the system prompt, with
the schema and the rules, goes first. So the Engine checks the size itself, before the
first model call of each turn (Compactor.fit). Over [context] budget_ratio × num_ctx, the
oldest exchanges (all but the keep_recent_turns last ones) leave the history:

- "compress": a rolling summary replaces them. The model writes it as structured output
  (T5), from those exchanges and the previous summary; the code renders it as text, right
  after the system prompt. The figures that the judge checked in those exchanges are put
  back by the code if the model left them out: a verified figure is never lost.
- "truncate": they are dropped. It is the witness of the measure (EVAL-10).

A summary that fails (Ollama error, JSON still invalid after the repair) falls back to
truncation for this turn: logged, never an error for the user.

The size is an estimate (ContextBudget): characters / 3.5 for French, rescaled after each
answer by the ratio between Ollama's real prompt_eval_count and the estimate of the same
prompt.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sensai.core import prompts
from sensai.core.config import Config
from sensai.core.history import Exchange, History, Summary
from sensai.llm.ollama_client import Message
from sensai.llm.structured import StructuredClient, generate_structured

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3.5  # French text, SQL and JSON included
MESSAGE_TOKENS = 4  # role and separators of each message in the chat template
# Bounds of the rescaling: a low count may come from Ollama's prompt cache, and an
# underestimate is the dangerous side (Ollama would cut the prompt)
MIN_SCALE, MAX_SCALE = 0.75, 2.0
SUMMARY_PROMPT = "context_summary.md"
SUMMARY_HEADING = "# Résumé des échanges précédents"
MAX_ANSWER_CHARS = 2000  # of each summarized answer: the summary call must fit num_ctx too
MAX_VERIFIED = 15  # verified figures carried from summary to summary
PREVIEW_CHARS = 200  # context_compacted.summary (events.md §3.13)

_TEXTS = {"type": "array", "maxItems": 10, "items": {"type": "string", "minLength": 1}}
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["definitions", "figures", "decisions", "open_questions", "notes"],
    "additionalProperties": False,
    "properties": {
        "definitions": _TEXTS,
        "figures": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["label", "value", "unit", "period"],
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "minLength": 1},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "period": {"type": "string"},
                },
            },
        },
        "decisions": _TEXTS,
        "open_questions": _TEXTS,
        "notes": {"type": "string"},
    },
}


class ContextBudget:
    """Estimated size of a prompt in tokens, rescaled on the real counts of Ollama."""

    def __init__(self) -> None:
        self.scale = 1.0  # real tokens / estimated tokens, from the last answer

    @staticmethod
    def limit(config: Config) -> int:
        """Tokens left to the prompt; read at each turn, set_config may change num_ctx."""
        return int(config.context.budget_ratio * config.ollama.num_ctx)

    @staticmethod
    def raw(messages: Sequence[Message], tools: list[dict[str, Any]] | None = None) -> int:
        """Estimate before rescaling: the tools count too, Ollama puts them in the prompt."""
        chars = sum(len(str(message.get("content") or "")) for message in messages)
        if tools:
            chars += len(json.dumps(tools, ensure_ascii=False))
        return math.ceil(chars / CHARS_PER_TOKEN) + MESSAGE_TOKENS * len(messages)

    def estimate(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None = None
    ) -> int:
        return math.ceil(self.raw(messages, tools) * self.scale)

    def calibrate(self, raw: int, real: int) -> None:
        """Rescale on the prompt_eval_count of a prompt whose raw estimate was `raw`."""
        if raw <= 0 or real <= 0:
            return
        self.scale = min(max(real / raw, MIN_SCALE), MAX_SCALE)

    def reset(self) -> None:
        """Another model, another tokenizer: back to the plain estimate."""
        self.scale = 1.0


@dataclass(frozen=True)
class Compaction:
    """What fit() did, for the context_compacted event (events.md §3.13)."""

    turns: int  # exchanges taken out of the history
    tokens_before: int
    tokens_after: int
    summary: str  # start of the summary, for the timeline


class Compactor:
    """Keeps the prompt of each turn under the budget (see the module docstring)."""

    def __init__(self, client: StructuredClient, budget: ContextBudget) -> None:
        self.client = client
        self.budget = budget

    async def fit(
        self,
        history: History,
        head: list[Message],
        question: str,
        tools: list[dict[str, Any]] | None,
        *,
        model: str,
        config: Config,
    ) -> Compaction | None:
        """Make room in `history` for this turn; None when it already fits.

        `head` is what is always kept: the system prompt with the memory and the profile.
        Never raises: a failed summary falls back to truncation.
        """
        limit = self.budget.limit(config)
        question_message: Message = {"role": "user", "content": question}

        def size(messages: list[Message]) -> int:
            return self.budget.estimate([*head, *messages, question_message], tools)

        before = size(history.messages)
        if before <= limit:
            return None
        oldest = history.oldest(config.context.keep_recent_turns)
        summary: Summary | None = None
        why = "Troncature"  # what the timeline says when no summary replaces the exchanges
        if not oldest:
            why = "Échanges récents trop longs"
        elif config.context.strategy == "compress":
            why = "Résumé impossible"
            summary = await self._summarize(history.summary, oldest, model, config)
            current = history.exchanges[: len(oldest)]
            same = len(current) == len(oldest) and all(
                a is b for a, b in zip(current, oldest, strict=True)
            )
            if not same:
                logger.info("History reset during the summary: nothing to replace")
                return None
            if summary is not None and size(history.messages_without(len(oldest), summary)) >= (
                before
            ):
                logger.warning("Summary longer than what it replaces: truncation instead")
                summary = None
        removed = len(oldest)
        history.drop(removed, summary)
        after = size(history.messages)
        # The recent exchanges alone are still too long: they go too, oldest first
        while after > limit and history.exchanges:
            history.drop(1)
            removed += 1
            after = size(history.messages)
        if removed == 0:
            logger.warning(
                "Prompt over budget (%d > %d tokens), nothing to take out", before, limit
            )
            return None
        if after > limit:
            logger.warning("Prompt still over budget after compaction (%d > %d)", after, limit)
        return Compaction(removed, before, after, _preview(summary, removed, why))

    async def _summarize(
        self, previous: Summary | None, exchanges: list[Exchange], model: str, config: Config
    ) -> Summary | None:
        """The rolling summary of `exchanges` and `previous`, or None if it failed."""
        messages: list[Message] = [
            {"role": "system", "content": prompts.load(SUMMARY_PROMPT)},
            {"role": "user", "content": _summary_request(previous, exchanges)},
        ]
        try:
            result = await generate_structured(
                self.client,
                messages,
                SUMMARY_SCHEMA,
                model,
                options={"num_ctx": config.ollama.num_ctx},
            )
        except Exception:  # Ollama down or too slow: the turn goes on without a summary
            logger.exception("Summary of the history failed: truncation")
            return None
        if not result.ok or result.data is None:
            logger.warning("Summary still invalid after repair (%s): truncation", result.errors)
            return None
        verified = [
            *(previous.verified if previous else ()),
            *(figure for exchange in exchanges for figure in exchange.key_figures),
        ][-MAX_VERIFIED:]
        data = keep_figures(result.data, verified, config.eval.numeric_tolerance)
        return Summary(render(data, config.context.summary_max_tokens), data, tuple(verified))


def keep_figures(
    data: dict[str, Any], verified: Sequence[dict[str, Any]], tolerance: float
) -> dict[str, Any]:
    """The summary, plus each verified figure whose value it does not give, as it was."""
    figures = list(data["figures"])
    for figure in verified:
        value = figure.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if any(_same(value, given["value"], tolerance) for given in figures):
            continue
        logger.info("Verified figure missing from the summary, added: %s", figure.get("label"))
        figures.append(
            {"label": figure["label"], "value": value, "unit": figure.get("unit", ""), "period": ""}
        )
    return {**data, "figures": figures}


def render(data: dict[str, Any], max_tokens: int) -> str:
    """The summary as the model reads it: figures first, at most max_tokens."""
    figures = [
        f"{figure['label']} : {_number(figure['value'])}"
        + (f" {figure['unit']}" if figure["unit"] else "")
        + (f" ({figure['period']})" if figure["period"] else "")
        for figure in data["figures"]
    ]
    parts = [
        ("Chiffres", figures),
        ("Définitions", data["definitions"]),
        ("Choix de l'utilisateur", data["decisions"]),
        ("Questions en suspens", data["open_questions"]),
        ("Notes", [data["notes"]] if data["notes"] else []),
    ]
    lines = [SUMMARY_HEADING, *(f"{name} : {' ; '.join(items)}" for name, items in parts if items)]
    text = "\n".join(lines)
    limit = int(max_tokens * CHARS_PER_TOKEN)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary_request(previous: Summary | None, exchanges: list[Exchange]) -> str:
    lines = []
    if previous is not None:
        lines += ["Résumé précédent :", json.dumps(previous.data, ensure_ascii=False), ""]
    lines.append("Échanges à résumer :")
    for exchange in exchanges:
        answer = exchange.answer
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS] + " […]"
        lines += [f"Question : {exchange.question}", f"Réponse : {answer}", ""]
    return "\n".join(lines).rstrip()


def _preview(summary: Summary | None, turns: int, why: str) -> str:
    """context_compacted.summary: the start of the summary, or why there is none."""
    if summary is not None:
        text = summary.text.removeprefix(SUMMARY_HEADING).strip().replace("\n", " · ")
    else:
        text = f"{why} : {turns} échange(s) retiré(s) sans résumé."
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


def _same(a: float, b: Any, tolerance: float) -> bool:
    if not isinstance(b, (int, float)) or isinstance(b, bool):
        return False
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=1e-9)


def _number(value: float) -> str:
    """48712.4 -> '48712.4', 18400.0 -> '18400': no exponent, no useless decimals."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
