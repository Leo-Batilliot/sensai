"""Client messages of one connection: what each one does (websocket.md §4 and §6.1)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from jsonschema import Draft202012Validator

from sensai import __version__
from sensai.web.engines.backend import ModelInfo, WebBackend
from sensai.web.engines.note import NOTE_ACTIONS
from sensai.web.scheduler import Scheduler, ScheduleRefused
from sensai.web.shares import ShareStore
from sensai.web.websocket.session import Session
from sensai.web.websocket.settings import invalid_session_patch

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
RESET_WAIT_S = 5.0
# The memory panel, and the Note panel (memory.md §7.5): document_delete_section is confirmed
UI_ACTIONS = frozenset({"memory_update", "memory_delete", *NOTE_ACTIONS})


def protocol_error(
    session_id: str, code: str, message: str, reply_to: str | None
) -> dict[str, Any]:
    """Error emitted by the web layer, outside any turn (websocket.md §7.1)."""
    payload: dict[str, Any] = {
        "type": "error",
        "session_id": session_id,
        "turn_id": None,
        "ts": time.time(),
        "code": code,
        "message": message,
        "recoverable": True,
    }
    if reply_to is not None:
        payload["reply_to"] = reply_to
    return payload


class MessageHandler:
    def __init__(
        self,
        session: Session,
        backend: WebBackend,
        validator: Draft202012Validator,
        shares: ShareStore,
        scheduler: Scheduler,
    ) -> None:
        self.session = session
        self.backend = backend
        self.validator = validator
        self.shares = shares
        self.scheduler = scheduler

    async def session_ready(self, resumed: bool) -> dict[str, Any]:
        engine = self.session.engine
        servers, tools = engine.describe()
        return {
            "type": "session_ready",
            "session_id": self.session.id,
            "ts": time.time(),
            "protocol_version": PROTOCOL_VERSION,
            "server_version": __version__,
            "resumed": resumed,
            "history": list(self.session.history) if resumed else [],
            "model": engine.model,
            "supports_tools": await self._supports_tools(engine.model),
            "ctx_max": engine.session_config["num_ctx"],
            "config": dict(engine.session_config),
            "servers": servers,
            "tools": tools,
            "persona": engine.persona,
            "personas": [
                {"id": p["id"], "name": p["name"], "role": p["role"]}
                for p in self.backend.personas()[1]
            ],
            "document_id": self.backend.documents().doc_id,  # the committee note (M4)
        }

    async def handle(self, text: str) -> None:
        """Parse and check one message, then call the matching `_on_<type>` method."""
        try:
            message = json.loads(text)
        except ValueError:
            await self._error("INVALID_MESSAGE", "Message illisible : JSON invalide.", None)
            return
        if not isinstance(message, dict):
            await self._error("INVALID_MESSAGE", "Message illisible : objet JSON attendu.", None)
            return
        reply_to = message.get("id") if isinstance(message.get("id"), str) else None
        problem = self._invalid(message)
        if problem is not None:
            await self._error("INVALID_MESSAGE", problem, reply_to)
            return
        handler = getattr(self, f"_on_{message['type']}", None)
        if handler is None:  # a v1.1 message this server does not handle yet (§4)
            await self._error(
                "INVALID_MESSAGE",
                f"Le message « {message['type']} » n'est pas encore pris en charge par ce serveur.",
                reply_to,
            )
            return
        await handler(message, reply_to)

    def _invalid(self, message: dict[str, Any]) -> str | None:
        content = message.get("content")
        if message.get("type") == "user_message" and isinstance(content, str):
            if not content.strip():
                return "Le message est vide."
            if len(content) > 4000:
                return "Le message dépasse 4 000 caractères."
        error = next(iter(sorted(self.validator.iter_errors(message), key=str)), None)
        return None if error is None else f"Message invalide : {error.message}"

    # --- one method per message type ------------------------------------------------------

    async def _on_user_message(self, message: dict[str, Any], reply_to: str | None) -> None:
        if await self._busy(reply_to):
            return
        self.session.start_turn(self.session.engine.run_turn(message["content"]))

    async def _on_ui_action(self, message: dict[str, Any], reply_to: str | None) -> None:
        tool = message["tool"]
        if tool not in UI_ACTIONS:
            await self._error(
                "UI_ACTION_FORBIDDEN",
                f"L'outil « {tool} » ne peut pas être lancé depuis l'interface.",
                reply_to,
            )
            return
        if await self._busy(reply_to):
            return
        self.session.start_turn(self.session.engine.run_ui_action(tool, message["arguments"]))

    async def _on_confirm(self, message: dict[str, Any], reply_to: str | None) -> None:
        resolved = self.session.broker.resolve(
            message["action_id"], message["decision"], message.get("comment")
        )
        if not resolved:
            await self._error(
                "UNKNOWN_ACTION",
                "Cette demande de confirmation n'est plus active (déjà traitée ou expirée).",
                reply_to,
            )

    async def _on_answer(self, message: dict[str, Any], reply_to: str | None) -> None:
        refusal = self.session.questions.resolve(
            message["question_id"], message.get("option_id"), message.get("text")
        )
        if refusal is not None:
            await self._error(refusal.code, refusal.message, reply_to)

    async def _on_cancel(self, message: dict[str, Any], reply_to: str | None) -> None:
        self.session.interrupt("cancelled")  # ignored without a running turn (§4.4)

    async def _on_reset(self, message: dict[str, Any], reply_to: str | None) -> None:
        session = self.session
        session.interrupt("cancelled")
        try:
            await asyncio.wait_for(session.turn_done.wait(), RESET_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning("Le tour n'a pas fini avant le reset (session %s)", session.id)
        session.engine.reset()
        session.clear_history()
        await session.send(self._ack("session_reset", reply_to))

    async def _on_set_model(self, message: dict[str, Any], reply_to: str | None) -> None:
        model = await self._find_model(message["model"], reply_to)
        if model is None:
            return
        engine = self.session.engine
        engine.set_model(model["name"])
        await self.session.send(
            self._ack(
                "model_changed",
                reply_to,
                model=model["name"],
                supports_tools=model["supports_tools"],
                ctx_max=engine.session_config["num_ctx"],
            )
        )

    async def _on_set_config(self, message: dict[str, Any], reply_to: str | None) -> None:
        patch: dict[str, Any] = message["patch"]
        problem = invalid_session_patch(patch)
        if problem is not None:
            await self._error("INVALID_CONFIG", problem, reply_to)
            return
        judge_model = patch.get("judge_model")
        if judge_model is not None and await self._find_model(judge_model, reply_to) is None:
            return
        engine = self.session.engine
        changed = [key for key, value in patch.items() if engine.session_config.get(key) != value]
        engine.set_config(patch)
        await self.session.send(
            self._ack(
                "config_updated", reply_to, config=dict(engine.session_config), changed=changed
            )
        )

    async def _on_set_persona(self, message: dict[str, Any], reply_to: str | None) -> None:
        engine = self.session.engine
        previous = engine.persona
        if not engine.set_persona(message["persona"]):
            await self._error(
                "PERSONA_NOT_FOUND", f"Le profil « {message['persona']} » n'existe pas.", reply_to
            )
            return
        _, tools = engine.describe()
        changed = self._ack("persona_changed", reply_to, persona=engine.persona, tools=tools)
        if engine.persona != previous:  # the same profile again: acknowledged, no separator
            self.session.remember(changed)  # the separator survives a reload (§5.2, V-7)
        await self.session.send(changed)

    async def _on_share(self, message: dict[str, Any], reply_to: str | None) -> None:
        websocket = self.session.websocket
        host = websocket.headers.get("host") if websocket is not None else None
        document = self.backend.documents().to_dict()
        share = self.shares.create(snapshot(self.session, document), host)
        await self.session.send(self._ack("share_created", reply_to, **share))

    async def _on_unshare(self, message: dict[str, Any], reply_to: str | None) -> None:
        token = message["token"]
        if not self.shares.revoke(token):
            await self._error(
                "SHARE_NOT_FOUND",
                "Ce lien de partage n'existe pas ou a déjà été révoqué.",
                reply_to,
            )
            return
        await self.session.send(self._ack("share_revoked", reply_to, token=token))

    async def _on_run_schedule(self, message: dict[str, Any], reply_to: str | None) -> None:
        # The run has its own engine: the session stays free for user_message (§4.11)
        try:
            await self.scheduler.run(message["schedule_id"], "manual", self.session.id, reply_to)
        except ScheduleRefused as refused:
            await self._error(refused.code, refused.message, reply_to)

    # --- helpers --------------------------------------------------------------------------

    async def _busy(self, reply_to: str | None) -> bool:
        if not self.session.turn_active:
            return False
        await self._error(
            "BUSY", "Une réponse est déjà en cours. Attendez la fin ou interrompez-la.", reply_to
        )
        return True

    async def _find_model(self, name: str, reply_to: str | None) -> ModelInfo | None:
        model = next((m for m in await self.backend.list_models() if m["name"] == name), None)
        if model is None:
            await self._error(
                "MODEL_NOT_FOUND",
                f"Le modèle « {name} » n'est pas installé : lancez `ollama pull {name}`.",
                reply_to,
            )
        return model

    async def _supports_tools(self, name: str) -> bool:
        models = await self.backend.list_models()
        return next((m["supports_tools"] for m in models if m["name"] == name), False)

    async def _error(self, code: str, message: str, reply_to: str | None) -> None:
        await self.session.send(protocol_error(self.session.id, code, message, reply_to))

    def _ack(self, kind: str, reply_to: str | None, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": kind, "session_id": self.session.id, "ts": time.time()}
        if reply_to is not None:
            payload["reply_to"] = reply_to
        payload.update(fields)
        return payload


def snapshot(session: Session, document: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a share freezes and an export writes: the finished turns, and the committee
    note at its current revision for a share (websocket.md §4.10)."""
    engine = session.engine
    return {
        "model": engine.model,
        "persona": engine.persona,
        "events": session.completed_history(),
        "document": document,
    }
