"""Run the sql-toolbox MCP server over stdio.

    python -m sensai.mcp_servers.sql_toolbox --db data/business.db

stdout carries the protocol only: logging goes to stderr, configured before anything
else is imported.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="sql-toolbox %(levelname)s %(message)s"
    )
    from sensai.mcp_servers.sql_toolbox.server import SqlToolboxServer, serve

    parser = argparse.ArgumentParser(prog="sql-toolbox", description="Serveur MCP de lecture SQL.")
    parser.add_argument("--db", required=True, help="fichier de la base (business.db)")
    parser.add_argument("--row-limit", type=int, default=200, help="lignes renvoyées au plus")
    parser.add_argument("--timeout-ms", type=int, default=5000, help="durée maximale d'une requête")
    args = parser.parse_args(argv)

    if not Path(args.db).is_file():
        logging.error("base introuvable : %s (lancez `make seed`)", args.db)
        return 2
    try:
        server = SqlToolboxServer(args.db, row_limit=args.row_limit, timeout_ms=args.timeout_ms)
    except sqlite3.Error as exc:
        logging.error("base illisible : %s (%s)", args.db, exc)
        return 2
    logging.info("prêt sur %s", args.db)
    try:
        serve(server, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
