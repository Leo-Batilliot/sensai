"""Web ConfirmationBroker: a pending HITL action is a Future resolved by a WebSocket message.

It implements `sensai.core.confirm.ConfirmationBroker`, like the CLI broker (HITL-02):
- the engine calls `request(action)` inside its turn generator, and bounds the wait to
  `action.expires_in_s` itself (`Engine._confirm`): no answer in time is its "timeout";
- the WebSocket receive loop, a concurrent task, calls `resolve()` when a `confirm`
  message arrives (events.md §6). Resolving from the code that consumes the generator
  would deadlock.
"""

from __future__ import annotations

import asyncio

from sensai.core.confirm import Decision, PendingAction
from sensai.core.events import DecisionKind, DecisionReason


class WebConfirmationBroker:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[Decision]] = {}

    async def request(self, action: PendingAction) -> Decision:
        """Wait for the user's decision, as long as the engine lets this call run."""
        future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._pending[action.action_id] = future
        try:
            return await future
        finally:  # answered, or cancelled by the engine's timeout
            self._pending.pop(action.action_id, None)

    def resolve(self, action_id: str, decision: DecisionKind, comment: str | None = None) -> bool:
        """Apply a `confirm` message. False if the action is unknown, resolved or expired."""
        future = self._pending.get(action_id)
        if future is None or future.done():
            return False
        future.set_result(Decision(decision, "user", comment))
        return True

    def reject_all(self, reason: DecisionReason) -> None:
        """Refuse every pending action: `cancelled` (cancel, reset) or `disconnected`."""
        for future in self._pending.values():
            if not future.done():
                future.set_result(Decision("reject", reason))

    @property
    def pending(self) -> frozenset[str]:
        return frozenset(self._pending)
