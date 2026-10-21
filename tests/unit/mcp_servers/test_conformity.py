"""MCP conformity of sql_toolbox, point by point (docs/notes/mcp-conformity.md)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from sensai.data.schema import connect, create_schema
from sensai.mcp import jsonrpc
from sensai.mcp.client import SUPPORTED_PROTOCOL_VERSIONS
from sensai.mcp_servers.sql_toolbox.server import SqlToolboxServer

# One successful and one failing call per tool
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("list_tables", {}),
    ("describe_table", {"table": "clients"}),
    ("describe_table", {"table": "nowhere"}),
    ("run_select", {"sql": "SELECT id, name FROM clients"}),
    ("run_select", {"sql": "DELETE FROM clients"}),
    ("run_select", {"sql": "SELECT nope FROM clients"}),
    ("preview_write", {"sql": "DELETE FROM contacts WHERE id = 1"}),
    ("preview_write", {"sql": "SELECT 1"}),
    ("execute_write", {"sql": "UPDATE clients SET city = city WHERE id = 1"}),
    ("execute_write", {"sql": "DROP TABLE clients"}),
]


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SqlToolboxServer]:
    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.execute(
        "INSERT INTO clients (id, name, sector, city, segment, created_at)"
        " VALUES (1, 'Brasserie Lemaire', 'Restauration', 'Lille', 'PME', '2024-01-01')"
    )
    conn.execute("INSERT INTO contacts (id, client_id, full_name) VALUES (1, 1, 'A')")
    conn.commit()
    conn.close()
    server = SqlToolboxServer(path)
    yield server
    server.close()


def line(message: Any) -> bytes:
    return json.dumps(message).encode() + b"\n"


def call(server: SqlToolboxServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    answer = server.handle(jsonrpc.request(1, "tools/call", {"name": name, "arguments": arguments}))
    assert answer is not None
    return answer["result"]


def tools(server: SqlToolboxServer) -> dict[str, dict[str, Any]]:
    answer = server.handle(jsonrpc.request(1, "tools/list"))
    assert answer is not None
    return {tool["name"]: tool for tool in answer["result"]["tools"]}


# --- Version negotiation and capabilities ---


@pytest.mark.parametrize("version", SUPPORTED_PROTOCOL_VERSIONS)
def test_a_supported_version_is_kept(server: SqlToolboxServer, version: str) -> None:
    answer = server.handle(jsonrpc.request(1, "initialize", {"protocolVersion": version}))
    assert answer is not None and answer["result"]["protocolVersion"] == version


def test_an_unknown_version_gets_ours(server: SqlToolboxServer) -> None:
    answer = server.handle(jsonrpc.request(1, "initialize", {"protocolVersion": "2030-01-01"}))
    assert answer is not None
    assert answer["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert answer["result"]["capabilities"] == {"tools": {"listChanged": False}}


# --- tools/list ---


def test_tools_list_accepts_a_cursor_and_has_one_page(server: SqlToolboxServer) -> None:
    answer = server.handle(jsonrpc.request(1, "tools/list", {"cursor": "anything"}))
    assert answer is not None
    assert len(answer["result"]["tools"]) == 5
    assert "nextCursor" not in answer["result"]


def test_every_tool_declares_an_object_output_schema(server: SqlToolboxServer) -> None:
    for tool in tools(server).values():
        assert tool["outputSchema"]["type"] == "object"
        jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])


# --- tools/call ---


@pytest.mark.parametrize(("name", "arguments"), CALLS, ids=lambda value: str(value)[:40])
def test_structured_content_conforms_to_the_output_schema(
    server: SqlToolboxServer, name: str, arguments: dict[str, Any]
) -> None:
    # MCP: with an outputSchema, every structuredContent must conform, failures included
    result = call(server, name, arguments)
    schema = tools(server)[name]["outputSchema"]
    assert (
        list(jsonschema.Draft202012Validator(schema).iter_errors(result["structuredContent"])) == []
    )
    # The text block carries the same information for clients that ignore structuredContent
    assert result["content"][0]["type"] == "text" and result["content"][0]["text"]


def test_a_tool_failure_is_a_result_not_a_protocol_error(server: SqlToolboxServer) -> None:
    result = call(server, "run_select", {"sql": "DELETE FROM clients"})
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "SQL_FORBIDDEN"


def test_unknown_tool_is_invalid_params(server: SqlToolboxServer) -> None:
    answer = server.handle(jsonrpc.request(1, "tools/call", {"name": "nope", "arguments": {}}))
    assert answer is not None and answer["error"]["code"] == jsonrpc.INVALID_PARAMS


def test_meta_in_params_is_accepted(server: SqlToolboxServer) -> None:
    # MCP Inspector sends _meta.progressToken: progress is optional, the call must work
    params = {"name": "list_tables", "arguments": {}, "_meta": {"progressToken": 15}}
    answer = server.handle(jsonrpc.request(1, "tools/call", params))
    assert answer is not None and answer["result"]["isError"] is False


# --- JSON-RPC errors ---


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"not json\n", jsonrpc.PARSE_ERROR),
        (line([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]), jsonrpc.INVALID_REQUEST),
        (line({"jsonrpc": "1.0", "id": 1, "method": "ping"}), jsonrpc.INVALID_REQUEST),
        (line({"id": 1, "method": "ping"}), jsonrpc.INVALID_REQUEST),
        (line({"jsonrpc": "2.0", "id": 1, "method": 42}), jsonrpc.INVALID_REQUEST),
        (line({"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"}), jsonrpc.INVALID_REQUEST),
        (line({"jsonrpc": "2.0", "id": 1}), jsonrpc.INVALID_REQUEST),
        (line({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "x"}), -32602),
        (line({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}), jsonrpc.METHOD_NOT_FOUND),
    ],
)
def test_protocol_errors(server: SqlToolboxServer, raw: bytes, code: int) -> None:
    answer = server.handle_line(raw)
    assert answer is not None and answer["error"]["code"] == code


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/something_new"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}},
        {"jsonrpc": "2.0", "id": 9, "result": {}},
    ],
)
def test_notifications_and_answers_get_no_reply(
    server: SqlToolboxServer, message: dict[str, Any]
) -> None:
    assert server.handle_line(line(message)) is None


def test_an_internal_bug_costs_one_answer_not_the_server(
    server: SqlToolboxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("bug")

    monkeypatch.setitem(server._handlers, "list_tables", broken)
    request = {"jsonrpc": "2.0", "id": 4, "method": "tools/call"}
    answer = server.handle_line(line(request | {"params": {"name": "list_tables"}}))
    assert answer is not None and answer["error"]["code"] == jsonrpc.INTERNAL_ERROR
    assert answer["id"] == 4
    ping = server.handle_line(line({"jsonrpc": "2.0", "id": 5, "method": "ping"}))
    assert ping is not None and ping["result"] == {}
