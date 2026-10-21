"""sql-toolbox: our MCP server, which exposes business.db to any MCP client.

It contains no LLM. Tools: list_tables, describe_table and run_select (reads only, through
the SQL guard), preview_write (impact of a write, always rolled back) and execute_write
(the write itself, which the client must have confirmed: it is declared destructive). The server answers one request at a time, over stdio: one JSON-RPC
message per line on stdin, one answer per line on stdout, logs on stderr only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import jsonschema

from sensai import __version__
from sensai.mcp import jsonrpc
from sensai.mcp.client import SUPPORTED_PROTOCOL_VERSIONS
from sensai.mcp.jsonrpc import Message
from sensai.mcp_servers.sql_toolbox.guard import (
    FORBIDDEN,
    READ,
    WRITE,
    QueryTimeout,
    SqlGuard,
    json_value,
    open_readonly,
)
from sensai.mcp_servers.sql_toolbox.preview import AUDIT_TABLE, SqlWriter, WriteBlocked

logger = logging.getLogger(__name__)

SERVER_NAME = "sql-toolbox"
SAMPLE_ROWS = 3
# A text column with at most this many distinct values gets them listed (statuses...)
MAX_LISTED_VALUES = 12

# Short descriptions of the business.db tables, shown by list_tables
TABLE_DESCRIPTIONS = {
    "sales_reps": "Commerciaux de l'entreprise",
    "clients": "Entreprises clientes, avec leur statut et leur commercial",
    "contacts": "Personnes à contacter chez chaque client",
    "products": "Catalogue : prix de vente et coût d'achat actuels",
    "orders": "Commandes des clients",
    "order_lines": "Détail des commandes : produit, quantité, prix et coût appliqués",
    "invoices": "Factures, une par commande expédiée ou livrée",
    "payments": "Paiements reçus pour les factures",
    "write_audit": "Journal des modifications confirmées par un utilisateur",
}


class ToolError(Exception):
    """A failure to report as a tool result (isError), with a code of the tools contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SqlToolboxServer:
    """Handles MCP messages for one database. Transport-free: see serve() for stdio."""

    def __init__(self, db_path: str | Path, row_limit: int = 200, timeout_ms: int = 5000) -> None:
        self.row_limit = row_limit
        self.guard = SqlGuard(db_path, timeout_ms)
        # The server's own queries (schema, samples): read-only, built from checked names
        self._meta = open_readonly(db_path)
        self.writer = SqlWriter(db_path, timeout_ms)
        self.tools = _tool_definitions(row_limit)
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_tables": self._list_tables,
            "describe_table": self._describe_table,
            "run_select": self._run_select,
            "preview_write": self._preview_write,
            "execute_write": self._execute_write,
        }
        self._validators = {
            tool["name"]: jsonschema.Draft202012Validator(tool["inputSchema"])
            for tool in self.tools
        }

    def close(self) -> None:
        self.guard.close()
        self._meta.close()
        self.writer.close()

    # ---- protocol ----------------------------------------------------------------------

    def handle_line(self, line: bytes) -> Message | None:
        """Answer one line read on stdin; None for a notification."""
        try:
            message = jsonrpc.decode(line)
        except ValueError:
            return jsonrpc.error_response(None, jsonrpc.PARSE_ERROR, "Parse error")
        return self.handle(message)

    def handle(self, message: Message) -> Message | None:
        if jsonrpc.is_notification(message) or jsonrpc.is_response(message):
            # initialized, cancelled (requests are answered one by one, nothing to stop)
            return None
        if not jsonrpc.is_request(message):
            return jsonrpc.error_response(
                message.get("id"), jsonrpc.INVALID_REQUEST, "Invalid request"
            )
        request_id, method = message["id"], message["method"]
        params = message.get("params") or {}
        if method == "initialize":
            return jsonrpc.result_response(request_id, self._initialize(params))
        if method == "ping":
            return jsonrpc.result_response(request_id, {})
        if method == "tools/list":
            return jsonrpc.result_response(request_id, {"tools": self.tools})
        if method == "tools/call":
            return self._call(request_id, params)
        return jsonrpc.error_response(
            request_id, jsonrpc.METHOD_NOT_FOUND, f"Method not found: {method}"
        )

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "Base de données de l'entreprise, en lecture. Commencer par list_tables, "
                "puis describe_table sur les tables utiles, puis run_select."
            ),
        }

    def _call(self, request_id: jsonrpc.RequestId, params: dict[str, Any]) -> Message:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in self._handlers:
            return jsonrpc.error_response(
                request_id, jsonrpc.INVALID_PARAMS, f"Unknown tool: {name}"
            )
        errors = sorted(self._validators[name].iter_errors(arguments), key=lambda e: e.path)
        if errors:
            detail = "; ".join(error.message for error in errors)
            return jsonrpc.error_response(
                request_id, jsonrpc.INVALID_PARAMS, f"Invalid params: {detail}"
            )
        try:
            data = self._handlers[name](arguments)
        except ToolError as exc:
            return jsonrpc.result_response(request_id, _error_result(exc.code, exc.message))
        return jsonrpc.result_response(
            request_id,
            {
                "content": [{"type": "text", "text": _compact(data)}],
                "structuredContent": data,
                "isError": False,
            },
        )

    # ---- tools -------------------------------------------------------------------------

    def _list_tables(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        tables = [
            {"name": name, "description": TABLE_DESCRIPTIONS.get(name, "")}
            for name in self._table_names()
        ]
        return {"tables": tables}

    def _describe_table(self, arguments: dict[str, Any]) -> dict[str, Any]:
        table = arguments["table"]
        names = self._table_names()
        if table not in names:
            raise ToolError(
                "SQL_ERROR", f"Table inconnue : {table}. Tables disponibles : {', '.join(names)}."
            )
        quoted = _quote(table)
        meta = self._meta
        ddl = meta.execute(
            "SELECT sql FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')", (table,)
        ).fetchone()[0]
        columns = [
            {
                "name": name,
                "type": type_,
                "not_null": bool(not_null),
                "primary_key": bool(primary_key),
                "default": default,
            }
            for _cid, name, type_, not_null, default, primary_key in meta.execute(
                f"PRAGMA table_info({quoted})"
            )
        ]
        foreign_keys = [
            {"column": column, "references": f"{ref_table}.{ref_column}", "on_delete": on_delete}
            for _id, _seq, ref_table, column, ref_column, _on_update, on_delete, _match in meta.execute(
                f"PRAGMA foreign_key_list({quoted})"
            )
        ]
        row_count = meta.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        sample = meta.execute(f"SELECT * FROM {quoted} LIMIT {SAMPLE_ROWS}")
        sample_columns = [description[0] for description in sample.description]
        sample_rows = [[json_value(value) for value in row] for row in sample.fetchall()]
        return {
            "table": table,
            "ddl": ddl,
            "row_count": row_count,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "sample": {"columns": sample_columns, "rows": sample_rows},
            "value_lists": self._value_lists(quoted, columns),
        }

    def _value_lists(self, quoted: str, columns: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """Distinct values of the text columns that have few of them (status, segment...)."""
        lists: dict[str, list[Any]] = {}
        for column in columns:
            if column["type"].upper() != "TEXT" or column["primary_key"]:
                continue
            name = _quote(column["name"])
            values = self._meta.execute(
                f"SELECT DISTINCT {name} FROM {quoted} WHERE {name} IS NOT NULL"
                f" ORDER BY 1 LIMIT {MAX_LISTED_VALUES + 1}"
            ).fetchall()
            if 0 < len(values) <= MAX_LISTED_VALUES:
                lists[column["name"]] = [value for (value,) in values]
        return lists

    def _run_select(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sql = arguments["sql"]
        try:
            classification = self.guard.classify(sql)
        except sqlite3.Error as exc:
            raise ToolError("SQL_ERROR", f"Erreur SQLite : {exc}") from exc
        if classification.kind == WRITE:
            raise ToolError(
                "SQL_FORBIDDEN",
                "run_select n'accepte que des lectures (SELECT ou WITH … SELECT) : "
                "cette requête modifie des données, elle n'a pas été exécutée. "
                "Pour modifier, utilisez execute_write (une confirmation sera demandée).",
            )
        if classification.kind == FORBIDDEN:
            raise ToolError("SQL_FORBIDDEN", f"Requête refusée : {classification.reason}.")
        assert classification.kind == READ
        try:
            columns, rows, truncated = self.guard.run_read(sql, self.row_limit)
        except QueryTimeout as exc:
            raise ToolError(
                "TOOL_TIMEOUT",
                f"Requête interrompue : plus de {self.guard.timeout_ms} ms. "
                "Ajoutez des filtres ou simplifiez-la.",
            ) from exc
        except sqlite3.Error as exc:
            raise ToolError("SQL_ERROR", f"Erreur SQLite : {exc}") from exc
        logger.debug("run_select: %d rows (%s)", len(rows), arguments.get("purpose", ""))
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}

    def _preview_write(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sql = arguments["sql"]
        operation = self._write_operation(sql, "preview_write")
        try:
            return self.writer.preview(sql, operation)
        except QueryTimeout as exc:
            raise self._timeout() from exc
        except sqlite3.Error as exc:
            raise ToolError("SQL_ERROR", f"Erreur SQLite : {exc}") from exc

    def _execute_write(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sql = arguments["sql"]
        operation = self._write_operation(sql, "execute_write")
        try:
            result = self.writer.execute(sql, operation)
        except WriteBlocked as exc:
            hint = f" {exc.hint}." if exc.hint else ""
            raise ToolError(
                "SQL_ERROR",
                f"Écriture refusée par la base, rien n'a été modifié : {exc.reason}.{hint}",
            ) from exc
        except QueryTimeout as exc:
            raise self._timeout() from exc
        except sqlite3.Error as exc:
            raise ToolError("SQL_ERROR", f"Erreur SQLite : {exc}") from exc
        logger.info("execute_write: %s, %d rows", operation, result["rows_affected"])
        return result

    def _write_operation(self, sql: str, tool: str) -> str:
        """The operation of a write (INSERT, UPDATE, DELETE); anything else is refused."""
        try:
            classification = self.guard.classify(sql)
        except sqlite3.Error as exc:
            raise ToolError("SQL_ERROR", f"Erreur SQLite : {exc}") from exc
        if classification.kind == READ:
            raise ToolError(
                "SQL_FORBIDDEN",
                f"{tool} n'accepte que des écritures (INSERT, UPDATE, DELETE) : "
                "pour lire, utilisez run_select.",
            )
        if classification.kind == FORBIDDEN:
            raise ToolError("SQL_FORBIDDEN", f"Requête refusée : {classification.reason}.")
        if AUDIT_TABLE in classification.tables:
            raise ToolError("SQL_FORBIDDEN", "Le journal des écritures ne peut pas être modifié.")
        assert classification.operation is not None
        return classification.operation

    def _timeout(self) -> ToolError:
        return ToolError(
            "TOOL_TIMEOUT",
            f"Requête interrompue : plus de {self.guard.timeout_ms} ms. Rien n'a été modifié.",
        )

    def _table_names(self) -> list[str]:
        return [
            name
            for (name,) in self._meta.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]


def serve(server: SqlToolboxServer, stdin: IO[bytes], stdout: IO[bytes]) -> None:
    """Answer requests until stdin closes."""
    for line in stdin:
        if not line.strip():
            continue
        answer = server.handle_line(line)
        if answer is not None:
            stdout.write(jsonrpc.encode(answer))
            stdout.flush()


def _error_result(code: str, message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"code": code, "message": message},
        "isError": True,
    }


def _compact(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


_WRITE_INPUT = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "minLength": 1,
            "maxLength": 20000,
            "description": "L'instruction d'écriture, une seule.",
        }
    },
    "required": ["sql"],
    "additionalProperties": False,
}
_IMPACT_OUTPUT = {
    "type": "object",
    "properties": {
        "operation": {"enum": ["INSERT", "UPDATE", "DELETE"]},
        "rows_affected": {"type": "integer"},
        "per_table": {"type": "object", "additionalProperties": {"type": "integer"}},
        "sample": {"type": "array", "maxItems": 5, "items": {"type": "object"}},
        "blocked": {"type": "array", "items": {"type": "object"}},
        "hint": {"type": "string"},
    },
    "required": ["operation", "rows_affected", "per_table", "sample", "blocked"],
}


def _tool_definitions(row_limit: int) -> list[dict[str, Any]]:
    """The tools as listed by tools/list. Descriptions are written for the model."""
    return [
        {
            "name": "list_tables",
            "description": (
                "Liste les tables de la base de données de l'entreprise, avec une courte "
                "description. À appeler en premier pour savoir où chercher."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "description"],
                        },
                    }
                },
                "required": ["tables"],
            },
            "annotations": {"title": "Lister les tables", **_READ_ONLY},
        },
        {
            "name": "describe_table",
            "description": (
                "Décrit une table : colonnes et types, clés étrangères, définition commentée, "
                f"{SAMPLE_ROWS} lignes d'exemple, et valeurs possibles des colonnes texte qui en "
                "ont peu (statuts, segments). À appeler avant d'écrire une requête sur la table."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Nom exact de la table, tel que renvoyé par list_tables.",
                    }
                },
                "required": ["table"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "ddl": {"type": "string"},
                    "row_count": {"type": "integer"},
                    "columns": {"type": "array", "items": {"type": "object"}},
                    "foreign_keys": {"type": "array", "items": {"type": "object"}},
                    "sample": {"type": "object"},
                    "value_lists": {"type": "object"},
                },
                "required": [
                    "table",
                    "ddl",
                    "row_count",
                    "columns",
                    "foreign_keys",
                    "sample",
                    "value_lists",
                ],
            },
            "annotations": {"title": "Décrire une table", **_READ_ONLY},
        },
        {
            "name": "run_select",
            "description": (
                "Exécute une requête SQL de lecture (SELECT ou WITH … SELECT, dialecte SQLite) "
                f"sur la base de l'entreprise et renvoie au plus {row_limit} lignes. Une seule "
                "instruction ; toute écriture est refusée. Les dates sont du texte "
                "'AAAA-MM-JJ' : utiliser date() et julianday() pour les calculs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 20000,
                        "description": "La requête SQL de lecture.",
                    },
                    "purpose": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "Ce que la requête cherche, en quelques mots.",
                    },
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                    "row_count": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["columns", "rows", "row_count", "truncated"],
            },
            "annotations": {"title": "Lire la base", **_READ_ONLY},
        },
        {
            "name": "preview_write",
            "description": (
                "Calcule l'effet d'une écriture (INSERT, UPDATE ou DELETE) sans la garder : "
                "lignes visées et nombre de lignes en plus ou en moins par table, suppressions "
                "en cascade comprises. La base n'est jamais modifiée."
            ),
            "inputSchema": _WRITE_INPUT,
            "outputSchema": _IMPACT_OUTPUT,
            "annotations": {"title": "Prévoir une écriture", **_READ_ONLY},
        },
        {
            "name": "execute_write",
            "description": (
                "Exécute une écriture (INSERT, UPDATE ou DELETE, dialecte SQLite) sur la base de "
                "l'entreprise. Une confirmation de l'utilisateur est demandée avant : si elle "
                "est refusée, ne pas réessayer. Une seule instruction ; le schéma et le journal "
                "des écritures ne peuvent pas être modifiés."
            ),
            "inputSchema": _WRITE_INPUT,
            "outputSchema": {
                "type": "object",
                "properties": {
                    "operation": {"enum": ["INSERT", "UPDATE", "DELETE"]},
                    "rows_affected": {"type": "integer"},
                    "per_table": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "audit_id": {"type": "integer"},
                },
                "required": ["operation", "rows_affected", "per_table", "audit_id"],
            },
            "annotations": {
                "title": "Modifier la base",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
    ]
