"""CLI chat tests: an Engine on a fake Ollama client that streams scripted chunks."""

import asyncio
import io
import signal
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from rich.console import Console

from fakes import CTRL_C, HANG, MODEL, OTHER_MODEL, FakeClient, answer
from sensai.cli.app import ChatSession, run_chat, run_interruptible, run_repl
from sensai.cli.render import Renderer
from sensai.core import engine as engine_module
from sensai.core.config import Config
from sensai.core.engine import Engine
from sensai.core.errors import OllamaUnavailable, StreamInterrupted
from sensai.llm.ollama_client import Chunk, Message


def make_session(client: FakeClient) -> tuple[ChatSession, io.StringIO]:
    out = io.StringIO()
    console = Console(file=out, width=100, color_system=None, highlight=False)
    return ChatSession(Engine(Config(), client, session_id="s-1"), Renderer(console)), out


def scripted(*lines: str) -> Callable[[str], str]:
    """read_line replacement: returns the lines in order, then behaves like Ctrl+D."""
    pending = list(lines)

    def read_line(prompt: str) -> str:
        if not pending:
            raise EOFError
        return pending.pop(0)

    return read_line


def exchange(question: str, reply: str) -> list[Message]:
    return [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]


async def wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(100):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never met")


# --- Turns and history ---


async def test_streams_the_answer_and_records_the_exchange() -> None:
    client = FakeClient(answer("Bonjour", ", je suis", " SENSAI."))
    session, out = make_session(client)

    assert await session.handle("Salut") is True

    assert "Bonjour, je suis SENSAI.\n" in out.getvalue()
    assert session.engine.history.messages == exchange("Salut", "Bonjour, je suis SENSAI.")


async def test_second_call_receives_the_first_exchange() -> None:
    client = FakeClient(answer("Un analyste."), answer("En bref."))
    session, _ = make_session(client)

    await session.handle("Quel est le rôle d'une contrôleuse de gestion ?")
    await session.handle("Et en une phrase ?")

    _model, messages, _options = client.calls[1]
    assert messages == [
        *exchange("Quel est le rôle d'une contrôleuse de gestion ?", "Un analyste."),
        {"role": "user", "content": "Et en une phrase ?"},
    ]


async def test_reset_empties_the_history() -> None:
    client = FakeClient(answer("Un analyste."), answer("Je ne sais pas."))
    session, out = make_session(client)

    await session.handle("Quel est le rôle d'une contrôleuse de gestion ?")
    await session.handle("/reset")
    await session.handle("De quoi parlait-on ?")

    assert "Historique vidé." in out.getvalue()
    _model, messages, _options = client.calls[1]
    assert messages == [{"role": "user", "content": "De quoi parlait-on ?"}]


@pytest.mark.parametrize("line", ["", "   ", "\t \n"])
async def test_empty_input_sends_nothing(line: str) -> None:
    client = FakeClient()
    session, out = make_session(client)

    assert await session.handle(line) is True

    assert client.calls == []
    assert "Entrée vide, rien n'est envoyé." in out.getvalue()


async def test_session_survives_ollama_being_down() -> None:
    client = FakeClient([OllamaUnavailable("http://localhost:11434")], answer("Me revoilà."))
    session, out = make_session(client)

    await session.handle("Premier essai")
    await session.handle("Second essai")

    assert "Ollama injoignable sur http://localhost:11434" in out.getvalue()
    assert "Me revoilà." in out.getvalue()
    assert session.engine.history.messages == exchange("Second essai", "Me revoilà.")


async def test_cut_stream_is_shown_but_not_kept() -> None:
    cut = StreamInterrupted("connexion coupée", partial="Le chiffre")
    client = FakeClient([Chunk(content="Le chiffre"), cut])
    session, out = make_session(client)

    await session.handle("Quel chiffre ?")

    assert "Le chiffre\n" in out.getvalue()
    assert "La réponse a été interrompue (connexion coupée)." in out.getvalue()
    assert "pas conservée dans l'historique" in out.getvalue()
    assert len(session.engine.history) == 0


