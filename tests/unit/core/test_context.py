"""Token budget and rolling summary (M2, CORE-13): long sessions on a scripted Ollama.

num_ctx is 2048 (the minimum of the configuration), so the prompt budget is 1536 tokens:
about 7 exchanges of the size used here. The answers report prompt_tokens=0, which leaves
the estimate unscaled: the tests can then compute sizes with ContextBudget.raw().
"""

import contextlib
import json
from typing import Any

import pytest

from contract_rules import check_turns
from event_checks import check_turn
from fakes import MODEL, FakeClient, FakeToolRegistry, answer, select_tool, tool_request
from sensai.core import events
from sensai.core.config import Config, ContextConfig, OllamaConfig
from sensai.core.context import SUMMARY_HEADING, Compactor, ContextBudget, keep_figures, render
from sensai.core.engine import Engine
from sensai.core.errors import OllamaUnavailable
from sensai.core.history import History
from sensai.eval.judge import Judge
from sensai.eval.online import OnlineJudge
from sensai.llm.ollama_client import Chunk, Message

SYSTEM = "Tu es l'assistant."
FILLER = "Détail des ventes par mois et par famille de produits, hors annulations. " * 8
SUMMARY: dict[str, Any] = {
    "definitions": ["Client rentable = marge brute > 25 %"],
    "figures": [{"label": "CA du T3", "value": 1250000, "unit": "€", "period": "T3 2026"}],
    "decisions": ["Montants en k€"],
    "open_questions": [],
    "notes": "Préparation du comité.",
}
FIGURES = [
    {
        "label": "Marge Brasserie Lemaire",
        "value": 48712.4,
        "unit": "€",
        "source_column": "marge_eur",
    }
]


def config(**context: Any) -> Config:
    return Config(ollama=OllamaConfig(num_ctx=2048), context=ContextConfig(**context))


def question(i: int) -> str:
    return f"Quel est le chiffre d'affaires du client numéro {i} sur le trimestre ?"


def reply(i: int, text: str = FILLER) -> list[Any]:
    return answer(f"Le client {i} a réalisé {1000 + i} k€. {text}", prompt_tokens=0)


def engine(*replies: list[Any], summaries: Any = (), **context: Any) -> tuple[Engine, FakeClient]:
    client = FakeClient(*replies, summaries=tuple(summaries))
    built = Engine(config(**context), client, system_prompt=SYSTEM, session_id="s-1")
    return built, client


async def run(engine: Engine, message: str) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        emitted = [event async for event in turn]
    check_turn(emitted)
    check_turns([json.loads(json.dumps(event.to_dict())) for event in emitted])
    return emitted


async def session(engine: Engine, turns: int) -> list[list[events.Event]]:
    return [await run(engine, question(i)) for i in range(turns)]


def compactions(turns: list[list[events.Event]]) -> list[events.ContextCompacted]:
    return [event for turn in turns for event in turn if isinstance(event, events.ContextCompacted)]


def filled(count: int) -> History:
    history = History()
    for i in range(count):
        history.commit(question(i), f"Le client {i} a réalisé {1000 + i} k€. {FILLER}", f"t-{i}")
    return history


# --- a long session ----------------------------------------------------------------------


async def test_thirty_turns_stay_under_budget() -> None:
    built, client = engine(*(reply(i) for i in range(30)), summaries=[SUMMARY] * 30)
    limit = ContextBudget.limit(built.config)

    turns = await session(built, 30)

    assert len(compactions(turns)) >= 4  # a rolling summary, again and again
    for _model, messages, _options in client.calls:
        assert ContextBudget.raw(messages) <= limit
        assert messages[0] == {"role": "system", "content": SYSTEM}  # never cut
    last = client.calls[-1][1]
    assert last[1]["role"] == "system" and last[1]["content"].startswith(SUMMARY_HEADING)
    assert "Client rentable = marge brute > 25 %" in last[1]["content"]
    # The next summary starts from the previous one
    assert "Résumé précédent :" in client.chat_calls[1][1]["content"]


async def test_compaction_is_reported_once_before_the_model_answers() -> None:
    built, _client = engine(*(reply(i) for i in range(9)), summaries=[SUMMARY])

    turns = await session(built, 9)

    (compaction,) = compactions(turns)
    compacted_turn = next(turn for turn in turns if compaction in turn)
    assert [event.type for event in compacted_turn[:3]] == [
        "turn_started",
        "context_compacted",
        "token",
    ]
    assert compaction.turns == 4  # all but keep_recent_turns
    assert compaction.tokens_after < compaction.tokens_before
    assert compaction.summary.startswith("Chiffres : CA du T3 : 1250000 € (T3 2026)")
    assert len(built.history.exchanges) == 5  # the 4 kept, then this turn


