"""The committee note tools (ARTIF-01, M4), native tools on top of the DocumentStore.

Contract: docs/contracts/memory.md §7. Names, descriptions, inputSchema and annotations
are read from docs/contracts/schemas/document_tools.v1.json, never retyped here.

- document_read, document_patch and document_delete_section are given to the model.
  document_restore is marked ui_only: it is only built for the interface (ui_action).
- A store error is a failed ToolResult with its code (memory.md §7.4), sent back to the
  model so that it reads the note again or fixes its call. It is never raised.
- An effective write carries a DocumentChange, from which the Engine emits
  document_updated; a write that changes nothing carries none.
- document_delete_section only deletes: the HITL policy asks for the confirmation (the
  tool is destructive), with the preview of DocumentStore.delete_impact().
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sensai.documents.store import DocumentError, DocumentStore, DocumentWrite
from sensai.memory.store import AuditContext
from sensai.tools.base import DocumentChange, Tool, ToolAnnotations, ToolResult

# The package is installed in editable mode: the contract is read from the repo
CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contracts"
    / "schemas"
    / "document_tools.v1.json"
)
DELETE_SECTION = "document_delete_section"

if TYPE_CHECKING:  # annotations only: never evaluated at run time
    from collections.abc import Callable

    Handler = Callable[
        [DocumentStore, dict[str, Any], AuditContext], DocumentWrite | dict[str, Any]
    ]


def load_definitions() -> dict[str, dict[str, Any]]:
    """Tool definitions of the contract, by name, in the shape of an MCP tools/list."""
    tools = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["tools"]
    return {tool["name"]: tool for tool in tools}


def _read(store: DocumentStore, args: dict[str, Any], ctx: AuditContext) -> dict[str, Any]:
    return store.read(args.get("section_id"))


def _patch(store: DocumentStore, args: dict[str, Any], ctx: AuditContext) -> DocumentWrite:
    return store.patch(
        args["section_id"],
        args["op"],
        args["content"],
        args["base_revision"],
        args.get("title"),
        ctx,
    )


def _delete(store: DocumentStore, args: dict[str, Any], ctx: AuditContext) -> DocumentWrite:
    return store.delete_section(args["section_id"], args["base_revision"], ctx)


def _restore(store: DocumentStore, args: dict[str, Any], ctx: AuditContext) -> DocumentWrite:
    return store.restore(args["revision"], ctx)


HANDLERS: dict[str, Handler] = {
    "document_read": _read,
    "document_patch": _patch,
    DELETE_SECTION: _delete,
    "document_restore": _restore,
}


def _result(outcome: DocumentWrite | dict[str, Any], duration_ms: int) -> ToolResult:
    if isinstance(outcome, DocumentWrite):
        data = outcome.to_dict()
        summary = outcome.summary
        change = (
            DocumentChange(
                doc_id=outcome.doc_id,
                revision=outcome.revision,
                section_id=outcome.section_id,
                op=outcome.op,
                summary=outcome.summary,
            )
            if outcome.changed
            else None
        )
    else:
        data = outcome
        change = None
        if "sections" in outcome:
            summary = f"Plan de la note, révision {outcome['revision']}"
        else:
            summary = f"Section « {outcome['title']} » lue"
    return ToolResult(
        ok=True,
        text=json.dumps(data, ensure_ascii=False),
        summary=summary,
        data=data,
        duration_ms=duration_ms,
        document_change=change,
    )


@dataclass(frozen=True)
class DocumentTool:
    """One note tool: implements the Tool protocol of tools.md §3.5."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations
    store: DocumentStore = field(repr=False)
    ctx: AuditContext
    handler: Handler = field(repr=False)

    async def call(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            outcome = self.handler(self.store, arguments, self.ctx)
        except DocumentError as exc:
            return ToolResult.failure(exc.code, exc.message, duration_ms=_elapsed_ms(started))
        return _result(outcome, _elapsed_ms(started))


def document_tools(
    store: DocumentStore, session_id: str | None = None, *, actor: str = "agent"
) -> list[Tool]:
    """The tools of memory.md §7.3 on this store. For the model (actor "agent"), the
    ui_only tool is left out; for the interface (actor "user"), every tool is given."""
    ctx = AuditContext(actor="user" if actor == "user" else "agent", session_id=session_id)
    return [
        DocumentTool(
            name=name,
            description=definition["description"],
            input_schema=definition["inputSchema"],
            annotations=ToolAnnotations.from_payload(definition["annotations"]),
            store=store,
            ctx=ctx,
            handler=HANDLERS[name],
        )
        for name, definition in load_definitions().items()
        if ctx.actor == "user" or not definition.get("ui_only", False)
    ]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


__all__ = ["CONTRACT_PATH", "DELETE_SECTION", "DocumentTool", "document_tools", "load_definitions"]
