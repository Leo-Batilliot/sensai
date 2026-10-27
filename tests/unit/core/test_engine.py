"""Engine tests with a scripted fake Ollama client (no network)."""

import asyncio
import contextlib
import logging
from typing import Any

import pytest

from event_checks import check_turn
from fakes import HANG, MODEL, OTHER_MODEL, FakeClient, StreamItem, answer
from sensai.core import events
from sensai.core.config import Config, HitlConfig, OllamaConfig
from sensai.core.confirm import AutoRejectBroker, Decision, PendingAction
from sensai.core.engine import Engine
from sensai.core.errors import ModelNoTools, OllamaUnavailable, StreamInterrupted
from sensai.llm.ollama_client import Chunk, OllamaClient


def make_engine(*replies: list[StreamItem], config: Config | None = None) -> Engine:
    return Engine(config or Config(), FakeClient(*replies), session_id="s-1")


def fake(engine: Engine) -> FakeClient:
    assert isinstance(engine.client, FakeClient)
    return engine.client


async def run(engine: Engine, message: str) -> list[events.Event]:
    async with contextlib.aclosing(engine.run_turn(message)) as turn:
        return [event async for event in turn]


def types(emitted: list[events.Event]) -> list[str]:
    return [event.type for event in emitted]


# --- Nominal turn ---


async def test_nominal_turn() -> None:
    engine = make_engine(answer("Bonjour", " !", prompt_tokens=12, completion_tokens=3))

    emitted = await run(engine, "Salut")

    check_turn(emitted)
    assert types(emitted) == ["turn_started", "token", "token", "message_done"]
    started, done = emitted[0], emitted[-1]
    assert isinstance(started, events.TurnStarted) and isinstance(done, events.MessageDone)
    assert (started.origin, started.user_message) == ("user", "Salut")
    assert done.content == "Bonjour !"
    assert done.interrupted is False
    assert done.usage == events.ContextUsage(
        prompt_tokens=12, completion_tokens=3, ctx_used=15, ctx_max=8192
    )
    assert all(event.session_id == "s-1" for event in emitted)


async def test_history_is_sent_with_the_next_turn() -> None:
    engine = make_engine(answer("Un analyste."), answer("En bref."))

    await run(engine, "Quel est le rôle d'une contrôleuse de gestion ?")
    await run(engine, "Et en une phrase ?")

    _model, messages, _options = fake(engine).calls[1]
    assert messages == [
        {"role": "user", "content": "Quel est le rôle d'une contrôleuse de gestion ?"},
        {"role": "assistant", "content": "Un analyste."},
        {"role": "user", "content": "Et en une phrase ?"},
    ]


async def test_thinking_is_emitted_but_never_recorded() -> None:
    engine = make_engine([Chunk(thinking="17 fois 3"), *answer("51.")])

    emitted = await run(engine, "17 × 3 ?")

    check_turn(emitted)
    assert types(emitted) == ["turn_started", "thinking", "token", "message_done"]
    assert engine.history.messages[-1] == {"role": "assistant", "content": "51."}


async def test_chat_options_come_from_the_config() -> None:
    config = Config(ollama=OllamaConfig(num_ctx=4096))
    engine = make_engine(answer("Oui."), config=config)

    await run(engine, "Question")

    assert fake(engine).calls[0][2] == {"temperature": 0.1, "num_ctx": 4096}


# --- Errors ---


async def test_ollama_down_ends_the_turn_with_a_recoverable_error() -> None:
    engine = make_engine([OllamaUnavailable("http://localhost:11434")])

    emitted = await run(engine, "Salut")

    check_turn(emitted)
    assert types(emitted) == ["turn_started", "error"]
    error = emitted[-1]
    assert isinstance(error, events.Error)
    assert (error.code, error.recoverable) == ("OLLAMA_UNAVAILABLE", True)
    assert error.turn_id == emitted[0].turn_id
    assert len(engine.history) == 0