async def test_under_budget_nothing_changes() -> None:
    built, client = engine(*(reply(i) for i in range(3)))

    turns = await session(built, 3)

    assert compactions(turns) == [] and client.chat_calls == []
    assert built.history.summary is None and len(built.history.exchanges) == 3


async def test_reset_empties_the_summary() -> None:
    built, client = engine(*(reply(i) for i in range(10)), summaries=[SUMMARY])
    await session(built, 9)
    assert built.history.summary is not None

    built.reset()
    await run(built, "Nouvelle question")

    assert built.history.summary is None
    assert client.calls[-1][1] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Nouvelle question"},
    ]


# --- verified figures ----------------------------------------------------------------------


async def test_key_figures_survive_compression() -> None:
    history = filled(9)
    history.add_key_figures("t-0", FIGURES)
    forgetful = {**SUMMARY, "figures": []}  # the model leaves the verified figure out
    compactor = Compactor(FakeClient(summaries=(forgetful,)), ContextBudget())

    done = await compactor.fit(
        history,
        [{"role": "system", "content": SYSTEM}],
        "Et au T4 ?",
        None,
        model=MODEL,
        config=config(keep_recent_turns=2),
    )

    assert done is not None and done.turns == 7
    assert history.summary is not None
    assert "Marge Brasserie Lemaire : 48712.4 €" in history.summary.text
    assert history.summary.verified == tuple(FIGURES)


async def test_a_verified_figure_survives_the_next_summaries() -> None:
    history = filled(9)
    history.add_key_figures("t-0", FIGURES)
    forgetful = {**SUMMARY, "figures": []}
    client = FakeClient(summaries=(forgetful, forgetful))
    compactor = Compactor(client, ContextBudget())
    head: list[Message] = [{"role": "system", "content": SYSTEM}]
    settings = config(keep_recent_turns=2)

    await compactor.fit(history, head, "Suite", None, model=MODEL, config=settings)
    for i in range(9, 16):
        history.commit(question(i), f"{1000 + i} k€. {FILLER}", f"t-{i}")
    await compactor.fit(history, head, "Suite", None, model=MODEL, config=settings)

    assert len(client.chat_calls) == 2
    assert history.summary is not None
    assert "Marge Brasserie Lemaire : 48712.4 €" in history.summary.text


class JudgeChat:
    """The judge model: a report whose key figures are FIGURES."""

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        format: dict[str, Any] | str | None = None,
        options: dict[str, Any] | None = None,
    ) -> Chunk:
        report = {
            "claims": [],
            "relevance": 5,
            "sql_answers_question": True,
            "key_figures": FIGURES,
            "assumptions": [],
            "followups": [],
        }
        return Chunk(content=json.dumps(report), done=True)


async def test_the_judge_figures_are_kept_with_their_exchange() -> None:
    sql = "SELECT name, marge_eur FROM clients ORDER BY marge_eur DESC LIMIT 1"
    built = Engine(
        Config(),
        FakeClient(tool_request("sql_toolbox__run_select", sql=sql), answer("48 712,40 €.")),
        registry=FakeToolRegistry(select_tool()),
        judge=OnlineJudge(Judge(JudgeChat(), MODEL)),
        session_id="s-1",
    )

    async with contextlib.aclosing(built.run_turn("Meilleure marge ?")) as turn:
        emitted = [event async for event in turn]

    assert emitted[-1].type == "judge_report"  # the figures come after the answer
    (exchange,) = built.history.exchanges
    assert exchange.turn_id == emitted[0].turn_id
    assert exchange.key_figures == FIGURES


def test_keep_figures_adds_only_what_is_missing() -> None:
    data = {**SUMMARY, "figures": [{"label": "Marge", "value": 48712.0, "unit": "€", "period": ""}]}
    other = {"label": "Délai moyen", "value": 42, "unit": "jours", "source_column": None}

    kept = keep_figures(data, [*FIGURES, other], tolerance=0.01)

    # 48712.0 is within 1 % of 48712.4: already there; the delay is added as it was
    assert [figure["label"] for figure in kept["figures"]] == ["Marge", "Délai moyen"]
    assert data["figures"] != kept["figures"]  # the model's answer is not changed in place


