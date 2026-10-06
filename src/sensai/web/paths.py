"""Repository paths used by the web layer.

The package is installed in editable mode (requirements-dev.txt), so these paths are
resolved from this file: src/sensai/web/paths.py -> repository root.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Frontend build produced by `make web-build`
STATIC_DIR = REPO_ROOT / "web" / "dist"

# Event sequences replayed by the MockEngine (tests/fixtures/events/README.md)
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "events"

# JSON schemas of the contracts (docs/contracts/websocket.md §11)
SCHEMAS_DIR = REPO_ROOT / "docs" / "contracts" / "schemas"
