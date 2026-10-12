"""FastAPI application: REST API, WebSocket and the built frontend (web/dist)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from sensai import __version__
from sensai.core.config import Config
from sensai.web.engines.backend import WebBackend
from sensai.web.paths import STATIC_DIR
from sensai.web.websocket.endpoint import install_websocket
from sensai.web.websocket.session import SessionManager

logger = logging.getLogger(__name__)

DEFAULT_STATIC_DIR = STATIC_DIR

FRONTEND_MISSING = (
    "Interface web non construite : lancez `make web-build`, puis relancez `make web`.\n"
)


def create_app(
    config: Config, backend: WebBackend, *, static_dir: Path = DEFAULT_STATIC_DIR
) -> FastAPI:
    """Build the web application. API routes must be declared before the static mount."""

    # Shared services (MCP servers, memory) start once with the application, not per session
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await backend.start()
        try:
            yield
        finally:
            await app.state.sessions.close_all()
            await backend.stop()

    app = FastAPI(title="SENSAI", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.sessions = SessionManager(backend)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        ollama = "up" if await backend.ollama_available() else "down"
        return {"status": "ok", "ollama": ollama, "version": __version__}

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"default": config.ollama.model, "models": await backend.list_models()}

    install_websocket(app, app.state.sessions)

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    else:
        logger.warning("Interface web absente (%s) : lancez `make web-build`.", static_dir)

        @app.get("/", include_in_schema=False)
        async def frontend_missing() -> PlainTextResponse:
            return PlainTextResponse(FRONTEND_MISSING, status_code=503)

    return app
