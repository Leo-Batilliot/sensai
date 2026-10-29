"""FastAPI application: REST API, WebSocket and the built frontend (web/dist)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from sensai import __version__
from sensai.core.config import Config
from sensai.web.engines.backend import WebBackend
from sensai.web.frontend import REVALIDATE, FrontendFiles
from sensai.web.paths import STATIC_DIR
from sensai.web.scheduler import Scheduler
from sensai.web.shares import ShareStore, to_markdown
from sensai.web.websocket.endpoint import install_websocket
from sensai.web.websocket.messages import snapshot
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
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()  # a run cut here ends in error, announced to the sessions
            await app.state.sessions.close_all()
            await backend.stop()

    app = FastAPI(title="SENSAI", version=__version__, lifespan=lifespan)
    app.state.config = config
    sessions = SessionManager(backend)
    app.state.sessions = sessions
    shares = ShareStore(config.web.share_dir, config.web.host, config.web.port)
    app.state.shares = shares
    scheduler = Scheduler(config, backend, sessions.broadcast)
    app.state.scheduler = scheduler

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        ollama = "up" if await backend.ollama_available() else "down"
        return {"status": "ok", "ollama": ollama, "version": __version__}

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"default": config.ollama.model, "models": await backend.list_models()}

    @app.get("/api/memory")
    async def memory() -> dict[str, list[dict[str, Any]]]:
        return await backend.memory_snapshot()

    @app.get("/api/documents/current")
    async def document() -> dict[str, Any]:
        return backend.documents().to_dict()

    @app.get("/api/documents/current/revisions")
    async def document_revisions() -> dict[str, Any]:
        return {"revisions": backend.documents().revisions()}

    @app.get("/api/documents/current/export", response_model=None)
    async def document_export(format: str = "md") -> Response:
        if format != "md":
            return api_error(400, "INVALID_MESSAGE", "Format d'export inconnu : md.")
        documents = backend.documents()
        return PlainTextResponse(
            documents.to_markdown(),
            media_type="text/markdown; charset=utf-8",
            headers=download(f"{documents.doc_id}-r{documents.revision}.md"),
        )

    @app.get("/api/personas")
    async def personas() -> dict[str, Any]:
        current, profiles = backend.personas()
        return {"current": current, "personas": profiles}

    @app.get("/api/sessions/{session_id}/export", response_model=None)
    async def export(session_id: str, format: str = "md") -> Response:
        if format not in ("md", "json"):
            return api_error(400, "INVALID_MESSAGE", "Format d'export inconnu : md ou json.")
        session = app.state.sessions.get(session_id)
        if session is None:
            return api_error(404, "INVALID_MESSAGE", "Conversation inconnue ou expirée.")
        frozen = snapshot(session)
        stamp = time.strftime("%Y-%m-%d-%H%M")
        if format == "json":
            body = {"session_id": session_id, "exported_at": time.time(), **frozen}
            body.pop("document")
            return JSONResponse(body, headers=download(f"sensai-conversation-{stamp}.json"))
        markdown = to_markdown(frozen["events"], frozen["model"], frozen["persona"])
        return PlainTextResponse(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers=download(f"sensai-conversation-{stamp}.md"),
        )

    @app.get("/api/shares")
    async def list_shares(request: Request) -> dict[str, Any]:
        # local_only: the server only accepts this machine, so does the link (websocket.md §9)
        return {"shares": shares.list(request.headers.get("host")), "local_only": shares.local_only}

    @app.get("/api/shares/{token}", response_model=None)
    async def get_share(token: str) -> Response:
        shared = shares.get(token)
        if shared is None:
            return api_error(
                404, "SHARE_NOT_FOUND", "Ce lien de partage n'existe pas ou a été révoqué."
            )
        return JSONResponse(shared)

    # The read-only page of a shared link: the same application, which reads /api/shares
    @app.get("/share/{token}", include_in_schema=False, response_model=None)
    async def share_page(token: str) -> Response:
        index = static_dir / "index.html"
        if not index.is_file():
            return PlainTextResponse(FRONTEND_MISSING, status_code=503)
        return FileResponse(index, headers={"Cache-Control": REVALIDATE})

    @app.get("/api/schedules")
    async def schedules() -> dict[str, Any]:
        return {"schedules": scheduler.describe()}

    # Shown in the browser, not downloaded: the "rapport" link of the scheduled tasks panel
    @app.get("/api/schedules/runs/{run_id}/report", response_model=None)
    async def schedule_report(run_id: str) -> Response:
        report = scheduler.report(run_id)
        if report is None:
            return api_error(404, "SCHEDULE_NOT_FOUND", "Pas de rapport pour cette exécution.")
        return PlainTextResponse(report.read_text(encoding="utf-8"))

    install_websocket(app, sessions, shares, scheduler)

    if (static_dir / "index.html").is_file():
        app.mount("/", FrontendFiles(directory=static_dir, html=True), name="frontend")
    else:
        logger.warning("Interface web absente (%s) : lancez `make web-build`.", static_dir)

        @app.get("/", include_in_schema=False)
        async def frontend_missing() -> PlainTextResponse:
            return PlainTextResponse(FRONTEND_MISSING, status_code=503)

    return app


def api_error(status: int, code: str, message: str) -> JSONResponse:
    """REST errors of websocket.md §9: {"error": {"code", "message"}}."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def download(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}
