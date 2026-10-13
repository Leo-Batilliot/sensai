"""Registry pieces that need no MCP server: naming, result conversion, native tools."""

from typing import Any

import pytest

from sensai.mcp.client import McpCallResult, ToolAnnotations
from sensai.tools.base import ToolResult
from sensai.tools.registry import Registry, to_tool_result, tool_name


class NativeTool:
    """A native tool as the memory tools will be (tools.md §3.5)."""

    name = "memory_search"
    description = "Cherche en mémoire"
    input_schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
        "additionalProperties": False,
    }
    annotations = ToolAnnotations(read_only=True, destructive=False)

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def call(self, arguments: dict[str, Any]) -> ToolResult:
        if self.fail:
            raise RuntimeError("boom")
        return ToolResult(ok=True, text="1 entrée", summary="1 entrée")


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


@pytest.mark.parametrize(
    ("server", "tool", "expected"),
    [
        ("sql_toolbox", "run_select", "sql_toolbox__run_select"),
        ("fs", "read.file", "fs__read_file"),
        ("time", "x" * 80, ("time__" + "x" * 80)[:64]),
    ],
)
def test_tool_name(server: str, tool: str, expected: str) -> None:
    assert tool_name(server, tool) == expected


def test_success_with_rows() -> None:
    data = {"columns": ["n"], "rows": [[1], [2]], "row_count": 2, "truncated": True}
    result = to_tool_result(McpCallResult([text_block('{"rows":...}')], data), duration_ms=12)
    assert result.ok and result.data == data and result.duration_ms == 12
    assert result.summary == "2 lignes · tronqué"
    single = to_tool_result(McpCallResult([text_block("x")], {"row_count": 1, "truncated": False}))
    assert single.summary == "1 ligne"


def test_summary_is_the_start_of_the_text() -> None:
    result = to_tool_result(McpCallResult([text_block("  2026-10-12\n  lundi ")]))
    assert result.summary == "2026-10-12 lundi"
    long = to_tool_result(McpCallResult([text_block("a" * 200)]))
    assert len(long.summary) == 80 and long.summary.endswith("…")


def test_non_text_blocks_and_empty_results() -> None:
    result = to_tool_result(McpCallResult([text_block("voici"), {"type": "image", "data": "..."}]))
    assert result.text == "voici\n[contenu non textuel ignoré]"
    assert to_tool_result(McpCallResult([], {"a": 1})).text == '{"a": 1}'
    assert to_tool_result(McpCallResult([])).text == "(résultat vide)"


def test_tool_errors_keep_the_server_code() -> None:
    ours = to_tool_result(
        McpCallResult(
            [text_block("Erreur SQLite : no such column: x")],
            {"code": "SQL_ERROR", "message": "Erreur SQLite : no such column: x"},
            is_error=True,
        )
    )
    assert not ours.ok and ours.error is not None
    assert ours.error.code == "SQL_ERROR"
    assert ours.text == "Erreur SQLite : no such column: x"
    third_party = to_tool_result(McpCallResult([text_block("Unknown tool")], is_error=True))
    assert third_party.error is not None and third_party.error.code == "TOOL_FAILED"


async def test_native_tools() -> None:
    registry = Registry(native_tools=[NativeTool()])
    info = registry.get("memory_search")
    assert info is not None and info.server is None and info.trusted
    assert registry.servers() == []
    (tool,) = registry.ollama_tools()
    assert tool["function"]["name"] == "memory_search"
    assert "$schema" not in tool["function"]["parameters"]
    assert (await registry.call("memory_search", {"query": "client"})).ok


def test_validate_arguments() -> None:
    registry = Registry(native_tools=[NativeTool()])
    assert registry.validate_arguments("memory_search", {"query": "x"}) == []
    errors = registry.validate_arguments("memory_search", {"query": "", "x": 1, "y": 2})
    assert 1 <= len(errors) <= 3
    assert any(error.startswith("query :") for error in errors)
    assert registry.validate_arguments("nope", {}) == ["outil inconnu : nope"]


async def test_failures_never_raise() -> None:
    registry = Registry(native_tools=[NativeTool(fail=True)])
    result = await registry.call("memory_search", {"query": "x"})
    assert result.error is not None and result.error.code == "TOOL_FAILED"
    unknown = await registry.call("nope", {})
    assert unknown.error is not None and unknown.error.code == "UNKNOWN_TOOL"