async def test_cancelled_task_leaves_no_trace() -> None:
    client = FakeClient([Chunk(content="Début"), HANG])
    session, out = make_session(client)

    task = asyncio.create_task(session.handle("Question longue"))
    await wait_until(lambda: "Début" in out.getvalue())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(session.engine.history) == 0
    assert client.open_streams == 0
    assert not session.engine.busy


async def test_thinking_is_announced_once_and_never_kept() -> None:
    client = FakeClient(
        [
            Chunk(thinking="17 fois 3,"),
            Chunk(thinking=" c'est 51."),
            *answer("17 × 3 font 51."),
        ]
    )
    session, out = make_session(client)

    await session.handle("17 × 3 ?")

    assert out.getvalue().count("(réflexion…)") == 1
    assert "c'est 51" not in out.getvalue()
    assert session.engine.history.messages == exchange("17 × 3 ?", "17 × 3 font 51.")


async def test_model_text_is_printed_as_is() -> None:
    client = FakeClient(answer("[bold]Total[/bold] :memo:"))
    session, out = make_session(client)

    await session.handle("Total ?")

    assert "[bold]Total[/bold] :memo:" in out.getvalue()


# --- Startup check ---


async def test_startup_with_a_missing_model_lists_the_installed_ones() -> None:
    client = FakeClient(models=(OTHER_MODEL,))
    session, out = make_session(client)

    assert await session.check_model() is False

    assert f"ollama pull {MODEL}" in out.getvalue()
    assert OTHER_MODEL in out.getvalue()


async def test_startup_warns_when_the_model_has_no_tools() -> None:
    client = FakeClient(capabilities=("completion",))
    session, out = make_session(client)

    assert await session.check_model() is True

    assert "ne gère pas les outils" in out.getvalue()


async def test_startup_with_ollama_down_keeps_the_session_open() -> None:
    client = FakeClient(show_error=OllamaUnavailable("http://localhost:11434"))
    session, out = make_session(client)

    assert await session.check_model() is True

    assert "Ollama injoignable" in out.getvalue()


# --- Commands ---


async def test_exit_command_stops_the_chat() -> None:
    session, _ = make_session(FakeClient())
    assert await session.handle("/exit") is False


async def test_model_command_switches_and_keeps_the_history() -> None:
    client = FakeClient(answer("Un."), answer("Deux."))
    session, out = make_session(client)

    await session.handle("Premier")
    await session.handle(f"/model {OTHER_MODEL}")
    await session.handle("Second")

    assert session.engine.model == OTHER_MODEL
    assert f"Modèle : {OTHER_MODEL} (historique conservé)." in out.getvalue()
    model, messages, _options = client.calls[1]
    assert model == OTHER_MODEL
    assert messages[:2] == exchange("Premier", "Un.")


async def test_model_command_with_an_unknown_model_keeps_the_current_one() -> None:
    session, out = make_session(FakeClient())

    await session.handle("/model inexistant")

    assert session.engine.model == MODEL
    assert "ollama pull inexistant" in out.getvalue()
    assert f"* {MODEL}" in out.getvalue()


async def test_models_command_marks_the_current_model() -> None:
    session, out = make_session(FakeClient())

    await session.handle("/models")

    assert f"* {MODEL}" in out.getvalue()
    assert f"  {OTHER_MODEL}" in out.getvalue()


async def test_command_errors_are_shown_and_the_session_goes_on() -> None:
    client = FakeClient(show_error=OllamaUnavailable("http://localhost:11434"))
    session, out = make_session(client)

    assert await session.handle("/models") is True

    assert "Ollama injoignable" in out.getvalue()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/help", "/model <nom>"),
        ("/tools", "Disponible après MCP-04."),
        ("/memory", "Disponible après MEM-03."),
        ("/inconnue", "Commande inconnue : /inconnue. Tapez /help."),
    ],
)
async def test_informative_commands(command: str, expected: str) -> None:
    client = FakeClient()
    session, out = make_session(client)

    assert await session.handle(command) is True

    assert expected in out.getvalue()
    assert client.calls == []


