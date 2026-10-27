"""Contract checks on docs/contracts/schemas/document_tools.v1.json (M4, contracts v1.1)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[3] / "docs/contracts"
CONTRACT = CONTRACTS / "schemas/document_tools.v1.json"
TOOLS: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in json.loads(CONTRACT.read_text(encoding="utf-8"))["tools"]
}
MEMORY_TOOLS = json.loads((CONTRACTS / "schemas/memory_tools.v1.json").read_text(encoding="utf-8"))
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
OLLAMA_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_CONTENT = 4000  # memory.md §7.1: one section per call, 4 000 characters at most


def validator(tool: str) -> Draft202012Validator:
    return Draft202012Validator(TOOLS[tool]["inputSchema"])


def test_expected_tools() -> None:
    assert set(TOOLS) == {
        "document_read",
        "document_patch",
        "document_delete_section",
        "document_restore",
    }


def test_names_do_not_clash_with_the_memory_tools() -> None:
    assert not set(TOOLS) & {tool["name"] for tool in MEMORY_TOOLS["tools"]}


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
    # MCP defaults destructiveHint and openWorldHint to true when they are missing
    annotations = TOOLS[name]["annotations"]
    assert all(isinstance(annotations.get(hint), bool) for hint in HINTS)
    assert annotations["openWorldHint"] is False


def test_only_the_section_deletion_is_destructive() -> None:
    destructive = {name for name, tool in TOOLS.items() if tool["annotations"]["destructiveHint"]}
    assert destructive == {"document_delete_section"}


def test_only_document_read_is_read_only() -> None:
    read_only = {name for name, tool in TOOLS.items() if tool["annotations"]["readOnlyHint"]}
    assert read_only == {"document_read"}


def test_restore_is_for_the_interface_only() -> None:
    ui_only = {name for name, tool in TOOLS.items() if tool.get("ui_only")}
    assert ui_only == {"document_restore"}


def test_patch_ops_and_content_limit() -> None:
    properties = TOOLS["document_patch"]["inputSchema"]["properties"]
    assert properties["op"]["enum"] == ["replace", "append", "insert_after"]
    assert properties["content"]["maxLength"] == MAX_CONTENT


@pytest.mark.parametrize("name", ["document_patch", "document_delete_section"])
def test_every_write_by_the_model_names_its_base_revision(name: str) -> None:
    assert "base_revision" in TOOLS[name]["inputSchema"]["required"]


def test_valid_and_invalid_patches() -> None:
    check = validator("document_patch")
    good = {"section_id": "rentabilite", "op": "replace", "content": "…", "base_revision": 4}
    assert not list(check.iter_errors(good))
    new = {**good, "op": "insert_after", "title": "Risques"}
    assert not list(check.iter_errors(new))
    assert list(check.iter_errors({**good, "content": "x" * (MAX_CONTENT + 1)}))
    assert list(check.iter_errors({**good, "op": "rewrite_all"}))
    assert list(check.iter_errors({**good, "section_id": "Rentabilité"}))  # not an id
    assert list(check.iter_errors({key: v for key, v in good.items() if key != "base_revision"}))


def test_read_takes_an_optional_section() -> None:
    check = validator("document_read")
    assert not list(check.iter_errors({}))
    assert not list(check.iter_errors({"section_id": "synthese"}))
    assert list(check.iter_errors({"section_id": "synthese", "content": "x"}))


def test_the_contract_describes_every_tool() -> None:
    text = (CONTRACTS / "memory.md").read_text(encoding="utf-8")
    section = text[text.index("## 7. Note du comité") :]
    for name in TOOLS:
        assert f"`{name}`" in section
