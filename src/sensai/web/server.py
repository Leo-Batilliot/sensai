"""FastAPI application: REST API and the built frontend (web/dist)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from sensai import __version__
from sensai.core.config import Config

logger = logging.getLogger(__name__)

DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"

FRONTEND_MISSING = (
    "Interface web non construite : lancez `make web-build`, puis relancez `make web`.\n"
)


def create_app(config: Config, *, static_dir: Path = DEFAULT_STATIC_DIR) -> FastAPI:
    """Build the web application. API routes must be declared before the static mount."""
    app = FastAPI(title="SENSAI", version=__version__)
    app.state.config = config

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    if (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    else:
        logger.warning("Interface web absente (%s) : lancez `make web-build`.", static_dir)

        @app.get("/", include_in_schema=False)
        async def frontend_missing() -> PlainTextResponse:
            return PlainTextResponse(FRONTEND_MISSING, status_code=503)

    return app