# --- REPL and Ctrl+C ---


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    event_loop = asyncio.new_event_loop()
    yield event_loop
    event_loop.close()


def test_repl_runs_until_exit(loop: asyncio.AbstractEventLoop) -> None:
    client = FakeClient(answer("Bonjour !"))
    session, _ = make_session(client)

    run_repl(session, loop, scripted("Salut", "/exit", "jamais lu"))

    assert session.engine.history.messages == exchange("Salut", "Bonjour !")


@pytest.mark.parametrize("quit_key", [EOFError, KeyboardInterrupt])
def test_ctrl_d_or_ctrl_c_at_the_prompt_quits(
    loop: asyncio.AbstractEventLoop, quit_key: type[BaseException]
) -> None:
    session, _ = make_session(FakeClient())

    def read_line(prompt: str) -> str:
        raise quit_key

    run_repl(session, loop, read_line)  # returns instead of raising


def test_ctrl_c_during_an_answer_goes_back_to_the_prompt(
    loop: asyncio.AbstractEventLoop,
) -> None:
    client = FakeClient(
        [Chunk(content="Début"), CTRL_C, Chunk(content=" jamais affiché"), Chunk(done=True)],
        answer("Suite."),
    )
    session, out = make_session(client)

    run_repl(session, loop, scripted("Question longue", "Autre question", "/exit"))

    assert "Début\nRéponse interrompue." in out.getvalue()
    assert "jamais affiché" not in out.getvalue()
    assert session.engine.history.messages == exchange("Autre question", "Suite.")
    assert client.open_streams == 0


def test_ctrl_c_outside_a_turn_cancels_the_command(loop: asyncio.AbstractEventLoop) -> None:
    async def slow_command() -> None:
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(10)

    with pytest.raises(asyncio.CancelledError):
        run_interruptible(loop, slow_command(), on_interrupt=lambda: False)


def test_ctrl_c_handler_is_restored_after_a_turn(loop: asyncio.AbstractEventLoop) -> None:
    before = signal.getsignal(signal.SIGINT)

    async def turn() -> str:
        return "fini"

    assert run_interruptible(loop, turn()) == "fini"
    assert signal.getsignal(signal.SIGINT) is before


# --- run_chat: configuration and exit codes ---


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("SENSAI_CONFIG", "SENSAI_OLLAMA_MODEL", "SENSAI_OLLAMA_HOST"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.toml"
    path.write_text('[ollama]\nmodel = "modele-du-fichier"\n', encoding="utf-8")
    return path


def use_fake(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    monkeypatch.setattr(engine_module, "OllamaClient", lambda *args, **kwargs: client)


def test_model_argument_wins_over_the_config_file(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(answer("Oui."), models=("modele-du-fichier", "modele-argument"))
    use_fake(monkeypatch, client)

    code = run_chat(str(config_file), model="modele-argument", read_line=scripted("Salut"))

    assert code == 0
    assert client.calls[0][0] == "modele-argument"
    assert client.closed


def test_config_file_model_is_used_without_argument(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(answer("Oui."), models=("modele-du-fichier",))
    use_fake(monkeypatch, client)

    assert run_chat(str(config_file), read_line=scripted("Salut")) == 0

    assert client.calls[0][0] == "modele-du-fichier"


def test_missing_model_stops_with_exit_code_1(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient(models=(OTHER_MODEL,))
    use_fake(monkeypatch, client)

    code = run_chat(str(config_file), model="inexistant", read_line=scripted("jamais lu"))

    assert code == 1
    assert "ollama pull inexistant" in capsys.readouterr().out
    assert client.calls == []
    assert client.closed


def test_invalid_config_stops_with_exit_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_chat(str(tmp_path / "absent.toml"), read_line=scripted())

    assert code == 1
    assert "Fichier de configuration introuvable" in capsys.readouterr().err
