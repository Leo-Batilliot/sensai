"""The committed frontend build (WEB-14): fresh, complete, served with cache headers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sensai.core.config import Config
from sensai.web.engines.mock import MockBackend
from sensai.web.frontend import IMMUTABLE, REVALIDATE, source_files, sources_digest, stale
from sensai.web.paths import STATIC_DIR, WEB_DIR
from sensai.web.server import create_app

REBUILD = "web/dist est plus ancien que web/src : lancez `make web-build` et committez web/dist."


# --- the build of the repository ------------------------------------------------------------


def test_the_committed_build_matches_the_sources() -> None:
    assert not stale(STATIC_DIR, WEB_DIR), REBUILD


def test_the_committed_build_is_complete() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for asset in assets:
        assert (STATIC_DIR / asset).is_file(), f"{asset} manque dans web/dist : {REBUILD}"


def test_tests_are_not_sources_of_the_build() -> None:
    files = source_files(WEB_DIR)
    assert "src/App.tsx" in files and "vite.config.ts" in files
    assert not any(f.endswith(".test.ts") or f.startswith("src/test/") for f in files)


# --- the digest -------------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path: Path) -> Path:
    (tmp_path / "src" / "test").mkdir(parents=True)
    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (tmp_path / "src" / "main.tsx").write_text("render()", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    return tmp_path


def stamp(web: Path) -> None:
    digest = sources_digest(web)
    (web / "dist" / "build.json").write_text(f'{{"sources": "{digest}"}}', encoding="utf-8")


def test_a_change_of_source_makes_the_build_stale(web: Path) -> None:
    assert stale(web / "dist", web)  # never built
    stamp(web)
    assert not stale(web / "dist", web)
    (web / "src" / "main.tsx").write_text("render(); // changed", encoding="utf-8")
    assert stale(web / "dist", web)


def test_a_change_of_test_keeps_the_build(web: Path) -> None:
    stamp(web)
    (web / "src" / "main.test.ts").write_text("it()", encoding="utf-8")
    (web / "src" / "test" / "fixtures.ts").write_text("load()", encoding="utf-8")
    assert not stale(web / "dist", web)


def test_without_sources_nothing_is_stale(tmp_path: Path) -> None:
    assert not stale(tmp_path / "dist", tmp_path)  # an install that only has web/dist


# --- served -----------------------------------------------------------------------------------


def test_assets_are_cached_and_the_page_is_checked_again(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>SENSAI</title>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("run()", encoding="utf-8")
    config = Config.model_validate({"web": {"share_dir": str(tmp_path / "shares")}})
    with TestClient(create_app(config, MockBackend(config), static_dir=dist)) as client:
        page = client.get("/")
        asset = client.get("/assets/index-abc123.js")
        shared = client.get("/share/AbCdEfGhIjKlMnOpQrStUv")
    assert page.headers["cache-control"] == REVALIDATE
    assert asset.headers["cache-control"] == IMMUTABLE
    assert shared.headers["cache-control"] == REVALIDATE


def test_the_server_says_when_the_build_is_stale(
    web: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sensai.__main__ import main

    monkeypatch.setattr("sensai.web.paths.WEB_DIR", web)
    monkeypatch.setattr("sensai.web.paths.STATIC_DIR", web / "dist")
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    monkeypatch.chdir(web)  # no config.toml: the defaults
    assert main(["web", "--mock"]) == 0
    assert "make web-build" in capsys.readouterr().err
    stamp(web)
    assert main(["web", "--mock"]) == 0
    assert "make web-build" not in capsys.readouterr().err
