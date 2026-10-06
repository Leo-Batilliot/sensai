"""Human-in-the-loop pause (HITL): what the Engine asks, and who answers.

The Engine emits confirmation_required, then awaits ConfirmationBroker.request() for at
most expires_in_s (Engine._confirm), then emits confirmation_resolved. Each adapter
brings its broker: keyboard in the CLI (HITL-02), modal over WebSocket (WEB-08).

The broker must be answered by a task other than the one consuming the event generator:
that consumer is stuck waiting for the next event, so nobody would answer (deadlock).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sensai.core.events import DecisionKind, DecisionReason

# Allowed (decision, reason) pairs (docs/contracts/events.md §3.7)
REASONS: dict[str, frozenset[str]] = {
    "approve": frozenset({"user"}),
    "reject": frozenset({"user", "cancelled", "disconnected"}),
    "timeout": frozenset({"timeout"}),
}


@dataclass(frozen=True)
class PendingAction:
    """An action waiting for a decision: the payload of confirmation_required."""

    action_id: str
    call_id: str
    tool: str
    sql: str | None
    impact: dict[str, Any]
    expires_in_s: float


@dataclass(frozen=True)
class Decision:
    """The answer to a PendingAction: the payload of confirmation_resolved."""

    decision: DecisionKind
    reason: DecisionReason
    comment: str | None = None

    def __post_init__(self) -> None:
        if self.reason not in REASONS[self.decision]:
            raise ValueError(f"reason {self.reason!r} is not allowed with {self.decision!r}")

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


class ConfirmationBroker(Protocol):
    """Asks a human to approve or reject an action."""

    async def request(self, action: PendingAction) -> Decision: ...


class AutoRejectBroker:
    """Rejects every action: the default when nobody can answer (tests, before HITL-02).

    The contract has no reason for an automatic refusal; "cancelled" is the closest.
    """

    async def request(self, action: PendingAction) -> Decision:
        return Decision("reject", "cancelled")
