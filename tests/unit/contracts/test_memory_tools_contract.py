"""Contract checks on docs/contracts/schemas/memory_tools.v1.json (MEM-01)."""

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from sensai.memory.schema import KINDS, connect, create_schema

CONTRACT = Path(__file__).resolve().parents[3] / "docs/contracts/schemas/memory_tools.v1.json"
TOOLS: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"]
}
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
# Ollama function names (and our MCP prefixing) only accept these characters
OLLAMA_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def validator(tool: str) -> Draft202012Validator:
    return Draft202012Validator(TOOLS[tool]["inputSchema"])


def test_expected_tools() -> None:
    assert set(TOOLS) == {
        "memory_search",
        "glossary_define",
        "memory_upsert_entity",
        "memory_relate",
        "memory_add_fact",
        "memory_update",
        "memory_delete",
    }


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_tool_is_well_formed(name: str) -> None:
    tool = TOOLS[name]
    assert OLLAMA_NAME.match(name)
    assert tool["description"].strip()
    Draft202012Validator.check_schema(tool["inputSchema"])
    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["additionalProperties"] is False


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_all_hints_are_explicit(name: str) -> None:
    # MCP defaults destructiveHint and openWorldHint to true when they are missing.
    annotations = TOOLS[name]["annotations"]
    assert all(isinstance(annotations.get(hint), bool) for hint in HINTS)
    assert annotations["openWorldHint"] is False


def test_only_memory_delete_is_destructive() -> None:
    destructive = {name for name, tool in TOOLS.items() if tool["annotations"]["destructiveHint"]}
    assert destructive == {"memory_delete"}


def test_only_memory_search_is_read_only() -> None:
    read_only = {name for name, tool in TOOLS.items() if tool["annotations"]["readOnlyHint"]}
    assert read_only == {"memory_search"}


@pytest.mark.parametrize("name", ["memory_update", "memory_delete"])
def test_kind_enum_matches_schema(name: str) -> None:
    assert tuple(TOOLS[name]["inputSchema"]["properties"]["kind"]["enum"]) == KINDS


def test_entity_types_match_schema() -> None:
    conn: sqlite3.Connection = connect(":memory:")
    create_schema(conn)
    for entity_type in TOOLS["memory_upsert_entity"]["inputSchema"]["properties"]["type"]["enum"]:
        conn.execute("INSERT INTO entities (type, name) VALUES (?, 'x')", (entity_type,))
    conn.close()


def test_fact_categories_match_schema() -> None:
    conn = connect(":memory:")
    create_schema(conn)
    for category in TOOLS["memory_add_fact"]["inputSchema"]["properties"]["category"]["enum"]:
        conn.execute(
            "INSERT INTO facts (category, content, source) VALUES (?, 'x', 'agent')", (category,)
        )
    conn.close()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("memory_search", {"query": "client rentable"}),
        ("memory_search", {"query": "Lemaire", "kind": "entity", "limit": 3}),
        ("glossary_define", {"term": "client rentable", "definition": "Marge brute > 25 %"}),
        (
            "glossary_define",
            {"term": "client rentable", "definition": "…", "sql_hint": "marge / ca > 0.25"},
        ),
        (
            "memory_upsert_entity",
            {
                "type": "client",
                "name": "Brasserie Lemaire",
                "aliases": ["Lemaire", "BL"],
                "attributes": {"compte_strategique": True},
                "external_ref": "clients:42",
            },
        ),
        ("memory_relate", {"src": "Brasserie Lemaire", "type": "managed_by", "dst": "Julien"}),
        ("memory_add_fact", {"content": "Montants en k€", "category": "preference"}),
        ("memory_add_fact", {"content": "x", "category": "note", "entity": "12"}),
        (
            "memory_update",
            {"kind": "glossary", "ref": "client rentable", "patch": {"sql_hint": "x"}},
        ),
        ("memory_delete", {"kind": "fact", "ref": "17"}),
    ],
)
def test_valid_arguments_are_accepted(tool: str, arguments: dict[str, Any]) -> None:
    validator(tool).validate(arguments)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("memory_search", {}),
        ("memory_search", {"query": ""}),
        ("memory_search", {"query": "x", "kind": "relation"}),
        ("memory_search", {"query": "x", "limit": 0}),
        ("glossary_define", {"term": "x"}),
        ("memory_upsert_entity", {"type": "supplier", "name": "x"}),
        ("memory_upsert_entity", {"type": "client", "name": "x", "external_ref": "clients"}),
        ("memory_relate", {"src": "a", "type": "Managed By", "dst": "b"}),
        ("memory_add_fact", {"content": "x", "category": "rule"}),
        ("memory_update", {"kind": "fact", "ref": "1", "patch": {}}),
        ("memory_update", {"kind": "fact", "ref": 1, "patch": {"content": "x"}}),
        ("memory_delete", {"kind": "facts", "ref": "1"}),
        ("memory_delete", {"kind": "fact", "ref": "1", "force": True}),
        # optional means omitted, never null (memory.md M-4)
        ("glossary_define", {"term": "x", "definition": "y", "sql_hint": None}),
        ("memory_upsert_entity", {"type": "client", "name": "x", "external_ref": None}),
        ("memory_add_fact", {"content": "x", "category": "note", "entity": None}),
    ],
)
def test_invalid_arguments_are_rejected(tool: str, arguments: dict[str, Any]) -> None:
    assert list(validator(tool).iter_errors(arguments))