async def test_ollama_down_mid_session_keeps_the_history() -> None:
    engine = make_engine(
        answer("42 jours."), [OllamaUnavailable("http://localhost:11434")], answer("Oui.")
    )
    await run(engine, "Délai moyen ?")

    failed = await run(engine, "Et sur le T3 ?")
    assert types(failed) == ["turn_started", "error"]
    assert failed[-1].recoverable is True  # type: ignore[attr-defined]

    await run(engine, "Et sur le T3 ?")  # Ollama is back: the session goes on
    _model, messages, _options = fake(engine).calls[2]
    assert messages == [
        {"role": "user", "content": "Délai moyen ?"},
        {"role": "assistant", "content": "42 jours."},
        {"role": "user", "content": "Et sur le T3 ?"},
    ]


async def test_a_model_without_tools_gets_a_clear_message() -> None:
    engine = make_engine([ModelNoTools("gemma:2b")])

    emitted = await run(engine, "Chiffre d'affaires ?")

    check_turn(emitted)  # MODEL_NO_TOOLS is in the closed list of error codes
    error = emitted[-1]
    assert isinstance(error, events.Error)
    assert (error.code, error.recoverable) == ("MODEL_NO_TOOLS", True)
    assert "ne gère pas les outils" in error.message


async def test_cut_stream_ends_the_turn_and_is_not_recorded() -> None:
    engine = make_engine([Chunk(content="Le chiffre"), StreamInterrupted("connexion coupée")])

    emitted = await run(engine, "Quel chiffre ?")

    check_turn(emitted)
    assert types(emitted) == ["turn_started", "token", "error"]
    assert emitted[-1].to_dict()["code"] == "STREAM_INTERRUPTED"
    assert len(engine.history) == 0


async def test_unexpected_failure_is_internal_and_not_recoverable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = make_engine([RuntimeError("bug")])

    with caplog.at_level(logging.ERROR, logger="sensai.core.engine"):
        emitted = await run(engine, "Salut")

    check_turn(emitted)
    error = emitted[-1]
    assert isinstance(error, events.Error)
    assert (error.code, error.recoverable) == ("INTERNAL", False)
    assert "bug" in caplog.text  # the detail goes to the logs, not to the user
    assert not engine.busy


# --- Session state ---


async def test_set_model_applies_to_the_next_turn_only() -> None:
    engine = make_engine(answer("Un."), answer("Deux."))

    async with contextlib.aclosing(engine.run_turn("Premier")) as turn:
        first = await turn.__anext__()
        assert first.type == "turn_started"
        engine.set_model(OTHER_MODEL)  # while the turn is running
        _rest = [event async for event in turn]
    await run(engine, "Second")

    assert [call[0] for call in fake(engine).calls] == [MODEL, OTHER_MODEL]


async def test_reset_empties_the_history() -> None:
    engine = make_engine(answer("Un."), answer("Je ne sais pas."))

    await run(engine, "Premier")
    engine.reset()
    await run(engine, "De quoi parlait-on ?")

    _model, messages, _options = fake(engine).calls[1]
    assert messages == [{"role": "user", "content": "De quoi parlait-on ?"}]


async def test_reset_during_a_turn_does_not_record_it() -> None:
    engine = make_engine(answer("Réponse", " longue."))

    async with contextlib.aclosing(engine.run_turn("Question")) as turn:
        await turn.__anext__()
        engine.reset()
        _rest = [event async for event in turn]

    assert len(engine.history) == 0


async def test_model_details_and_list_models_go_through_the_engine() -> None:
    engine = make_engine()

    details = await engine.model_details()

    assert details.name == MODEL and details.supports_tools
    assert await engine.list_models() == [MODEL, OTHER_MODEL]


# --- Cancellation and concurrency ---


async def test_cancel_stops_the_turn_at_the_next_chunk() -> None:
    engine = make_engine(answer("Début", " suite", " fin."))

    emitted: list[events.Event] = []
    async with contextlib.aclosing(engine.run_turn("Question longue")) as turn:
        async for event in turn:
            emitted.append(event)
            if event.type == "token":
                assert engine.cancel() is True

    check_turn(emitted)
    assert types(emitted) == ["turn_started", "token", "message_done"]
    done = emitted[-1]
    assert isinstance(done, events.MessageDone)
    assert (done.content, done.interrupted, done.usage) == ("Début", True, None)
    assert len(engine.history) == 0
    assert fake(engine).open_streams == 0  # the HTTP stream is closed: Ollama stops


