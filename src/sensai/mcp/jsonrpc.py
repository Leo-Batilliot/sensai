"""JSON-RPC 2.0 messages as used by MCP.

Over stdio, one message is one line of UTF-8 JSON: `encode` never produces a newline
inside a message (json.dumps escapes them in strings).
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from sensai.core.errors import SensaiError

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

Message = dict[str, Any]
RequestId = int | str


class JsonRpcError(SensaiError):
    """An `error` answer from the server: a protocol failure, not a tool failure.

    A tool that fails while running answers with a result whose `isError` is true;
    that result goes back to the model and is not raised as this exception.
    """

    code = "MCP_RPC_ERROR"

    def __init__(self, rpc_code: int, rpc_message: str, data: Any = None) -> None:
        super().__init__(f"Erreur JSON-RPC {rpc_code} : {rpc_message}")
        self.rpc_code = rpc_code
        self.rpc_message = rpc_message
        self.data = data

    @classmethod
    def from_payload(cls, error: Any) -> JsonRpcError:
        """Build the exception from the `error` member of a response, even a malformed one."""
        if not isinstance(error, dict):
            return cls(INTERNAL_ERROR, f"erreur illisible : {error!r}")
        rpc_code = error.get("code")
        return cls(
            rpc_code if isinstance(rpc_code, int) else INTERNAL_ERROR,
            str(error.get("message", "")),
            error.get("data"),
        )


class IdGenerator:
    """Request ids, unique for the lifetime of one connection."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def next(self) -> int:
        return next(self._counter)


def request(request_id: RequestId, method: str, params: dict[str, Any] | None = None) -> Message:
    message: Message = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any] | None = None) -> Message:
    message: Message = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        message["params"] = params
    return message


def result_response(request_id: RequestId, result: dict[str, Any]) -> Message:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(
    request_id: RequestId | None, rpc_code: int, rpc_message: str, data: Any = None
) -> Message:
    error: dict[str, Any] = {"code": rpc_code, "message": rpc_message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def encode(message: Message) -> bytes:
    """Serialize a message as one line, newline included."""
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def decode(line: bytes) -> Message:
    """Parse one line. Raises ValueError if it is not a JSON object."""
    message = json.loads(line)
    if not isinstance(message, dict):
        raise ValueError(f"not a JSON-RPC message: {line[:200]!r}")
    return message


def is_response(message: Message) -> bool:
    return (
        "id" in message and "method" not in message and ("result" in message or "error" in message)
    )


def is_request(message: Message) -> bool:
    return "id" in message and "method" in message


def is_notification(message: Message) -> bool:
    return "id" not in message and "method" in message
