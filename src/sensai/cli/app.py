"""CLI chat: a thin adapter of the Engine (B1, PROJECT.md §6.1; CORE-05).

It reads lines, turns commands into Engine calls and displays the events of each turn.
History, model, errors and cancellation belong to the Engine
(docs/contracts/events.md): the CLI never talks to Ollama itself.

The engine comes from the same assembly as the web (core/app_factory.py, CORE-10): MCP
tools, HITL, memory and judge. Without the demo database it is a plain chat (B1).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Callable, Coroutine
from types import FrameType
from typing import Any, TypeVar

from sensai.cli.confirm import CliConfirmationBroker
from sensai.cli.render import Renderer
from sensai.core import events
from sensai.core.app_factory import Services
from sensai.core.config import Config, ConfigError, load_config
from sensai.core.engine import Engine
from sensai.core.errors import ModelNotFound, SensaiError
from sensai.llm.ollama_client import ModelDetails
from sensai.tools.policy import evaluate

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

NOT_KEPT = "Elle n'est pas conservée dans l'historique."
NO_TOOLS = "Aucun outil : la base de démonstration est absente (lancez `sensai seed`)."

# Badge of the judge (events.md §3.11) -> label and renderer method
BADGES = {
    "verified": ("Vérifiée", "info"),
    "needs_review": ("À vérifier", "warning"),
    "contradicted": ("Contredite", "error"),
    "unavailable": ("Vérification indisponible", "hint"),
}

ServicesFactory = Callable[[Config], Services]


class ChatSession:
    """One CLI conversation: an Engine and the terminal it is displayed on."""

    def __init__(
        self, engine: Engine, renderer: Renderer, broker: CliConfirmationBroker | None = None
    ) -> None:
        self.engine = engine
        self.render = renderer
        self.broker = broker

    def interrupt(self) -> bool:
        """Ctrl+C during a turn: refuse a pending confirmation, else stop the answer."""
        if self.broker is not None and self.broker.interrupt():
            return True
        return self.engine.cancel()

    async def check_model(self) -> bool:
        """Startup check. False when the model is not installed: the chat cannot start."""
        try:
            details = await self.engine.model_details()
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
        """One turn: display the events of the Engine as they come."""
        async with contextlib.aclosing(self.engine.run_turn(question)) as turn:
            async for event in turn:
                self._show(event)

    def _show(self, event: events.Event) -> None:
        if isinstance(event, events.Thinking):
            self.render.thinking()
        elif isinstance(event, events.Token):
            self.render.delta(event.delta)
        elif isinstance(event, events.MessageDone):
            self.render.end_answer()
            if event.interrupted:
                self.render.warning(f"Réponse interrompue. {NOT_KEPT}")
        elif isinstance(event, events.Error):
            self.render.end_answer()
            if event.code == "STREAM_INTERRUPTED":
                self.render.warning(f"{event.message} {NOT_KEPT}")
            else:
                self.render.error(event.message)
        # Tool steps, as a timeline (plain characters: they must exist in cp1252)
        elif isinstance(event, events.ToolCall):
            self.render.hint(f"• {event.tool}")
        elif isinstance(event, events.SqlGenerated):
            self.render.hint(f"  {event.operation} : {' '.join(event.sql.split())}")
        elif isinstance(event, events.ToolResult):
            if event.error is None:
                self.render.hint(f"  résultat : {event.summary}")
            else:
                self.render.warning(f"  échec : {event.error.message}")
        elif isinstance(event, events.MemoryUpdated):
            self.render.hint(f"  mémoire : {event.summary}")
        elif isinstance(event, events.ConfirmationRequired):
            self.render.confirmation(event.tool, event.sql, event.impact, event.expires_in_s)
        elif isinstance(event, events.ConfirmationResolved):
            if event.decision == "approve":
                self.render.info("  confirmé : exécution")
            else:
                self.render.warning("  refusé : rien n'est exécuté")
        elif isinstance(event, events.ContextInjected):
            labels = ", ".join(ref.label for ref in event.refs)
            self.render.hint(f"  contexte mémoire fourni : {labels}")
        elif isinstance(event, events.JudgeStarted):
            self.render.hint("(vérification de la réponse…)")
        elif isinstance(event, events.JudgeReport):
            self._show_verdict(event)

    def _show_verdict(self, report: events.JudgeReport) -> None:
        label, method = BADGES[report.badge]
        unmatched = report.numeric_check.get("unmatched") or []
        detail = f" : chiffres absents des données : {', '.join(unmatched)}" if unmatched else ""
        getattr(self.render, method)(f"Vérification : {label}{detail}")
        claims = report.report["claims"] if report.report else []
        for claim in claims:
            if claim["verdict"] == "contradicted":
                expected = claim.get("expected_value")
                hint = f" (attendu : {expected})" if expected else ""
                self.render.warning(f"  « {claim['text']} »{hint}")

    async def _command(self, text: str) -> bool:
        name, _, argument = text.partition(" ")
        if name == "/exit":
            return False
        if name == "/help":
            self.render.info(HELP)
        elif name == "/reset":
            self.engine.reset()
            self.render.info("Historique vidé.")
        elif name == "/models":
            await self._show_models()
        elif name == "/model":
            await self._switch_model(argument.strip())
        elif name == "/tools":
            self.show_tools()
        elif name == "/memory":
            self._show_memory()
        else:
            self.render.warning(f"Commande inconnue : {name}. Tapez /help.")
        return True

    async def _switch_model(self, name: str) -> None:
        if not name:
            self.render.info(f"Modèle actuel : {self.engine.model}. Pour en changer : /model <nom>")
            return
        try:
            details = await self.engine.model_details(name)
        except ModelNotFound as exc:
            self.render.error(exc.message)
            await self._show_models()
            return
        self.engine.set_model(name)
        self.render.info(f"Modèle : {name} (historique conservé).")
        self._warn_if_no_tools(details)

    async def _show_models(self) -> None:
        models = await self.engine.list_models()
        if not models:
            self.render.info("Aucun modèle installé (ollama pull <nom>).")
            return
        lines = [f"{'*' if name == self.engine.model else ' '} {name}" for name in models]
        self.render.info("Modèles installés :\n" + "\n".join(lines))

    def show_servers(self) -> None:
        """State of each MCP server: shown at startup and by /tools."""
        registry = self.engine.registry
        if registry is None:
            self.render.hint(NO_TOOLS)
            return
        for server in registry.servers():
            if server.status == "up":
                self.render.hint(f"Serveur MCP {server.name} : en ligne")
            else:
                self.render.warning(f"Serveur MCP {server.name} : indisponible ({server.error})")

    def show_tools(self) -> None:
        self.show_servers()
        registry = self.engine.registry
        if registry is None:
            return
        hitl = self.engine.config.hitl
        lines = []
        for info in registry.tools():
            # The decision without arguments (tools.md §5): run_select shows no confirmation
            confirm = evaluate(info, {}, hitl).action == "confirm"
            lines.append(f"  {info.name}{' (confirmation)' if confirm else ''}")
        self.render.info("Outils :\n" + "\n".join(lines))

    def _show_memory(self) -> None:
        if self.engine.memory is None:
            self.render.hint("Aucune mémoire : la base de démonstration est absente.")
            return
        content = self.engine.memory.store.dump()
        glossary = content["glossary"]
        lines = [f"Glossaire ({len(glossary)}) :"]
        lines += [f"  {row['term']} : {row['definition']}" for row in glossary]
        lines.append(
            f"Entités : {len(content['entities'])} · relations : {len(content['relations'])}"
            f" · faits : {len(content['facts'])}"
        )
        self.render.info("\n".join(lines))

    def _warn_if_no_tools(self, details: ModelDetails) -> None:
        if not details.supports_tools:
            self.render.warning(
                f"Le modèle {details.name} ne gère pas les outils : "
                "les questions sur les données ne fonctionneront pas avec lui."
            )


def run_interruptible(
    loop: asyncio.AbstractEventLoop,
    coro: Coroutine[Any, Any, T],
    on_interrupt: Callable[[], bool] | None = None,
) -> T:
    """Run coro until done; Ctrl+C interrupts it instead of killing the app.

    The first Ctrl+C calls on_interrupt (Engine.cancel: the turn ends cleanly with
    message_done(interrupted)). When it returns False, as when no turn is running, the
    task is cancelled instead (CancelledError). The SIGINT handler only schedules this on
    the loop, so it never runs in the middle of a statement; signal.signal also works on
    Windows, unlike loop.add_signal_handler. A second Ctrl+C raises KeyboardInterrupt.
    """
    task = loop.create_task(coro)
    interrupted = False

    def interrupt() -> None:
        if on_interrupt is None or not on_interrupt():
            task.cancel()

    def on_sigint(signum: int, frame: FrameType | None) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        loop.call_soon_threadsafe(interrupt)

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
            keep_going = run_interruptible(loop, session.handle(line), session.interrupt)
        except asyncio.CancelledError:
            continue  # Ctrl+C during a command: back to the prompt
        if not keep_going:
            return


def run_chat(
    config_path: str | None = None,
    model: str | None = None,
    host: str | None = None,
    *,
    read_line: Callable[[str], str] = input,
    services_factory: ServicesFactory = Services,
) -> int:
    """`sensai chat`. Returns the exit code: 0, 1 if the chat cannot start, 130 on Ctrl+C."""
    try:
        config = load_config(config_path, overrides={"ollama.model": model, "ollama.host": host})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    renderer = Renderer()
    broker = CliConfirmationBroker(renderer, read_line)
    loop = asyncio.new_event_loop()
    services: Services | None = None
    engine: Engine | None = None
    try:
        renderer.hint(
            f"SENSAI · {config.ollama.model} sur {config.ollama.host} · "
            "/help pour l'aide, /exit pour quitter"
        )
        business_db = config.database.business_path
        if business_db.is_file():
            services = services_factory(config)
            renderer.hint("Démarrage des serveurs MCP…")
            run_interruptible(loop, services.start())
            engine = services.new_engine(broker)
        else:
            renderer.warning(
                f"Base de démonstration absente ({business_db}) : lancez `sensai seed`. "
                "Chat sans outils, sans mémoire et sans vérification."
            )
            engine = Engine(config, broker=broker)
        session = ChatSession(engine, renderer, broker)
        if services is not None:
            session.show_servers()
        if not run_interruptible(loop, session.check_model()):
            return 1
        run_repl(session, loop, read_line)
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        renderer.info("")
        return 130
    finally:
        # Shut down in reverse order: the session's Ollama client, then the MCP servers
        if engine is not None:
            loop.run_until_complete(engine.aclose())
        if services is not None:
            loop.run_until_complete(services.stop())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
