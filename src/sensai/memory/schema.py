"""Load the memory.db schema and open connections with foreign keys enforced."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA_FILE = "schema.sql"
SCHEMA_VERSION = 1  # PRAGMA user_version set by schema.sql

# Kinds of memory entries, shared with the tools, the events and the WebSocket contract
KINDS = ("glossary", "entity", "relation", "fact")

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    """Current UTC time in the only format the timestamp columns accept.

    datetime.isoformat() adds microseconds and '+00:00' and would fail the CHECK.
    """
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def schema_sql() -> str:
    """Return the DDL of memory.db, read from the installed package."""
    return resources.files("sensai.memory").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open memory.db with foreign keys on (SQLite leaves them off by default)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Whether this SQLite build ships FTS5, needed by memory_fts."""
    options = {row[0] for row in conn.execute("PRAGMA compile_options")}
    return "ENABLE_FTS5" in options


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table, index and trigger on an empty database.

    executescript is fine here: the DDL is trusted and shipped with the code. It must
    never run SQL coming from the model.
    """
    if not fts5_available(conn):
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} n'inclut pas FTS5, requis par memory.db. "
            "Utilisez un Python dont le module sqlite3 est compilé avec FTS5."
        )
    conn.executescript(schema_sql())
