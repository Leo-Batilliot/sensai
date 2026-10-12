import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sensai.data.schema import connect, create_schema
from sensai.mcp import jsonrpc
from sensai.mcp.client import SUPPORTED_PROTOCOL_VERSIONS
from sensai.mcp_servers.sql_toolbox.server import SqlToolboxServer, serve


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SqlToolboxServer]:
    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.execute(
        "INSERT INTO sales_reps VALUES (1, 'Julien Marchand', 'j@nordvent.fr', 'Lille', '2020-01-01')"
    )
    conn.executemany(
        "INSERT INTO clients (id, name, sector, city, segment, status, sales_rep_id, created_at)"
        " VALUES (?, ?, 'Restauration', 'Lille', 'PME', ?, 1, '2024-01-01')",
        [(1, "Brasserie Lemaire", "active"), (2, "Atelier Morel", "active"), (3, "X", "inactive")],
    )
    conn.commit()
    conn.close()
    server = SqlToolboxServer(path, row_limit=2, timeout_ms=200)
    yield server
    server.close()


def request(server: SqlToolboxServer, method: str, params: dict[str, Any] | None = None) -> Any:
    answer = server.handle(jsonrpc.request(1, method, params))
    assert answer is not None
    return answer


def call(server: SqlToolboxServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    answer = request(server, "tools/call", {"name": name, "arguments": arguments})
    return answer["result"]


def test_initialize(server: SqlToolboxServer) -> None:
    result = request(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "sql-toolbox"
    # An unknown version gets our latest one; the client decides what to do with it
    result = request(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert result["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_notifications_get_no_answer(server: SqlToolboxServer) -> None:
    assert server.handle(jsonrpc.notification("notifications/initialized")) is None
    assert server.handle(jsonrpc.notification("notifications/cancelled", {"requestId": 1})) is None


def test_ping_and_unknown_method(server: SqlToolboxServer) -> None:
    assert request(server, "ping")["result"] == {}
    assert request(server, "resources/list")["error"]["code"] == jsonrpc.METHOD_NOT_FOUND


def test_parse_error(server: SqlToolboxServer) -> None:
    answer = server.handle_line(b"not json\n")
    assert answer is not None and answer["error"]["code"] == jsonrpc.PARSE_ERROR


def test_tools_are_declared_with_explicit_annotations(server: SqlToolboxServer) -> None:
    tools = request(server, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["list_tables", "describe_table", "run_select"]
    for tool in tools:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"
        assert "outputSchema" in tool
        hints = {k: v for k, v in tool["annotations"].items() if k.endswith("Hint")}
        assert hints == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


def test_unknown_tool_and_invalid_params(server: SqlToolboxServer) -> None:
    answer = request(server, "tools/call", {"name": "drop_everything", "arguments": {}})
    assert answer["error"]["code"] == jsonrpc.INVALID_PARAMS
    answer = request(server, "tools/call", {"name": "run_select", "arguments": {}})
    assert answer["error"]["code"] == jsonrpc.INVALID_PARAMS
    answer = request(
        server, "tools/call", {"name": "run_select", "arguments": {"sql": "SELECT 1", "x": 1}}
    )
    assert answer["error"]["code"] == jsonrpc.INVALID_PARAMS


def test_results_carry_text_and_structured_content(server: SqlToolboxServer) -> None:
    result = call(server, "list_tables", {})
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_list_tables(server: SqlToolboxServer) -> None:
    tables = call(server, "list_tables", {})["structuredContent"]["tables"]
    names = [table["name"] for table in tables]
    assert "clients" in names and "write_audit" in names
    assert all(table["description"] for table in tables)


def test_describe_table(server: SqlToolboxServer) -> None:
    data = call(server, "describe_table", {"table": "clients"})["structuredContent"]
    assert data["row_count"] == 3
    assert "-- company size" in data["ddl"]  # column comments reach the model
    columns = {column["name"]: column for column in data["columns"]}
    assert columns["id"]["primary_key"] and columns["name"]["not_null"]
    assert data["foreign_keys"] == [
        {"column": "sales_rep_id", "references": "sales_reps.id", "on_delete": "NO ACTION"}
    ]
    assert len(data["sample"]["rows"]) == 3
    assert data["value_lists"]["status"] == ["active", "inactive"]
    assert "name" in data["value_lists"]  # only 3 distinct names in this small base


def test_describe_unknown_table(server: SqlToolboxServer) -> None:
    result = call(server, "describe_table", {"table": "clients; DROP TABLE clients"})
    assert result["isError"]
    assert result["structuredContent"]["code"] == "SQL_ERROR"
    assert "Tables disponibles" in result["structuredContent"]["message"]


def test_run_select(server: SqlToolboxServer) -> None:
    result = call(server, "run_select", {"sql": "SELECT id, name FROM clients ORDER BY id"})
    assert result["structuredContent"] == {
        "columns": ["id", "name"],
        "rows": [[1, "Brasserie Lemaire"], [2, "Atelier Morel"]],
        "row_count": 2,
        "truncated": True,
    }


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM clients", "SQL_FORBIDDEN"),
        ("SELECT 1; DELETE FROM clients", "SQL_FORBIDDEN"),
        ("ATTACH 'x.db' AS x", "SQL_FORBIDDEN"),
        ("SELECT nope FROM clients", "SQL_ERROR"),
        (
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r",
            "TOOL_TIMEOUT",
        ),
    ],
)
def test_run_select_errors(server: SqlToolboxServer, sql: str, code: str) -> None:
    result = call(server, "run_select", {"sql": sql})
    assert result["isError"]
    assert result["structuredContent"]["code"] == code
    assert result["content"][0]["text"] == result["structuredContent"]["message"]
    assert call(server, "run_select", {"sql": "SELECT COUNT(*) FROM clients"})["structuredContent"][
        "rows"
    ] == [[3]]


def test_serve_answers_line_by_line(server: SqlToolboxServer) -> None:
    lines = [
        jsonrpc.encode(jsonrpc.request(1, "ping")),
        b"\n",
        jsonrpc.encode(jsonrpc.notification("notifications/initialized")),
        jsonrpc.encode(jsonrpc.request(2, "tools/list")),
    ]
    stdout = io.BytesIO()
    serve(server, io.BytesIO(b"".join(lines)), stdout)
    answers = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [answer["id"] for answer in answers] == [1, 2]
