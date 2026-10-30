"""The built frontend (web/dist), committed so that `make web` needs no Node (WEB-14).

- `npm run build` writes web/dist/build.json: the digest of the sources of the build
  (vite.config.ts). `stale()` computes it again from web/: the CI and the server tell a
  web/dist older than web/src without Node.
- `FrontendFiles` serves web/dist with cache headers: the files under assets/ carry a
  hash in their name and never change; index.html is checked at every load, so that a
  new build shows up without a forced reload.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STAMP = "build.json"
TOP_FILES = ("index.html", "package-lock.json", "vite.config.ts")
TREES = ("src", "public")
TEST_FILE = re.compile(r"\.test\.tsx?$")

IMMUTABLE = "public, max-age=31536000, immutable"  # assets/index-<hash>.js
REVALIDATE = "no-cache"  # index.html, favicon: asked again at every load


def source_files(web_dir: Path) -> list[str]:
    """The files a build is made from, as vite.config.ts lists them: tests left out."""
    paths = [name for name in TOP_FILES if (web_dir / name).is_file()]
    for tree in TREES:
        root = web_dir / tree
        if root.is_dir():
            paths += [p.relative_to(web_dir).as_posix() for p in root.rglob("*") if p.is_file()]
    kept = [p for p in paths if not TEST_FILE.search(p) and not p.startswith("src/test/")]
    return sorted(kept)


def sources_digest(web_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(web_dir):
        digest.update(f"{path}\0".encode())
        digest.update((web_dir / path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def built_digest(static_dir: Path) -> str | None:
    """The digest written by the build, or None (no build, or one older than WEB-14)."""
    try:
        stamp = json.loads((static_dir / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = stamp.get("sources") if isinstance(stamp, dict) else None
    return value if isinstance(value, str) else None


def stale(static_dir: Path, web_dir: Path) -> bool:
    """True when web/dist was not built from the sources of web/ as they are now.

    False without web/src (an install without the sources has nothing to compare)."""
    if not (web_dir / "src").is_dir():
        return False
    return built_digest(static_dir) != sources_digest(web_dir)


class FrontendFiles(StaticFiles):
    """web/dist with its cache headers (see the module docstring)."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        hashed = Path(full_path).parent.name == "assets"
        response.headers["Cache-Control"] = IMMUTABLE if hashed else REVALIDATE
        return response
