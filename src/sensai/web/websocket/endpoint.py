"""WebSocket `/ws/{session_id}` (docs/contracts/websocket.md).

The pieces it wires together:
- session.py: one session per conversation, its history and its running turn;
- messages.py: what each client message does;
- settings.py: the session settings changeable with `set_config`.
"""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from jsonschema import Draft202012Validator

from sensai.web.paths import SCHEMAS_DIR
from sensai.web.shares import ShareStore
from sensai.web.websocket.messages import MessageHandler
from sensai.web.websocket.session import SessionManager

SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
CLOSE_INVALID_SESSION = 4400


def load_client_validator() -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / "ws_client.v1.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def install_websocket(app: FastAPI, manager: SessionManager, shares: ShareStore) -> None:
    validator = load_client_validator()

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        if not SESSION_ID.fullmatch(session_id):
            await websocket.close(code=CLOSE_INVALID_SESSION)
            return
        session, resumed = await manager.open(session_id)
        handler = MessageHandler(session, manager.backend, validator, shares)
        await session.attach(websocket, lambda: handler.session_ready(resumed))
        try:
            while True:
                # The receive loop never runs a turn: `confirm` must stay readable (session.py)
                await handler.handle(await websocket.receive_text())
        except (WebSocketDisconnect, RuntimeError):  # RuntimeError: socket closed by a newer one
            pass
        finally:
            session.detach(websocket)
