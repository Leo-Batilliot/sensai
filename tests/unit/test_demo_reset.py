"""Reset of the demo (CORE-19): sensai demo-reset puts back what the acts write."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import PERSONAS_DIR
from sensai import demo_reset
from sensai.__main__ import main
from sensai.core.config import (
    AgentConfig,
    Config,
    DemoConfig,
    McpConfig,
    McpServerConfig,
    OllamaConfig,
)
from sensai.demo_reset import (
    ResetError,
    check_mcp_cache,
    check_session,
    preload_models,
    reset_data,
)
from sensai.documents.store import DocumentStore
from sensai.llm.ollama_client import OllamaClient
from sensai.memory.store import MemoryStore

TODAY = date(2026, 10, 31)
UVX = "/usr/local/bin/uvx"


@pytest.fixture
def launch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A launch directory with what the five acts leave behind."""
    monkeypatch.chdir(tmp_path)  # every path of the configuration is relative to it
    data = tmp_path / "data"
    data.mkdir()
    with MemoryStore.open(data / "memory.db") as store:
        store.add_fact("Les montants sont en k€", "preference")
    with DocumentStore.open(data / "memory.db") as documents:
        outline = documents.outline()
        first = outline["sections"][0]["section_id"]
        documents.patch(first, "replace", "Marge en baisse de 4 points.", outline["revision"])
    (data / "shares").mkdir()
    (data / "shares" / "a1b2c3.json").write_text("{}", encoding="utf-8")
    (data / "scheduler.db").write_bytes(b"")
    (data / "file_audit.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "reports" / "planifie").mkdir(parents=True)
    (tmp_path / "reports" / "export.md").write_text("# Export", encoding="utf-8")
    (tmp_path / "reports" / "planifie" / "point-du-lundi.md").write_text("#", encoding="utf-8")
    return tmp_path


def test_reset_puts_back_what_the_acts_write(launch_dir: Path) -> None:
    steps = reset_data(Config(), today=TODAY)

    assert all(step.ok for step in steps)
    assert (launch_dir / "data" / "business.db").is_file()
    with MemoryStore.open(launch_dir / "data" / "memory.db") as store:
        assert not any(store.dump().values())  # the rule of act 2 is learnt live
    with (
        DocumentStore.open(launch_dir / "data" / "memory.db") as reset,
        DocumentStore.open(launch_dir / "new.db") as new,
    ):
        assert reset.outline() == new.outline()  # the note of the template, untouched
    assert list((launch_dir / "data" / "shares").iterdir()) == []
    assert not (launch_dir / "data" / "scheduler.db").exists()
    assert not (launch_dir / "data" / "file_audit.jsonl").exists()
    assert list((launch_dir / "reports").iterdir()) == []
    assert "date de référence 2026-10-31" in steps[0].text
    assert "Rapports supprimés : 2" in steps[-1].text


def test_reset_works_on_a_new_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # nothing generated yet: no data/, no reports/

    steps = reset_data(Config(), today=TODAY)

    assert all(step.ok for step in steps)
    assert (tmp_path / "data" / "memory.db").is_file()
    assert (tmp_path / "reports").is_dir()  # the export of act 5 writes there


def test_a_file_in_use_stops_the_reset(launch_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # What Windows answers when sensai web still holds business.db open
    def locked(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(13, "Accès refusé", "data/business.db")

    monkeypatch.setattr(demo_reset, "generate", locked)

    with pytest.raises(ResetError, match="fermez `sensai web`"):
        reset_data(Config(), today=TODAY)


def test_session_check_names_the_default_profile_and_prompt() -> None:
    steps = check_session(Config(agent=AgentConfig(personas_dir=PERSONAS_DIR)))

    assert all(step.ok for step in steps)
    assert steps[0].text == "Profil des nouvelles sessions : Contrôle de gestion (analyste)"
    assert steps[1].text == "Version du prompt système : v4"


@pytest.mark.parametrize(
    ("agent", "warning"),
    [
        (AgentConfig(persona="inconnu", personas_dir=PERSONAS_DIR), "Profil par défaut inconnu"),
        (AgentConfig(personas_dir=Path("absent")), "Dossier des profils introuvable"),
    ],
)
def test_session_check_warns_about_a_profile_that_would_not_start(
    agent: AgentConfig, warning: str
) -> None:
    steps = check_session(Config(agent=agent))

    assert not steps[0].ok and warning in steps[0].text


def test_session_check_recalls_the_demo_mode_of_act_3() -> None:
    config = Config(
        agent=AgentConfig(personas_dir=PERSONAS_DIR), demo=DemoConfig(alter_answer=True)
    )

    assert "Mode démo actif" in check_session(config)[-1].text


def completed(returncode: int) -> Any:
    def run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        run.calls.append(args)  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(args, returncode)

    run.calls = []  # type: ignore[attr-defined]
    return run


def test_time_server_in_the_uv_cache() -> None:
    run = completed(0)

    steps = check_mcp_cache(Config(), which=lambda _command: UVX, run=run)

    assert [step.ok for step in steps] == [True]
    assert "time : mcp-server-time en cache" in steps[0].text
    # Only the server started by uvx, offline: nothing downloaded to check it
    assert run.calls == [[UVX, "--offline", "mcp-server-time", "--help"]]


def test_time_server_missing_from_the_uv_cache() -> None:
    steps = check_mcp_cache(Config(), which=lambda _command: UVX, run=completed(2))

    assert not steps[0].ok and "absent du cache uv" in steps[0].text


def test_uvx_missing_from_the_path() -> None:
    # The case of `.venv\Scripts\python.exe -m sensai web` without the venv activated
    steps = check_mcp_cache(Config(), which=lambda _command: None, run=completed(0))

    assert not steps[0].ok and "introuvable dans le PATH" in steps[0].text


def test_no_check_without_a_server_started_by_uvx() -> None:
    toolbox = McpServerConfig(command="python", args=("-m", "sensai.mcp_servers.sql_toolbox"))
    config = Config(mcp=McpConfig(servers={"sql_toolbox": toolbox}))

    assert check_mcp_cache(config, which=lambda _command: UVX, run=completed(0)) == []


def ollama(requests: list[dict[str, Any]], status: int = 200) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append({"path": request.url.path, **json.loads(request.content)})
        return httpx.Response(status, json={"done": True} if status == 200 else {"error": "?"})

    return OllamaClient(
        "http://ollama.test", keep_alive="30m", transport=httpx.MockTransport(handler)
    )


async def test_preload_uses_the_num_ctx_of_the_turns() -> None:
    requests: list[dict[str, Any]] = []
    config = Config(ollama=OllamaConfig(num_ctx=16384, judge_model="llama3.1:8b"))

    steps = await preload_models(config, ollama(requests))

    assert all(step.ok for step in steps)
    # Another num_ctx would make Ollama load the model again at the first question
    assert requests == [
        {
            "path": "/api/generate",
            "model": model,
            "options": {"num_ctx": 16384},
            "keep_alive": "30m",
        }
        for model in ("qwen3:8b", "llama3.1:8b")
    ]


async def test_preload_of_a_missing_model_is_a_warning() -> None:
    steps = await preload_models(Config(), ollama([], status=404))

    assert not steps[0].ok and "qwen3:8b" in steps[0].text


def test_command(launch_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (launch_dir / "config.toml").write_text(
        f'[agent]\npersonas_dir = "{PERSONAS_DIR.as_posix()}"\n\n'
        '[mcp.servers.sql_toolbox]\ncommand = "python"\n'
        'args = ["-m", "sensai.mcp_servers.sql_toolbox"]\n',
        encoding="utf-8",
    )

    assert main(["demo-reset", "--no-preload"]) == 0

    out = capsys.readouterr().out
    assert "- Base de démonstration régénérée : data" in out
    assert "- Profil des nouvelles sessions : Contrôle de gestion (analyste)" in out
    assert out.rstrip().endswith("Démo prête : lancez `sensai web`.")
