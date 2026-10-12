"""The 7 memory tools of the agent (MEM-03), native tools on top of the MemoryStore.

Contract: docs/contracts/memory.md §3. Names, descriptions, inputSchema and annotations
are read from docs/contracts/schemas/memory_tools.v1.json, never retyped here, so that
what the model sees is exactly what the contract says.

- Arguments are validated by the registry before the call (T5): a tool only fills the
  defaults that jsonschema does not apply (memory_search.limit).
- A store error is a failed ToolResult with its lowercase code (memory.md §3.4), sent
  back to the model so that it can fix its call. It is never raised.
- An effective write carries a MemoryChange, from which the Engine emits memory_updated;
  'unchanged' carries none (tools.md §3.4).
- memory_delete only deletes: asking for a confirmation is the HITL policy's job
  (HITL-01/02), with the preview of MemoryStore.delete_impact().
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sensai.memory.errors import MemoryStoreError
from sensai.memory.store import DEFAULT_SEARCH_LIMIT, AuditContext, MemoryStore, WriteResult
from sensai.tools.base import MemoryChange, Tool, ToolAnnotations, ToolResult

# The package is installed in editable mode: the contract is read from the repo
CONTRACT_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "contracts" / "schemas" / "memory_tools.v1.json"
)

if TYPE_CHECKING:  # annotations only: never evaluated at run time
    from collections.abc import Callable

    Handler = Callable[
        [MemoryStore, dict[str, Any], AuditContext], WriteResult | list[dict[str, Any]]
    ]


def load_definitions() -> dict[str, dict[str, Any]]:
    """Tool definitions of the contract, by name, in the shape of an MCP tools/list."""
    tools = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["tools"]
    return {tool["name"]: tool for tool in tools}


# --- one handler per tool: arguments already validated against the inputSchema ---------


def _search(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> list[dict[str, Any]]:
    return store.search(args["query"], args.get("kind"), args.get("limit", DEFAULT_SEARCH_LIMIT))


def _define(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.define_term(args["term"], args["definition"], args.get("sql_hint"), ctx)


def _upsert_entity(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.upsert_entity(
        args["type"],
        args["name"],
        args.get("aliases", ()),
        args.get("attributes"),
        args.get("external_ref"),
        ctx,
    )


def _relate(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.relate(args["src"], args["type"], args["dst"], ctx)


def _add_fact(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.add_fact(args["content"], args["category"], args.get("entity"), "agent", ctx)


def _update(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.update(args["kind"], args["ref"], args["patch"], ctx)


def _delete(store: MemoryStore, args: dict[str, Any], ctx: AuditContext) -> WriteResult:
    return store.delete(args["kind"], args["ref"], ctx)


HANDLERS: dict[str, Handler] = {
    "memory_search": _search,
    "glossary_define": _define,
    "memory_upsert_entity": _upsert_entity,
    "memory_relate": _relate,
    "memory_add_fact": _add_fact,
    "memory_update": _update,
    "memory_delete": _delete,
}


# --- results ----------------------------------------------------------------------------


def _search_result(results: list[dict[str, Any]], duration_ms: int) -> ToolResult:
    data = {"results": results}
    summary = f"{len(results)} entrée(s) trouvée(s)" if results else "Rien en mémoire"
    return ToolResult(
        ok=True,
        text=json.dumps(data, ensure_ascii=False),
        summary=summary,
        data=data,
        duration_ms=duration_ms,
    )


def _write_result(result: WriteResult, duration_ms: int) -> ToolResult:
    data = result.to_dict()
    change = (
        None
        if result.op == "unchanged"
        else MemoryChange(op=result.op, kind=result.kind, ref=result.ref, summary=result.summary)
    )
    return ToolResult(
        ok=True,
        text=json.dumps(data, ensure_ascii=False),
        summary=result.summary,
        data=data,
        duration_ms=duration_ms,
        memory_change=change,
    )


@dataclass(frozen=True)
class MemoryTool:
    """One memory tool: implements the Tool protocol of tools.md §3.5."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: ToolAnnotations
    store: MemoryStore = field(repr=False)
    ctx: AuditContext
    handler: Handler = field(repr=False)

    async def call(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        try:
            outcome = self.handler(self.store, arguments, self.ctx)
        except MemoryStoreError as exc:
            return ToolResult.failure(exc.code, exc.message, duration_ms=_elapsed_ms(started))
        if isinstance(outcome, WriteResult):
            return _write_result(outcome, _elapsed_ms(started))
        return _search_result(outcome, _elapsed_ms(started))


def memory_tools(store: MemoryStore, session_id: str | None = None) -> list[Tool]:
    """The 7 tools of memory.md §3 on this store, audited as the agent of this session."""
    ctx = AuditContext(actor="agent", session_id=session_id)
    return [
        MemoryTool(
            name=name,
            description=definition["description"],
            input_schema=definition["inputSchema"],
            annotations=ToolAnnotations.from_payload(definition["annotations"]),
            store=store,
            ctx=ctx,
            handler=HANDLERS[name],
        )
        for name, definition in load_definitions().items()
    ]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


__all__ = ["CONTRACT_PATH", "MemoryTool", "load_definitions", "memory_tools"]