async def test_cancel_without_a_running_turn_is_ignored() -> None:
    engine = make_engine(answer("Complet."))

    assert engine.cancel() is False
    emitted = await run(engine, "Question")

    done = emitted[-1]
    assert isinstance(done, events.MessageDone) and done.interrupted is False


async def test_a_second_turn_while_busy_is_refused() -> None:
    engine = make_engine(answer("Un."))

    async with contextlib.aclosing(engine.run_turn("Premier")) as first:
        await first.__anext__()
        refused = await run(engine, "Second")
        _rest = [event async for event in first]

    assert types(refused) == ["error"]
    busy = refused[0]
    assert isinstance(busy, events.Error)
    assert (busy.code, busy.turn_id, busy.recoverable) == ("BUSY", None, True)
    assert len(fake(engine).calls) == 1


async def test_closing_the_generator_midway_releases_the_session() -> None:
    engine = make_engine([Chunk(content="Début"), HANG], answer("Ensuite."))

    async with contextlib.aclosing(engine.run_turn("Question")) as turn:
        assert (await turn.__anext__()).type == "turn_started"
        assert (await turn.__anext__()).type == "token"
    # closed midway: no more events, nothing recorded, the session is free again

    assert not engine.busy
    assert len(engine.history) == 0
    assert fake(engine).open_streams == 0
    assert types(await run(engine, "Autre question"))[-1] == "message_done"


# --- HITL pause (_confirm) ---


def pending(expires_in_s: float = 120) -> PendingAction:
    return PendingAction(
        action_id="a-1",
        call_id="k-1",
        tool="sql_toolbox__execute_write",
        sql="DELETE FROM clients WHERE id = 1",
        impact={
            "operation": "DELETE",
            "rows_affected": 1,
            "per_table": {},
            "sample": [],
            "blocked": [],
        },
        expires_in_s=expires_in_s,
    )


class ApprovingBroker:
    async def request(self, action: PendingAction) -> Decision:
        return Decision("approve", "user", comment="OK")


class SilentBroker:
    async def request(self, action: PendingAction) -> Decision:
        await asyncio.Event().wait()  # nobody ever answers
        raise AssertionError("unreachable")


async def test_confirm_returns_the_broker_decision() -> None:
    engine = Engine(Config(), FakeClient(), broker=ApprovingBroker())

    decision = await engine._confirm(pending())

    assert decision.approved and decision.comment == "OK"


async def test_confirm_without_an_answer_times_out() -> None:
    config = Config(hitl=HitlConfig(confirmation_timeout_s=0.05))
    engine = Engine(config, FakeClient(), broker=SilentBroker())

    decision = await engine._confirm(pending(expires_in_s=0.05))

    assert (decision.decision, decision.reason, decision.approved) == ("timeout", "timeout", False)


async def test_default_broker_rejects_everything() -> None:
    engine = Engine(Config(), FakeClient())

    assert isinstance(engine.broker, AutoRejectBroker)
    decision = await engine._confirm(pending())
    assert (decision.decision, decision.reason) == ("reject", "cancelled")


@pytest.mark.parametrize(
    ("decision", "reason"),
    [("approve", "cancelled"), ("reject", "timeout"), ("timeout", "user")],
)
def test_decision_refuses_pairs_outside_the_contract(decision: Any, reason: Any) -> None:
    with pytest.raises(ValueError, match="not allowed"):
        Decision(decision, reason)


# --- Default client ---


async def test_engine_builds_its_own_ollama_client_from_the_config() -> None:
    engine = Engine(Config(ollama=OllamaConfig(host="http://ollama.test:11434")))
    try:
        assert isinstance(engine.client, OllamaClient)
        assert engine.client.host == "http://ollama.test:11434"
    finally:
        await engine.aclose()
