"""Load the business.db schema and open connections with foreign keys enforced."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_FILE = "business_schema.sql"


def schema_sql() -> str:
    """Return the DDL of business.db, read from the installed package."""
    return resources.files("sensai.data").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open business.db with foreign keys on (SQLite leaves them off by default)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table and index on an empty database.

    executescript is fine here: the DDL is trusted and shipped with the code. It must
    never run SQL coming from the model.
    """
    conn.executescript(schema_sql())
