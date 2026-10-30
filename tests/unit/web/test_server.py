import shutil
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fakes import PERSONAS_DIR
from sensai import __version__
from sensai.__main__ import main
from sensai.core.config import Config
from sensai.data.schema import connect, create_schema
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.engines.real import RealBackend
from sensai.web.server import create_app
from sensai.web.websocket.settings import SESSION_SETTINGS

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A fake frontend build: what `make web-build` writes to web/dist."""
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "index.html").write_text("<title>SENSAI</title>", encoding="utf-8")
    return directory


def make_client(static_dir: Path) -> TestClient:
    config = Config()
    return TestClient(
        create_app(config, MockBackend(config, pacing=INSTANT), static_dir=static_dir)
    )


# --- application ----------------------------------------------------------------------


def test_health(dist: Path) -> None:
    response = make_client(dist).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ollama": "up", "version": __version__}


def test_models_lists_the_default_and_the_tool_support(dist: Path) -> None:
    body = make_client(dist).get("/api/models").json()
    assert body["default"] == Config().ollama.model
    names = {model["name"]: model["supports_tools"] for model in body["models"]}
    assert names[Config().ollama.model] is True
    assert False in names.values()  # one model without tools, to show the warning


def test_config_gives_the_settings_of_the_interface(dist: Path) -> None:
    body = make_client(dist).get("/api/config").json()
    config = Config()
    # exactly the keys set_config accepts, with the values of config.toml
    assert set(body["defaults"]) == set(SESSION_SETTINGS)
    assert body["defaults"]["num_ctx"] == config.ollama.num_ctx
    assert body["defaults"]["judge_model"] is None  # empty in config.toml: same as the agent
    assert body["bounds"] == {"temperature": [0.0, 1.0], "num_ctx": [2048, 32768]}
    assert body["read_only"]["confirmation_timeout_s"] == config.hitl.confirmation_timeout_s
    # never a path, an MCP command, a deny list nor the [web] section
    text = str(body)
    assert "path" not in text and "deny" not in text and "command" not in text
    assert str(config.web.port) not in text


def test_serves_the_built_frontend(dist: Path) -> None:
    response = make_client(dist).get("/")
    assert response.status_code == 200
    assert "<title>SENSAI</title>" in response.text


def test_static_mount_does_not_shadow_the_api(dist: Path) -> None:
    client = make_client(dist)
    assert client.get("/api/health").headers["content-type"] == "application/json"
    assert client.get("/api/models").headers["content-type"] == "application/json"


def test_missing_build_explains_how_to_fix(tmp_path: Path) -> None:
    client = make_client(tmp_path / "absent")
    response = client.get("/")
    assert response.status_code == 503
    assert "make web-build" in response.text
    assert client.get("/api/health").status_code == 200


# --- `sensai web` command -------------------------------------------------------------


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty directory and no SENSAI_* variables, so a developer's settings never leak in."""
    monkeypatch.chdir(tmp_path)
    for name in ("SENSAI_CONFIG", "SENSAI_WEB_HOST", "SENSAI_WEB_PORT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("isolated_env")
def test_web_command_starts_uvicorn_with_the_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_run(app: FastAPI, *, host: str, port: int) -> None:
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["web", "--mock", "--port", "9001"]) == 0
    assert calls["host"] == "127.0.0.1"  # [web] host default
    assert calls["port"] == 9001  # --port wins over the default
    assert isinstance(calls["app"], FastAPI)


@pytest.mark.usefixtures("isolated_env")
def test_web_command_without_database_asks_for_the_seed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["web"]) == 1
    error = capsys.readouterr().err
    assert "make seed" in error
    assert "--mock" in error  # the way to run without a database


def launch_directory(directory: Path) -> None:
    """What `sensai web` reads in its launch directory: the database and the profiles."""
    (directory / "data").mkdir()
    conn = connect(directory / "data" / "business.db")  # [database] business_path default
    create_schema(conn)
    conn.close()
    shutil.copytree(PERSONAS_DIR, directory / "personas")  # [agent] personas_dir default


@pytest.mark.usefixtures("isolated_env")
def test_web_command_starts_the_real_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_directory(tmp_path)
    apps: list[FastAPI] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, *, host, port: apps.append(app))
    assert main(["web"]) == 0
    (app,) = apps
    assert isinstance(app.state.sessions.backend, RealBackend)


@pytest.mark.usefixtures("isolated_env")
def test_web_command_reports_an_invalid_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launch_directory(tmp_path)
    (tmp_path / "personas" / "adv.toml").write_text('id = "adv"\n', encoding="utf-8")
    monkeypatch.setattr(uvicorn, "run", lambda app, *, host, port: pytest.fail("started"))
    assert main(["web"]) == 1
    error = capsys.readouterr().err
    assert "adv.toml" in error and "name" in error  # the file and the missing field


@pytest.mark.usefixtures("isolated_env")
def test_web_command_reports_a_bad_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["web", "--mock", "--config", str(tmp_path / "absent.toml")]) == 1
    assert "introuvable" in capsys.readouterr().err
