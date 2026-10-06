"""Headless engine: runs conversation turns and emits events (docs/contracts/events.md).

The Engine owns the session state (history, model) and is the only component that talks
to Ollama. Adapters (CLI, web, harness) consume the events of run_turn() and make no
decision of their own (PROJECT.md §5.1). Tools (CORE-06), memory (MEM-04) and the judge
(EVAL-05) plug in here later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Protocol

from sensai.core import events
from sensai.core.config import Config
from sensai.core.confirm import (
    AutoRejectBroker,
    ConfirmationBroker,
    Decision,
    PendingAction,
)
from sensai.core.errors import SensaiError
from sensai.core.history import History
from sensai.llm.ollama_client import Chunk, Message, ModelDetails, OllamaClient, Usage

logger = logging.getLogger(__name__)

BUSY_MESSAGE = "Une réponse est déjà en cours. Attendez la fin ou interrompez-la."
INTERNAL_MESSAGE = (
    "Erreur inattendue du moteur (le détail est dans les journaux). "
    "Recommencez la conversation avec /reset."
)


class ChatClient(Protocol):
    """What the Engine needs from OllamaClient (tests use a scripted fake)."""

    def chat_stream(
        self,
        model: str,
        messages: list[Message],
        *,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Chunk, None]: ...

    async def show(self, model: str) -> ModelDetails: ...

    async def list_models(self) -> list[str]: ...

    async def aclose(self) -> None: ...


def new_id() -> str:
    """Opaque identifier for sessions, turns, calls and actions (events.md §1)."""
    return str(uuid.uuid4())


class Engine:
    """One conversation session. Not thread-safe: use it from a single event loop."""

    def __init__(
        self,
        config: Config,
        client: ChatClient | None = None,
        *,
        broker: ConfirmationBroker | None = None,
        session_id: str | None = None,
    ) -> None:
        self.config = config
        self.client: ChatClient = (
            client
            if client is not None
            else OllamaClient(
                config.ollama.host,
                timeout_s=config.ollama.timeout_s,
                keep_alive=config.ollama.keep_alive,
            )
        )
        self.broker: ConfirmationBroker = broker if broker is not None else AutoRejectBroker()
        self.session_id = session_id or new_id()
        self.model = config.ollama.model
        self.history = History()
        self._running = False
        self._cancel_requested = False
        # Bumped by reset(): a turn that was running during a reset is not recorded
        self._epoch = 0

    async def aclose(self) -> None:
        await self.client.aclose()

    @property
    def busy(self) -> bool:
        return self._running

    def chat_options(self) -> dict[str, Any]:
        """Ollama options of every chat request, from the configuration."""
        return {
            "temperature": self.config.agent.temperature,
            "num_ctx": self.config.ollama.num_ctx,
        }

    async def model_details(self, model: str | None = None) -> ModelDetails:
        """Capabilities of a model (the current one by default). Raises ModelNotFound."""
        return await self.client.show(model or self.model)

    async def list_models(self) -> list[str]:
        return await self.client.list_models()

    def set_model(self, model: str) -> None:
        """Model of the next turns; a running turn keeps its own. Check it with model_details()."""
        self.model = model

    def reset(self) -> None:
        """Empty the history. A running turn will not be recorded (cancel it first)."""
        self.history.clear()
        self._epoch += 1

    def cancel(self) -> bool:
        """Stop the running turn at its next chunk; it ends with message_done(interrupted).

        Returns False when no turn is running: the request is then ignored. Never cancel
        the task that consumes run_turn() instead: it could not emit message_done.
        """
        if not self._running:
            return False
        self._cancel_requested = True
        return True

    async def run_turn(self, user_message: str) -> AsyncGenerator[events.Event, None]:
        """One conversation turn: turn_started, thinking/token*, then message_done or error.

        Consumers should close the generator with contextlib.aclosing: abandoned midway,
        it releases the session only when garbage-collected.
        """
        if self._running:
            yield self._error(None, "BUSY", BUSY_MESSAGE, recoverable=True)
            return
        self._running = True
        self._cancel_requested = False
        turn_id = new_id()
        model, epoch = self.model, self._epoch
        started = time.monotonic()
        try:
            yield events.TurnStarted(
                session_id=self.session_id,
                turn_id=turn_id,
                origin="user",
                user_message=user_message,
            )
            parts: list[str] = []
            usage: Usage | None = None
            interrupted = False
            try:
                stream = self.client.chat_stream(
                    model,
                    self.history.messages_for(user_message),
                    options=self.chat_options(),
                )
                # aclosing: leaving early closes the HTTP response, which stops Ollama
                async with contextlib.aclosing(stream) as chunks:
                    async for chunk in chunks:
                        if self._cancel_requested:
                            interrupted = True
                            break
                        if chunk.thinking:
                            yield events.Thinking(
                                session_id=self.session_id,
                                turn_id=turn_id,
                                delta=chunk.thinking,
                            )
                        if chunk.content:
                            parts.append(chunk.content)
                            yield events.Token(
                                session_id=self.session_id,
                                turn_id=turn_id,
                                delta=chunk.content,
                            )
                        if chunk.usage is not None:
                            usage = chunk.usage
            except SensaiError as exc:
                yield self._error(turn_id, exc.code, exc.message, recoverable=exc.recoverable)
                return
            except Exception:
                logger.exception("Turn %s failed", turn_id)
                yield self._error(turn_id, "INTERNAL", INTERNAL_MESSAGE, recoverable=False)
                return

            content = "".join(parts)
            if not interrupted and epoch == self._epoch:
                self.history.commit(user_message, content)
            yield events.MessageDone(
                session_id=self.session_id,
                turn_id=turn_id,
                content=content,
                interrupted=interrupted,
                # Ollama only counts tokens on the final chunk, which never comes after a cancel
                usage=None if interrupted else self._context_usage(usage),
                duration_ms=_elapsed_ms(started),
            )
        finally:
            self._running = False
            self._cancel_requested = False

    async def _confirm(self, action: PendingAction) -> Decision:
        """HITL pause: wait for the broker's decision, at most action.expires_in_s.

        The caller emits confirmation_required before and confirmation_resolved after
        (CORE-06, HITL-02). No answer in time is a refusal: Decision("timeout").
        """
        try:
            return await asyncio.wait_for(self.broker.request(action), action.expires_in_s)
        except asyncio.TimeoutError:  # distinct from the builtin TimeoutError in 3.10
            return Decision("timeout", "timeout")

    def _context_usage(self, usage: Usage | None) -> events.ContextUsage | None:
        if usage is None:
            return None
        return events.ContextUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            ctx_used=usage.prompt_tokens + usage.completion_tokens,
            ctx_max=self.config.ollama.num_ctx,
        )

    def _error(
        self, turn_id: str | None, code: str, message: str, *, recoverable: bool
    ) -> events.Error:
        return events.Error(
            session_id=self.session_id,
            turn_id=turn_id,
            code=code,
            message=message,
            recoverable=recoverable,
        )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
