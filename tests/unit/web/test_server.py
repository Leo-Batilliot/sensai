from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sensai import __version__
from sensai.__main__ import main
from sensai.core.config import Config
from sensai.data.schema import connect, create_schema
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.engines.real import RealBackend
from sensai.web.server import create_app

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


@pytest.mark.usefixtures("isolated_env")
def test_web_command_starts_the_real_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path / "data" / "business.db")  # [database] business_path default
    create_schema(conn)
    conn.close()
    apps: list[FastAPI] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, *, host, port: apps.append(app))
    assert main(["web"]) == 0
    (app,) = apps
    assert isinstance(app.state.sessions.backend, RealBackend)


@pytest.mark.usefixtures("isolated_env")
def test_web_command_reports_a_bad_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["web", "--mock", "--config", str(tmp_path / "absent.toml")]) == 1
    assert "introuvable" in capsys.readouterr().err