# --- when the summary fails ----------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [("pas du JSON", "toujours pas"), (OllamaUnavailable("http://localhost:11434"),)],
    ids=["invalid-after-repair", "ollama-down"],
)
async def test_failed_summary_falls_back_to_truncation(failure: tuple[Any, ...]) -> None:
    built, _client = engine(*(reply(i) for i in range(9)), summaries=failure)

    turns = await session(built, 9)

    (compaction,) = compactions(turns)
    assert compaction.summary == "Résumé impossible : 4 échange(s) retiré(s) sans résumé."
    assert all(turn[-1].type == "message_done" for turn in turns)  # no error for the user
    assert built.history.summary is None


async def test_truncate_strategy_drops_without_summary() -> None:
    built, client = engine(*(reply(i) for i in range(9)), strategy="truncate")

    turns = await session(built, 9)

    (compaction,) = compactions(turns)
    assert compaction.summary == "Troncature : 4 échange(s) retiré(s) sans résumé."
    assert client.chat_calls == []  # no model call
    assert built.history.summary is None
    assert built.history.exchanges[0].question == question(4)


async def test_recent_exchanges_too_long_are_dropped_too() -> None:
    long_reply = FILLER * 7  # ~4 000 characters: two of them exceed the budget
    built, client = engine(*(reply(i, long_reply) for i in range(3)), summaries=[SUMMARY])

    turns = await session(built, 3)

    (compaction,) = compactions(turns)
    assert compaction.summary.startswith("Échanges récents trop longs : 1 échange(s)")
    assert client.chat_calls == []  # nothing old enough to summarize (keep_recent_turns)
    for _model, messages, _options in client.calls:
        assert ContextBudget.raw(messages) <= ContextBudget.limit(built.config)


async def test_a_reset_during_the_summary_installs_nothing() -> None:
    history = filled(9)

    class ResettingClient:
        async def chat(self, model: str, messages: list[Message], **_: Any) -> Chunk:
            history.clear()  # /reset while the summary is being written
            return Chunk(content=json.dumps(SUMMARY), done=True)

    compactor = Compactor(ResettingClient(), ContextBudget())
    head: list[Message] = [{"role": "system", "content": SYSTEM}]

    assert await compactor.fit(history, head, "Suite", None, model=MODEL, config=config()) is None
    assert history.summary is None and history.messages == []


# --- estimate ---------------------------------------------------------------------------


def test_the_estimate_counts_text_messages_and_tools() -> None:
    messages: list[Message] = [{"role": "user", "content": "x" * 35}]
    assert ContextBudget.raw(messages) == 10 + 4  # 35 / 3.5, plus the message itself
    tools = [{"type": "function", "function": {"name": "outil"}}]
    assert ContextBudget.raw(messages, tools) > ContextBudget.raw(messages)


def test_calibration_follows_ollama_within_bounds() -> None:
    budget = ContextBudget()
    budget.calibrate(1000, 1300)
    assert budget.scale == 1.3
    assert budget.estimate([{"role": "user", "content": "x" * 350}]) == 136  # (100 + 4) × 1.3
    budget.calibrate(1000, 100)  # a cached prompt: never trusted below 0.75
    assert budget.scale == 0.75
    budget.calibrate(1000, 0)  # no count: nothing learned
    assert budget.scale == 0.75
    budget.reset()
    assert budget.scale == 1.0


async def test_the_engine_rescales_on_the_real_count_and_resets_with_the_model() -> None:
    sent = ContextBudget.raw(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Bonjour"}]
    )
    built, _client = engine(answer("Bonjour.", prompt_tokens=sent * 3 // 2))

    await run(built, "Bonjour")
    assert built.budget.scale == pytest.approx(1.5, abs=0.1)

    built.set_model("autre:8b")
    assert built.budget.scale == 1.0


def test_render_puts_the_figures_first_and_respects_its_budget() -> None:
    text = render(SUMMARY, max_tokens=500)
    assert text.splitlines() == [
        SUMMARY_HEADING,
        "Chiffres : CA du T3 : 1250000 € (T3 2026)",
        "Définitions : Client rentable = marge brute > 25 %",
        "Choix de l'utilisateur : Montants en k€",
        "Notes : Préparation du comité.",
    ]
    short = render({**SUMMARY, "notes": "x" * 2000}, max_tokens=50)
    assert len(short) == 175 and short.endswith("…")  # 50 tokens × 3.5 characters
