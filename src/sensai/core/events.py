"""Engine events: what the Engine emits and every adapter (CLI, web, harness) consumes.

One frozen dataclass per event type of docs/contracts/events.md, which is the source of
truth. The JSON schemas in docs/contracts/schemas/events/ check that to_dict() matches it.
Every field is always present: a missing value is None, never an omitted key.

Some names (ToolCall, ToolResult, Error) also exist elsewhere: import the module
(``from sensai.core import events``) and write ``events.ToolResult``.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

Origin = Literal["user", "ui"]
Operation = Literal["SELECT", "INSERT", "UPDATE", "DELETE", "OTHER"]
DecisionKind = Literal["approve", "reject", "timeout"]
DecisionReason = Literal["user", "cancelled", "disconnected", "timeout"]
MemoryOp = Literal["create", "update", "delete"]
MemoryKind = Literal["glossary", "entity", "relation", "fact"]
Badge = Literal["verified", "needs_review", "contradicted", "unavailable"]


@dataclass(frozen=True, kw_only=True)
class Event:
    """Fields shared by every event (events.md §2)."""

    type: ClassVar[str] = ""
    session_id: str
    turn_id: str | None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """The event as sent over the wire: `type` first, then every field, None included."""
        return {"type": self.type, **dataclasses.asdict(self)}


# --- Nested values ---


@dataclass(frozen=True)
class MemoryRef:
    kind: MemoryKind
    ref: str
    label: str


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True)
class ContextUsage:
    """Token counts of the last model call of a turn (events.md §3.10)."""

    prompt_tokens: int
    completion_tokens: int
    ctx_used: int
    ctx_max: int


# --- Events (events.md §3) ---


@dataclass(frozen=True, kw_only=True)
class TurnStarted(Event):
    type: ClassVar[str] = "turn_started"
    origin: Origin
    user_message: str | None


@dataclass(frozen=True, kw_only=True)
class ContextInjected(Event):
    type: ClassVar[str] = "context_injected"
    refs: list[MemoryRef]


@dataclass(frozen=True, kw_only=True)
class Token(Event):
    type: ClassVar[str] = "token"
    delta: str


@dataclass(frozen=True, kw_only=True)
class Thinking(Event):
    type: ClassVar[str] = "thinking"
    delta: str


@dataclass(frozen=True, kw_only=True)
class ToolCall(Event):
    type: ClassVar[str] = "tool_call"
    call_id: str
    server: str | None
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class SqlGenerated(Event):
    type: ClassVar[str] = "sql_generated"
    call_id: str
    sql: str
    operation: Operation


@dataclass(frozen=True, kw_only=True)
class ConfirmationRequired(Event):
    type: ClassVar[str] = "confirmation_required"
    action_id: str
    call_id: str
    tool: str
    sql: str | None
    impact: dict[str, Any]
    expires_in_s: float


@dataclass(frozen=True, kw_only=True)
class ConfirmationResolved(Event):
    type: ClassVar[str] = "confirmation_resolved"
    action_id: str
    decision: DecisionKind
    reason: DecisionReason
    comment: str | None


@dataclass(frozen=True, kw_only=True)
class ToolResult(Event):
    type: ClassVar[str] = "tool_result"
    call_id: str
    ok: bool
    summary: str
    data: dict[str, Any] | None
    error: ToolError | None
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class MemoryUpdated(Event):
    type: ClassVar[str] = "memory_updated"
    op: MemoryOp
    kind: MemoryKind
    ref: str
    summary: str


@dataclass(frozen=True, kw_only=True)
class MessageDone(Event):
    type: ClassVar[str] = "message_done"
    content: str
    interrupted: bool
    usage: ContextUsage | None
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class JudgeStarted(Event):
    type: ClassVar[str] = "judge_started"


@dataclass(frozen=True, kw_only=True)
class JudgeReport(Event):
    type: ClassVar[str] = "judge_report"
    badge: Badge
    scores: dict[str, float | None]
    numeric_check: dict[str, Any]
    report: dict[str, Any] | None
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class Error(Event):
    type: ClassVar[str] = "error"
    code: str
    message: str
    recoverable: bool


EVENT_TYPES: dict[str, type[Event]] = {
    cls.type: cls
    for cls in (
        TurnStarted,
        ContextInjected,
        Token,
        Thinking,
        ToolCall,
        SqlGenerated,
        ConfirmationRequired,
        ConfirmationResolved,
        ToolResult,
        MemoryUpdated,
        MessageDone,
        JudgeStarted,
        JudgeReport,
        Error,
    )
}
