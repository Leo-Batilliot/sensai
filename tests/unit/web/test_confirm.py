"""WebConfirmationBroker, alone and plugged into the real Engine (CORE-05)."""

from __future__ import annotations

import asyncio

from fakes import FakeClient
from sensai.core.config import Config
from sensai.core.confirm import ConfirmationBroker, PendingAction
from sensai.core.engine import Engine
from sensai.web.websocket.confirm import WebConfirmationBroker


def pending(action_id: str = "a-1", expires_in_s: float = 120) -> PendingAction:
    return PendingAction(
        action_id=action_id,
        call_id="k-1",
        tool="sql_toolbox__execute_write",
        sql="DELETE FROM clients WHERE status = 'inactive'",
        impact={
            "operation": "DELETE",
            "rows_affected": 27,
            "per_table": {},
            "sample": [],
            "blocked": [],
        },
        expires_in_s=expires_in_s,
    )


async def answer_soon(broker: WebConfirmationBroker, action_id: str, decision: str) -> bool:
    """Plays the WebSocket receive loop: a concurrent task answers once the request is pending."""
    while action_id not in broker.pending:  # noqa: ASYNC110 - lets the requesting task run
        await asyncio.sleep(0)
    return broker.resolve(action_id, decision, "vu")  # type: ignore[arg-type]


def test_it_is_a_core_confirmation_broker() -> None:
    broker: ConfirmationBroker = WebConfirmationBroker()  # checked by the type annotation
    assert callable(broker.request)


async def test_resolve_gives_the_user_decision() -> None:
    broker = WebConfirmationBroker()
    request = asyncio.create_task(broker.request(pending()))
    assert await answer_soon(broker, "a-1", "approve")
    decision = await request
    assert (decision.decision, decision.reason, decision.comment) == ("approve", "user", "vu")
    assert broker.pending == frozenset()


async def test_an_action_is_resolved_only_once() -> None:
    broker = WebConfirmationBroker()
    request = asyncio.create_task(broker.request(pending()))
    assert await answer_soon(broker, "a-1", "reject")
    assert broker.resolve("a-1", "approve") is False  # already answered
    assert broker.resolve("unknown", "approve") is False
    assert (await request).decision == "reject"


async def test_reject_all_refuses_every_pending_action() -> None:
    broker = WebConfirmationBroker()
    requests = [asyncio.create_task(broker.request(pending(f"a-{i}"))) for i in range(2)]
    while len(broker.pending) < 2:  # noqa: ASYNC110 - lets the requesting tasks run
        await asyncio.sleep(0)
    broker.reject_all("disconnected")
    decisions = await asyncio.gather(*requests)
    assert {(d.decision, d.reason) for d in decisions} == {("reject", "disconnected")}


# --- with the real Engine of P1 (plugged by WEB-08) -----------------------------------


async def test_engine_gets_the_decision_sent_over_the_websocket() -> None:
    broker = WebConfirmationBroker()
    engine = Engine(Config(), FakeClient(), broker=broker)
    answer = asyncio.create_task(answer_soon(broker, "a-1", "approve"))
    decision = await engine._confirm(pending())  # the HITL pause of the engine
    assert await answer
    assert decision.approved


async def test_engine_timeout_leaves_no_pending_action() -> None:
    broker = WebConfirmationBroker()
    engine = Engine(Config(), FakeClient(), broker=broker)
    decision = await engine._confirm(pending(expires_in_s=0.05))
    assert (decision.decision, decision.reason) == ("timeout", "timeout")
    assert broker.pending == frozenset()  # an expired action can no longer be confirmed
    assert broker.resolve("a-1", "approve") is False
