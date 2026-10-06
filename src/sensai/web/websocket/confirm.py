"""Web ConfirmationBroker: a pending HITL action is a Future resolved by a WebSocket message.

The engine awaits `request()` inside its turn generator; the WebSocket receive loop, a
concurrent task, calls `resolve()` when a `confirm` message arrives (events.md §6).
Resolving from the code that consumes the generator would deadlock.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class Resolution:
    """How a confirmation ended: `decision` and `reason` as in events.md §3.7."""

    decision: str  # "approve" | "reject" | "timeout"
    reason: str  # "user" | "cancelled" | "disconnected" | "timeout"
    comment: str | None = None


TIMED_OUT = Resolution("timeout", "timeout")


class WebConfirmationBroker:
    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[Resolution]] = {}

    async def request(self, action_id: str, timeout_s: float) -> Resolution:
        """Wait for the user's decision; no answer within `timeout_s` means a refusal."""
        future: asyncio.Future[Resolution] = asyncio.get_running_loop().create_future()
        self._pending[action_id] = future
        try:
            return await asyncio.wait_for(future, timeout_s)
        except asyncio.TimeoutError:  # distinct from the builtin TimeoutError on Python 3.10
            return TIMED_OUT
        finally:
            self._pending.pop(action_id, None)

    def resolve(self, action_id: str, decision: str, comment: str | None = None) -> bool:
        """Apply a `confirm` message. False if the action is unknown, resolved or expired."""
        future = self._pending.get(action_id)
        if future is None or future.done():
            return False
        future.set_result(Resolution(decision, "user", comment))
        return True

    def reject_all(self, reason: str) -> None:
        """Refuse every pending action (`cancel`, `reset` or disconnection)."""
        for future in self._pending.values():
            if not future.done():
                future.set_result(Resolution("reject", reason))

    @property
    def pending(self) -> frozenset[str]:
        return frozenset(self._pending)
