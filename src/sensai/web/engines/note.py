"""The committee note from the Note panel (WEB-17, M4, memory.md §7.5).

The panel edits a section (document_patch), deletes one (document_delete_section) or goes
back to a revision (document_restore) through `ui_action`. The Engine has no
run_ui_action yet (point P-6, P1): the turn of such an action is built here, the same for
the MockEngine and the RealEngine, on the note tools of ARTIF-01 with the actor "user".

Same policy and same broker as the session: document_delete_section is destructive, so
it always waits for a confirmation, like memory_delete. A REVISION_CONFLICT (the agent
wrote meanwhile) is a failed tool_result: the panel reads the note again.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sensai.core import events
from sensai.core.config import HitlConfig
from sensai.core.confirm import ConfirmationBroker, Decision, PendingAction
from sensai.documents.store import DocumentStore
from sensai.tools.base import ToolResult
from sensai.tools.document_tools import DELETE_SECTION, document_tools
from sensai.tools.policy import evaluate
from sensai.tools.registry import Registry

NOTE_ACTIONS = frozenset({"document_patch", "document_delete_section", "document_restore"})

REJECTED = {
    "reject": "Suppression refusée.",
    "timeout": "Pas de confirmation dans le délai : suppression annulée.",
}


def note_registry(store: DocumentStore, session_id: str) -> Registry:
    """The note tools as the interface sees them: document_restore included."""
    return Registry(
        None,
        document_tools(store, session_id, actor="user"),
        previewers={DELETE_SECTION: lambda args: store.delete_impact(args["section_id"])},
    )


async def note_action(
    registry: Registry,
    tool: str,
    arguments: dict[str, Any],
    *,
    session_id: str,
    broker: ConfirmationBroker,
    hitl: HitlConfig,
) -> AsyncIterator[events.Event]:
    """The turn of one ui_action on the note (websocket.md §4.2, example §8.3)."""
    started = time.monotonic()
    turn_id, call_id = _new_id(), _new_id()

    def common() -> dict[str, Any]:
        return {"session_id": session_id, "turn_id": turn_id}

    yield events.TurnStarted(**common(), origin="ui", user_message=None)
    yield events.ToolCall(**common(), call_id=call_id, server=None, tool=tool, arguments=arguments)
    info = registry.get(tool)
    errors = registry.validate_arguments(tool, arguments) if info is not None else []
    result: ToolResult | None = None
    if info is None:
        result = ToolResult.failure("UNKNOWN_TOOL", f"Outil inconnu : {tool}.")
    elif errors:
        result = ToolResult.failure(
            "INVALID_ARGUMENTS", f"Arguments invalides : {' ; '.join(errors)}."
        )
    else:
        decision = evaluate(info, arguments, hitl)
        if decision.action == "deny":
            result = ToolResult.failure(
                decision.code or "TOOL_DENIED", f"Action refusée : {' ; '.join(decision.reasons)}."
            )
        elif decision.action == "confirm":
            action = PendingAction(
                action_id=_new_id(),
                call_id=call_id,
                tool=tool,
                sql=None,
                impact=await registry.preview(tool, arguments) or _no_figures(),
                expires_in_s=hitl.confirmation_timeout_s,
            )
            yield events.ConfirmationRequired(
                **common(),
                action_id=action.action_id,
                call_id=call_id,
                tool=tool,
                sql=None,
                impact=action.impact,
                expires_in_s=action.expires_in_s,
            )
            answer = await _confirm(broker, action)
            yield events.ConfirmationResolved(
                **common(),
                action_id=action.action_id,
                decision=answer.decision,
                reason=answer.reason,
                comment=answer.comment,
            )
            if not answer.approved:
                message = REJECTED.get(answer.decision, "Suppression annulée.")
                result = ToolResult.failure("ACTION_REJECTED", message)
    if result is None:
        result = await registry.call(tool, arguments)
    yield events.ToolResult(
        **common(),
        call_id=call_id,
        ok=result.ok,
        summary=result.summary,
        data=result.data,
        error=result.error,
        duration_ms=result.duration_ms,
    )
    change = result.document_change
    if change is not None:
        yield events.DocumentUpdated(
            **common(),
            doc_id=change.doc_id,
            revision=change.revision,
            section_id=change.section_id,
            op=change.op,
            summary=change.summary,
        )
    yield events.MessageDone(
        **common(),
        content="",
        interrupted=False,
        usage=None,
        duration_ms=round((time.monotonic() - started) * 1000),
    )


async def _confirm(broker: ConfirmationBroker, action: PendingAction) -> Decision:
    """Same pause as Engine._confirm: no answer in time is a refusal."""
    try:
        return await asyncio.wait_for(broker.request(action), action.expires_in_s)
    except asyncio.TimeoutError:  # distinct from the builtin TimeoutError in 3.10
        return Decision("timeout", "timeout")


def _no_figures() -> dict[str, Any]:
    # The section is unknown: the call will fail with section_not_found after the decision
    return {"operation": "DELETE", "rows_affected": 0, "per_table": {}, "sample": [], "blocked": []}


def _new_id() -> str:
    return str(uuid.uuid4())
