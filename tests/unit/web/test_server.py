from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sensai import __version__
from sensai.__main__ import main
from sensai.core.config import Config
from sensai.web.server import create_app


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A fake frontend build: what `make web-build` writes to web/dist."""
    directory = tmp_path / "dist"
    directory.mkdir()
    (directory / "index.html").write_text("<title>SENSAI</title>", encoding="utf-8")
    return directory


# --- application ----------------------------------------------------------------------


def test_health(dist: Path) -> None:
    client = TestClient(create_app(Config(), static_dir=dist))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_serves_the_built_frontend(dist: Path) -> None:
    client = TestClient(create_app(Config(), static_dir=dist))
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>SENSAI</title>" in response.text


def test_static_mount_does_not_shadow_the_api(dist: Path) -> None:
    client = TestClient(create_app(Config(), static_dir=dist))
    assert client.get("/api/health").headers["content-type"] == "application/json"


def test_missing_build_explains_how_to_fix(tmp_path: Path) -> None:
    client = TestClient(create_app(Config(), static_dir=tmp_path / "absent"))
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
def test_web_command_starts_uvicorn_with_the_configured_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_run(app: FastAPI, *, host: str, port: int) -> None:
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["web", "--port", "9001"]) == 0
    assert calls["host"] == "127.0.0.1"  # [web] host default
    assert calls["port"] == 9001  # --port wins over the default
    assert isinstance(calls["app"], FastAPI)


@pytest.mark.usefixtures("isolated_env")
def test_web_command_reports_a_bad_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["web", "--config", str(tmp_path / "absent.toml")]) == 1
    assert "introuvable" in capsys.readouterr().err
