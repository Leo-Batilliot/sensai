"""Minimal MCP server over stdio, for tests that need no network nor uvx.

Tools (one per tools/list page, to exercise pagination):
  echo(text)      -> returns text
  fail()          -> tool error (isError: true)
  sleep(seconds)  -> answers after a delay
  crash()         -> the process exits without answering

Options:
  --protocol-version V  answer V to initialize (default: the client's version)
  --ping                send a ping request to the client after initialization
  --banner              print a non JSON line on stdout before starting
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "Return the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "fail",
        "description": "Always fails.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "sleep",
        "description": "Answer after a delay.",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "crash",
        "description": "Exit without answering.",
        "inputSchema": {"type": "object"},
        "annotations": {"destructiveHint": True},
    },
]


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result(request_id: Any, value: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": value})


def error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(request_id: Any, params: dict[str, Any]) -> None:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name == "echo":
        if not isinstance(arguments.get("text"), str):
            error(request_id, -32602, "Invalid params: text is required")
            return
        result(request_id, text_result(arguments["text"]) | {"structuredContent": arguments})
    elif name == "fail":
        result(request_id, text_result("this tool always fails", is_error=True))
    elif name == "sleep":
        time.sleep(float(arguments.get("seconds", 1)))
        result(request_id, text_result("awake"))
    elif name == "crash":
        os._exit(3)
    else:
        result(request_id, text_result(f"Unknown tool: {name}", is_error=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-version")
    parser.add_argument("--ping", action="store_true")
    parser.add_argument("--banner", action="store_true")
    options = parser.parse_args()

    if options.banner:
        print("echo server starting", flush=True)
    print("echo server: logs go to stderr", file=sys.stderr, flush=True)

    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            if method == "notifications/initialized" and options.ping:
                send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
            continue  # notifications get no answer
        if method is None:
            # Answer from the client to our ping: reported on stderr for the test to check
            print(f"client answered {json.dumps(message)}", file=sys.stderr, flush=True)
            continue
        if method == "initialize":
            version = options.protocol_version or params.get("protocolVersion")
            result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "echo-server", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            index = int(params.get("cursor") or 0)
            page: dict[str, Any] = {"tools": [TOOLS[index]]}
            if index + 1 < len(TOOLS):
                page["nextCursor"] = str(index + 1)
            result(request_id, page)
        elif method == "tools/call":
            call_tool(request_id, params)
        elif method == "ping":
            result(request_id, {})
        else:
            error(request_id, -32601, f"Method not found: {method}")


if __name__ == "__main__":
    main()
