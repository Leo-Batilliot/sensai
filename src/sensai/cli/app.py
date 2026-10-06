"""CLI chat: the base loop of the subject (B1, PROJECT.md §6.1).

Streaming, history within the session, clean error handling, and a model chosen by
argument or configuration. CORE-05 will turn this module into a thin adapter of the
Engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import AsyncGenerator, Callable, Coroutine
from types import FrameType
from typing import Any, Protocol, TypeVar

from sensai.cli.render import Renderer
from sensai.core.config import Config, ConfigError, load_config
from sensai.core.errors import ModelNotFound, SensaiError, StreamInterrupted
from sensai.core.history import History
from sensai.llm.ollama_client import Chunk, Message, ModelDetails, OllamaClient

T = TypeVar("T")

PROMPT = "> "

HELP = """Commandes :
  /help          cette aide
  /models        modèles installés dans Ollama
  /model <nom>   change de modèle (l'historique est conservé)
  /reset         vide l'historique
  /tools         outils disponibles
  /memory        mémoire de l'agent
  /exit          quitte (ou Ctrl+D)
Ctrl+C pendant une réponse l'interrompt sans quitter."""

# Commands declared now, implemented by later tasks
NOT_YET_AVAILABLE = {"/tools": "MCP-04", "/memory": "MEM-03"}

NOT_KEPT = "Elle n'est pas conservée dans l'historique."


class ChatClient(Protocol):
    """What the chat needs from OllamaClient (tests use a scripted fake)."""

    def chat_stream(
        self, model: str, messages: list[Message], *, options: dict[str, Any] | None = None
    ) -> AsyncGenerator[Chunk, None]: ...

    async def show(self, model: str) -> ModelDetails: ...

    async def list_models(self) -> list[str]: ...

    async def aclose(self) -> None: ...


class ChatSession:
    """One CLI conversation: the current model, its history and the Ollama client."""

    def __init__(
        self,
        client: ChatClient,
        model: str,
        renderer: Renderer,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.render = renderer
        self.options = options
        self.history = History()

    async def check_model(self) -> bool:
        """Startup check. False when the model is not installed: the chat cannot start."""
        try:
            details = await self.client.show(self.model)
        except ModelNotFound as exc:
            self.render.error(exc.message)
            with contextlib.suppress(SensaiError):
                await self._show_models()
            return False
        except SensaiError as exc:
            # Ollama down or slow: the session stays open and the next message retries
            self.render.error(exc.message)
            return True
        self._warn_if_no_tools(details)
        return True

    async def handle(self, line: str) -> bool:
        """Process one input line. False when the user asked to quit."""
        text = line.strip()
        if not text:
            self.render.hint("Entrée vide, rien n'est envoyé.")
            return True
        if not text.startswith("/"):
            await self.ask(text)
            return True
        try:
            return await self._command(text)
        except SensaiError as exc:
            self.render.error(exc.message)
            return True

    async def ask(self, question: str) -> None:
        """One turn: stream the answer, then record the exchange if it is complete."""
        parts: list[str] = []
        try:
            await self._stream_answer(question, parts)
        except asyncio.CancelledError:
            self.render.warning(f"Réponse interrompue. {NOT_KEPT}")
            raise
        except StreamInterrupted as exc:
            self.render.warning(f"{exc.message} {NOT_KEPT}")
            return
        except SensaiError as exc:
            self.render.error(exc.message)
            return
        self.history.commit(question, "".join(parts))

    async def _stream_answer(self, question: str, parts: list[str]) -> None:
        messages = self.history.messages_for(question)
        stream = self.client.chat_stream(self.model, messages, options=self.options)
        try:
            # aclosing: on cancel, the generator closes its HTTP response, which stops Ollama
            async with contextlib.aclosing(stream) as chunks:
                async for chunk in chunks:
                    if chunk.thinking:
                        self.render.thinking()
                    if chunk.content:
                        parts.append(chunk.content)
                        self.render.delta(chunk.content)
        finally:
            self.render.end_answer()

    async def _command(self, text: str) -> bool:
        name, _, argument = text.partition(" ")
        if name == "/exit":
            return False
        if name == "/help":
            self.render.info(HELP)
        elif name == "/reset":
            self.history.clear()
            self.render.info("Historique vidé.")
        elif name == "/models":
            await self._show_models()
        elif name == "/model":
            await self._switch_model(argument.strip())
        elif name in NOT_YET_AVAILABLE:
            self.render.hint(f"Disponible après {NOT_YET_AVAILABLE[name]}.")
        else:
            self.render.warning(f"Commande inconnue : {name}. Tapez /help.")
        return True

    async def _switch_model(self, name: str) -> None:
        if not name:
            self.render.info(f"Modèle actuel : {self.model}. Pour en changer : /model <nom>")
            return
        try:
            details = await self.client.show(name)
        except ModelNotFound as exc:
            self.render.error(exc.message)
            await self._show_models()
            return
        self.model = name
        self.render.info(f"Modèle : {name} (historique conservé).")
        self._warn_if_no_tools(details)

    async def _show_models(self) -> None:
        models = await self.client.list_models()
        if not models:
            self.render.info("Aucun modèle installé (ollama pull <nom>).")
            return
        lines = [f"{'*' if name == self.model else ' '} {name}" for name in models]
        self.render.info("Modèles installés :\n" + "\n".join(lines))

    def _warn_if_no_tools(self, details: ModelDetails) -> None:
        if not details.supports_tools:
            self.render.warning(
                f"Le modèle {details.name} ne gère pas les outils : "
                "les questions sur les données ne fonctionneront pas avec lui."
            )


def run_interruptible(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, T]) -> T:
    """Run coro until done; Ctrl+C cancels it (CancelledError) instead of killing the app.

    Like asyncio.run in 3.11+, the SIGINT handler cancels the task, so the interruption
    lands on an await, never in the middle of a statement. signal.signal also works on
    Windows, unlike loop.add_signal_handler. A second Ctrl+C raises KeyboardInterrupt.
    """
    task = loop.create_task(coro)
    interrupted = False

    def on_sigint(signum: int, frame: FrameType | None) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        loop.call_soon_threadsafe(task.cancel)

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        return loop.run_until_complete(task)
    finally:
        signal.signal(signal.SIGINT, previous)


def run_repl(
    session: ChatSession,
    loop: asyncio.AbstractEventLoop,
    read_line: Callable[[str], str] = input,
) -> None:
    """Read and process lines until /exit, Ctrl+D, or Ctrl+C at the prompt."""
    while True:
        try:
            line = read_line(PROMPT)
        except (EOFError, KeyboardInterrupt):
            session.render.info("")  # the shell prompt must not follow "> " on the same line
            return
        try:
            keep_going = run_interruptible(loop, session.handle(line))
        except asyncio.CancelledError:
            continue  # Ctrl+C during a turn: back to the prompt
        if not keep_going:
            return


def chat_options(config: Config) -> dict[str, Any]:
    """Ollama options of every chat request, from the configuration."""
    return {"temperature": config.agent.temperature, "num_ctx": config.ollama.num_ctx}


def run_chat(
    config_path: str | None = None,
    model: str | None = None,
    host: str | None = None,
    *,
    read_line: Callable[[str], str] = input,
) -> int:
    """`sensai chat`. Returns the exit code: 0, 1 if the chat cannot start, 130 on Ctrl+C."""
    try:
        config = load_config(config_path, overrides={"ollama.model": model, "ollama.host": host})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    renderer = Renderer()
    client = OllamaClient(
        config.ollama.host,
        timeout_s=config.ollama.timeout_s,
        keep_alive=config.ollama.keep_alive,
    )
    session = ChatSession(client, config.ollama.model, renderer, chat_options(config))
    loop = asyncio.new_event_loop()
    try:
        renderer.hint(
            f"SENSAI · {config.ollama.model} sur {config.ollama.host} · "
            "/help pour l'aide, /exit pour quitter"
        )
        if not run_interruptible(loop, session.check_model()):
            return 1
        run_repl(session, loop, read_line)
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        renderer.info("")
        return 130
    finally:
        loop.run_until_complete(client.aclose())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
