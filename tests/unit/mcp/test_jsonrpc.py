import json

import pytest

from sensai.core.errors import SensaiError
from sensai.mcp import jsonrpc
from sensai.mcp.jsonrpc import IdGenerator, JsonRpcError


def test_encode_is_one_line_even_with_newlines_in_values() -> None:
    message = jsonrpc.request(1, "tools/call", {"name": "echo", "arguments": {"text": "a\nb"}})
    line = jsonrpc.encode(message)
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert jsonrpc.decode(line) == message


def test_encode_keeps_accents_readable() -> None:
    line = jsonrpc.encode(jsonrpc.notification("x", {"text": "délai"}))
    assert "délai".encode() in line


def test_request_without_params_has_no_params_member() -> None:
    assert jsonrpc.request(7, "tools/list") == {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}


@pytest.mark.parametrize("line", [b"[1, 2]", b'"text"', b"not json"])
def test_decode_rejects_what_is_not_an_object(line: bytes) -> None:
    with pytest.raises(ValueError):
        jsonrpc.decode(line)


def test_message_kinds() -> None:
    response = jsonrpc.result_response(1, {})
    error = jsonrpc.error_response(1, jsonrpc.INVALID_PARAMS, "bad")
    request = jsonrpc.request(1, "ping")
    notification = jsonrpc.notification("notifications/initialized")
    assert jsonrpc.is_response(response) and jsonrpc.is_response(error)
    assert jsonrpc.is_request(request) and not jsonrpc.is_response(request)
    assert jsonrpc.is_notification(notification) and not jsonrpc.is_request(notification)


def test_error_from_payload() -> None:
    error = JsonRpcError.from_payload({"code": -32602, "message": "Invalid params", "data": "x"})
    assert isinstance(error, SensaiError)
    assert (error.rpc_code, error.rpc_message, error.data) == (-32602, "Invalid params", "x")
    assert "-32602" in error.message


@pytest.mark.parametrize("payload", ["boom", None, {"code": "nan", "message": 3}])
def test_error_from_malformed_payload(payload: object) -> None:
    error = JsonRpcError.from_payload(payload)
    assert error.rpc_code == jsonrpc.INTERNAL_ERROR


def test_ids_are_unique() -> None:
    ids = IdGenerator()
    assert [ids.next() for _ in range(3)] == [1, 2, 3]


def test_error_response_serialises() -> None:
    message = jsonrpc.error_response(None, jsonrpc.PARSE_ERROR, "Parse error")
    assert json.loads(jsonrpc.encode(message))["error"]["code"] == -32700
