"""MemoryStore: CRUD on memory.db, audit of every write, full-text search (MEM-02).

Contract: docs/contracts/memory.md. Every entry is addressed by (kind, ref); ref is the
term for the glossary and the integer id as text for the other kinds.

- Each effective write adds its memory_audit row in the same transaction. A write that
  changes nothing returns op 'unchanged' and records nothing.
- Deleting an entity also deletes its relations and facts (ON DELETE CASCADE): SQLite
  does it silently, so the store snapshots them first and audits each one.
- Case-insensitive comparisons use str.casefold() in Python: COLLATE NOCASE only folds
  ASCII ('ÉLAN' != 'élan' for SQLite).
- Raw text never reaches MATCH: search() goes through fts.build_fts_query().
- delete_impact() previews a deletion, cascades included, for the HITL confirmation of
  memory_delete (tools.md, point T-4).

The store is synchronous and owns its connection. Tool wrappers and annotations come
with MEM-03.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from sensai.memory import schema
from sensai.memory.errors import (
    AmbiguousEntity,
    Conflict,
    EntityNotFound,
    InvalidPatch,
    MemoryStoreError,
    NotFound,
)
from sensai.memory.fts import build_fts_query

Kind = Literal["glossary", "entity", "relation", "fact"]
Op = Literal["create", "update", "delete", "unchanged"]
Actor = Literal["agent", "user"]

DEFAULT_SEARCH_LIMIT = 5
LABEL_MAX_CHARS = 60
IMPACT_SAMPLE_SIZE = 5  # confirmation_required.impact.sample holds 5 entries at most

# Fields a patch may replace, per kind (memory.md §2)
PATCHABLE: dict[str, frozenset[str]] = {
    "glossary": frozenset({"term", "definition", "sql_hint"}),
    "entity": frozenset({"type", "name", "aliases", "external_ref", "attributes"}),
    "relation": frozenset({"type", "attributes"}),
    "fact": frozenset({"content", "category", "entity"}),
}

_TABLES = {"glossary": "glossary", "entity": "entities", "relation": "relations", "fact": "facts"}
_JSON_COLUMNS = ("aliases", "attributes")


@dataclass(frozen=True)
class WriteResult:
    """Result of a write, sent back to the model as is (memory.md §3.2)."""

    op: Op
    kind: Kind
    ref: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"op": self.op, "kind": self.kind, "ref": self.ref, "summary": self.summary}


@dataclass(frozen=True)
class AuditContext:
    """Who writes: 'agent' for tool calls, 'user' for actions from the interface."""

    actor: Actor = "agent"
    session_id: str | None = None


AGENT = AuditContext()


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.loads(value) if key in _JSON_COLUMNS and isinstance(value, str) else value
        for key, value in row.items()
    }


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _truncate(text: str) -> str:
    return text if len(text) <= LABEL_MAX_CHARS else text[: LABEL_MAX_CHARS - 1] + "…"


def _dedupe(names: Sequence[str], exclude: Sequence[str] = ()) -> list[str]:
    """Keep the first spelling of each name, ignoring case and the excluded names."""
    seen = {name.casefold() for name in exclude}
    kept: list[str] = []
    for name in names:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            kept.append(name)
    return kept


class MemoryStore:
    """Read and write the agent's memory. Open it with MemoryStore.open(path)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: str | Path) -> MemoryStore:
        """Open memory.db, creating the file and its schema when they do not exist."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = schema.connect(path)
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        if version == 0:
            schema.create_schema(conn)
        elif version != schema.SCHEMA_VERSION:
            conn.close()
            raise RuntimeError(
                f"{path} : schéma mémoire v{version}, v{schema.SCHEMA_VERSION} attendu."
            )
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- low-level helpers -------------------------------------------------------------

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [_decode(dict(zip(columns, row, strict=True))) for row in cursor.fetchall()]

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Commit on success, roll back on error; map constraint errors to §3.4 codes."""
        try:
            with self._conn:
                yield
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise Conflict(f"Entrée déjà existante ({exc}).") from exc
            raise InvalidPatch(f"Valeur refusée par la base ({exc}).") from exc

    def _audit(
        self,
        op: Literal["create", "update", "delete"],
        kind: Kind,
        ref: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        ctx: AuditContext,
    ) -> None:
        def dump(row: dict[str, Any] | None) -> str | None:
            return None if row is None else json.dumps(row, ensure_ascii=False, sort_keys=True)

        self._conn.execute(
            "INSERT INTO memory_audit (op, kind, ref, before, after, actor, session_id, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (op, kind, ref, dump(before), dump(after), ctx.actor, ctx.session_id, schema.now_iso()),
        )

    def _row(self, kind: Kind, ref: str) -> dict[str, Any] | None:
        """The raw row of (kind, ref), JSON columns decoded, or None."""
        if kind == "glossary":
            matches = [
                row for row in self._rows("SELECT * FROM glossary") if _same(row["term"], ref)
            ]
            return matches[0] if matches else None
        if kind not in _TABLES:
            raise InvalidPatch(f"kind inconnu : « {kind} ».")
        if not ref.isdigit():
            return None
        rows = self._rows(f"SELECT * FROM {_TABLES[kind]} WHERE id = ?", (int(ref),))
        return rows[0] if rows else None

    def _require(self, kind: Kind, ref: str) -> dict[str, Any]:
        row = self._row(kind, ref)
        if row is None:
            raise NotFound(kind, ref)
        return row

    def _entities(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM entities ORDER BY id")

    def _entity_name(self, entity_id: int) -> str:
        row = self._row("entity", str(entity_id))
        return row["name"] if row else str(entity_id)

    # --- entity designation (memory.md §3.1) --------------------------------------------

    def resolve_entity(self, designation: str) -> int:
        """Id of the entity designated by its ref, exact name or exact alias.

        Tried in that order; the first rule with matches wins. Several matches raise
        AmbiguousEntity, none raises EntityNotFound.
        """
        entities = self._entities()
        if designation.isdigit():
            if any(row["id"] == int(designation) for row in entities):
                return int(designation)
            raise EntityNotFound(designation)
        for matches in (
            [row for row in entities if _same(row["name"], designation)],
            [row for row in entities if any(_same(a, designation) for a in row["aliases"])],
        ):
            if len(matches) == 1:
                return int(matches[0]["id"])
            if matches:
                raise AmbiguousEntity(
                    designation, [(row["type"], row["name"], str(row["id"])) for row in matches]
                )
        raise EntityNotFound(designation)

    # --- create / upsert ----------------------------------------------------------------

    def define_term(
        self, term: str, definition: str, sql_hint: str | None = None, ctx: AuditContext = AGENT
    ) -> WriteResult:
        """Create a glossary term, or replace the definition of an existing one."""
        before = self._row("glossary", term)
        if before is None:
            with self._transaction():
                self._conn.execute(
                    "INSERT INTO glossary (term, definition, sql_hint) VALUES (?, ?, ?)",
                    (term, definition, sql_hint),
                )
                after = self._require("glossary", term)
                self._audit("create", "glossary", term, None, after, ctx)
            return WriteResult("create", "glossary", term, f"Terme « {term} » défini")
        return self.update(
            "glossary", before["term"], {"definition": definition, "sql_hint": sql_hint}, ctx
        )

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        aliases: Sequence[str] = (),
        attributes: dict[str, Any] | None = None,
        external_ref: str | None = None,
        ctx: AuditContext = AGENT,
    ) -> WriteResult:
        """Create an entity, or complete the one with the same type and name.

        Completing adds the new aliases and merges attributes key by key. Matching on
        aliases ('Lemaire' = 'Brasserie Lemaire') is MEM-05.
        """
        existing = [
            row
            for row in self._entities()
            if row["type"] == entity_type and _same(row["name"], name)
        ]
        if existing:
            row = existing[0]
            patch: dict[str, Any] = {
                "aliases": row["aliases"] + _dedupe(aliases, [row["name"], *row["aliases"]]),
                "attributes": {**row["attributes"], **(attributes or {})},
            }
            if external_ref is not None:
                patch["external_ref"] = external_ref
            return self.update("entity", str(row["id"]), patch, ctx)

        with self._transaction():
            cursor = self._conn.execute(
                "INSERT INTO entities (type, name, aliases, attributes, external_ref)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    entity_type,
                    name,
                    json.dumps(_dedupe(aliases, [name]), ensure_ascii=False),
                    json.dumps(attributes or {}, ensure_ascii=False),
                    external_ref,
                ),
            )
            ref = str(cursor.lastrowid)
            self._audit("create", "entity", ref, None, self._require("entity", ref), ctx)
        return WriteResult("create", "entity", ref, f"Entité « {name} » enregistrée")

    def relate(
        self, src: str, relation_type: str, dst: str, ctx: AuditContext = AGENT
    ) -> WriteResult:
        """Link two entities designated by ref, name or alias. Idempotent."""
        src_id, dst_id = self.resolve_entity(src), self.resolve_entity(dst)
        label = f"{self._entity_name(src_id)} —{relation_type}→ {self._entity_name(dst_id)}"
        existing = self._rows(
            "SELECT id FROM relations WHERE src_id = ? AND type = ? AND dst_id = ?",
            (src_id, relation_type, dst_id),
        )
        if existing:
            return WriteResult(
                "unchanged", "relation", str(existing[0]["id"]), f"Relation {label} déjà connue"
            )
        with self._transaction():
            cursor = self._conn.execute(
                "INSERT INTO relations (src_id, type, dst_id) VALUES (?, ?, ?)",
                (src_id, relation_type, dst_id),
            )
            ref = str(cursor.lastrowid)
            self._audit("create", "relation", ref, None, self._require("relation", ref), ctx)
        return WriteResult("create", "relation", ref, f"Relation {label} créée")

    def add_fact(
        self,
        content: str,
        category: str,
        entity: str | None = None,
        source: Actor = "agent",
        ctx: AuditContext = AGENT,
    ) -> WriteResult:
        """Record a fact, attached to an entity (ref, name or alias) or general."""
        entity_id = None if entity is None else self.resolve_entity(entity)
        with self._transaction():
            cursor = self._conn.execute(
                "INSERT INTO facts (entity_id, category, content, source) VALUES (?, ?, ?, ?)",
                (entity_id, category, content, source),
            )
            ref = str(cursor.lastrowid)
            self._audit("create", "fact", ref, None, self._require("fact", ref), ctx)
        return WriteResult("create", "fact", ref, f"Fait retenu : « {_truncate(content)} »")

    # --- update -------------------------------------------------------------------------

    def update(
        self, kind: Kind, ref: str, patch: dict[str, Any], ctx: AuditContext = AGENT
    ) -> WriteResult:
        """Replace the fields given in patch (memory.md §2). 'unchanged' if no change."""
        if kind not in PATCHABLE:
            raise InvalidPatch(f"kind inconnu : « {kind} ».")
        unknown = sorted(set(patch) - PATCHABLE[kind])
        if unknown or not patch:
            allowed = ", ".join(sorted(PATCHABLE[kind]))
            raise InvalidPatch(
                f"Champ(s) non modifiable(s) pour {kind} : {', '.join(unknown) or 'aucun'}."
                f" Champs acceptés : {allowed}."
            )
        before = self._require(kind, ref)
        values = self._column_values(kind, before, patch)
        changed = {column: value for column, value in values.items() if before[column] != value}
        label = self._label(kind, before)
        if not changed:
            return WriteResult("unchanged", kind, ref, f"{label} : aucune modification")

        new_ref = changed.get("term", ref) if kind == "glossary" else ref
        self._check_unique_name(kind, before, changed)
        assignments = ", ".join(f"{column} = ?" for column in changed)
        params = [
            json.dumps(value, ensure_ascii=False) if column in _JSON_COLUMNS else value
            for column, value in changed.items()
        ]
        key, key_value = ("term", before["term"]) if kind == "glossary" else ("id", before["id"])
        with self._transaction():
            self._conn.execute(
                f"UPDATE {_TABLES[kind]} SET {assignments}, updated_at = ? WHERE {key} = ?",
                (*params, schema.now_iso(), key_value),
            )
            after = self._require(kind, new_ref)
            self._audit("update", kind, new_ref, before, after, ctx)
        return WriteResult("update", kind, new_ref, f"{self._label(kind, after)} : mis à jour")

    def _column_values(
        self, kind: Kind, before: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Patch fields as column values, with the type checks the DDL cannot do."""
        values: dict[str, Any] = {}
        for field, value in patch.items():
            if field == "entity":
                values["entity_id"] = None if value is None else self.resolve_entity(str(value))
            elif field == "aliases":
                if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
                    raise InvalidPatch("aliases doit être une liste de chaînes.")
                values[field] = _dedupe(value, [patch.get("name", before.get("name", ""))])
            elif field == "attributes":
                if not isinstance(value, dict):
                    raise InvalidPatch("attributes doit être un objet.")
                values[field] = value
            elif value is None and field not in ("sql_hint", "external_ref"):
                raise InvalidPatch(f"{field} ne peut pas être vide.")
            elif value is not None and not isinstance(value, str):
                raise InvalidPatch(f"{field} doit être une chaîne.")
            else:
                values[field] = value
        return values

    def _check_unique_name(
        self, kind: Kind, before: dict[str, Any], changed: dict[str, Any]
    ) -> None:
        """Case-insensitive uniqueness, beyond the ASCII-only COLLATE NOCASE."""
        if kind == "glossary" and "term" in changed:
            for row in self._rows("SELECT term FROM glossary"):
                if _same(row["term"], changed["term"]) and not _same(row["term"], before["term"]):
                    raise Conflict(f"Le terme « {row['term']} » existe déjà.")
        if kind == "entity" and ("name" in changed or "type" in changed):
            name = changed.get("name", before["name"])
            entity_type = changed.get("type", before["type"])
            for row in self._entities():
                if (
                    row["id"] != before["id"]
                    and row["type"] == entity_type
                    and _same(row["name"], name)
                ):
                    raise Conflict(
                        f"L'entité {entity_type} « {row['name']} » existe déjà (ref {row['id']})."
                    )

    # --- delete -------------------------------------------------------------------------

    def _cascade(self, kind: Kind, row: dict[str, Any]) -> list[tuple[Kind, dict[str, Any]]]:
        """Entries that ON DELETE CASCADE removes with this one: an entity's links and facts."""
        if kind != "entity":
            return []
        entity_id = row["id"]
        relations = self._rows(
            "SELECT * FROM relations WHERE src_id = ? OR dst_id = ? ORDER BY id",
            (entity_id, entity_id),
        )
        facts = self._rows("SELECT * FROM facts WHERE entity_id = ? ORDER BY id", (entity_id,))
        return [("relation", r) for r in relations] + [("fact", f) for f in facts]

    def delete_impact(self, kind: Kind, ref: str) -> dict[str, Any]:
        """Preview of delete(), in the shape of confirmation_required.impact (memory.md §4).

        Read only. per_table counts the cascades; sample lists the entry, then the
        cascaded ones, 5 at most. Nothing blocks a memory deletion: blocked is empty.
        """
        before = self._require(kind, ref)
        entries = [(kind, before), *self._cascade(kind, before)]
        per_table: dict[str, int] = {}
        for entry_kind, _ in entries:
            table = _TABLES[entry_kind]
            per_table[table] = per_table.get(table, 0) - 1
        sample = [
            {
                "kind": entry_kind,
                "ref": row["term"] if entry_kind == "glossary" else str(row["id"]),
                "label": self._short_label(entry_kind, row),
            }
            for entry_kind, row in entries[:IMPACT_SAMPLE_SIZE]
        ]
        return {
            "operation": "DELETE",
            "rows_affected": 1,
            "per_table": per_table,
            "sample": sample,
            "blocked": [],
        }

    def delete(self, kind: Kind, ref: str, ctx: AuditContext = AGENT) -> WriteResult:
        """Delete an entry. An entity takes its relations and facts with it, each audited."""
        before = self._require(kind, ref)
        label = self._label(kind, before)
        cascade = self._cascade(kind, before)
        key, key_value = ("term", before["term"]) if kind == "glossary" else ("id", before["id"])
        ref = before["term"] if kind == "glossary" else ref
        with self._transaction():
            for child_kind, row in cascade:
                self._audit("delete", child_kind, str(row["id"]), row, None, ctx)
            self._audit("delete", kind, ref, before, None, ctx)
            self._conn.execute(f"DELETE FROM {_TABLES[kind]} WHERE {key} = ?", (key_value,))
        summary = f"{label} : supprimé"
        if cascade:
            relations = sum(1 for child_kind, _ in cascade if child_kind == "relation")
            summary += f" (avec {relations} relation(s) et {len(cascade) - relations} fait(s))"
        return WriteResult("delete", kind, ref, summary)

    # --- read ---------------------------------------------------------------------------

    def get(self, kind: Kind, ref: str) -> dict[str, Any]:
        """One entry in the shape of GET /api/memory (memory.md §5)."""
        return self._public(kind, self._require(kind, ref))

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """Every entry, grouped by table, for GET /api/memory (memory.md §5)."""
        return {
            "glossary": [
                self._public("glossary", row)
                for row in self._rows("SELECT * FROM glossary ORDER BY term")
            ],
            "entities": [self._public("entity", row) for row in self._entities()],
            "relations": [
                self._public("relation", row)
                for row in self._rows("SELECT * FROM relations ORDER BY id")
            ],
            "facts": [
                self._public("fact", row) for row in self._rows("SELECT * FROM facts ORDER BY id")
            ],
        }

    def audit_log(self, kind: Kind | None = None, ref: str | None = None) -> list[dict[str, Any]]:
        """Audit rows, oldest first, with before / after decoded from JSON."""
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if ref is not None:
            clauses.append("ref = ?")
            params.append(ref)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(f"SELECT * FROM memory_audit{where} ORDER BY id", params)
        for row in rows:
            for column in ("before", "after"):
                row[column] = None if row[column] is None else json.loads(row[column])
        return rows

    def search(
        self, query: str, kind: Kind | None = None, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[dict[str, Any]]:
        """Best matches first (bm25), in the result shape of memory_search (§3.2)."""
        fts_query = build_fts_query(query)
        if fts_query is None:
            return []
        sql = "SELECT kind, ref FROM memory_fts WHERE memory_fts MATCH ?"
        params: list[Any] = [fts_query]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY bm25(memory_fts) LIMIT ?"
        params.append(limit)
        results = []
        for hit in self._rows(sql, params):
            row = self._row(hit["kind"], hit["ref"])
            if row is not None:
                results.append(self._search_result(hit["kind"], hit["ref"], row))
        return results

    # --- shapes -------------------------------------------------------------------------

    def _label(self, kind: Kind, row: dict[str, Any]) -> str:
        if kind == "glossary":
            return f"Terme « {row['term']} »"
        if kind == "entity":
            return f"Entité « {row['name']} »"
        if kind == "relation":
            src, dst = self._entity_name(row["src_id"]), self._entity_name(row["dst_id"])
            return f"Relation {src} —{row['type']}→ {dst}"
        return f"Fait « {_truncate(row['content'])} »"

    def _short_label(self, kind: Kind, row: dict[str, Any]) -> str:
        """Label without prefix, as in search results and context_injected (memory.md §4)."""
        if kind == "glossary":
            return row["term"]
        if kind == "entity":
            return row["name"]
        if kind == "relation":
            src, dst = self._entity_name(row["src_id"]), self._entity_name(row["dst_id"])
            return f"{src} —{row['type']}→ {dst}"
        return _truncate(row["content"])

    def _search_result(self, kind: Kind, ref: str, row: dict[str, Any]) -> dict[str, Any]:
        if kind == "glossary":
            return {
                "kind": kind,
                "ref": row["term"],
                "label": row["term"],
                "snippet": f"{row['term']} : {row['definition']}",
                "sql_hint": row["sql_hint"],
            }
        if kind == "entity":
            aliases = f" ({', '.join(row['aliases'])})" if row["aliases"] else ""
            return {
                "kind": kind,
                "ref": ref,
                "label": row["name"],
                "snippet": row["name"] + aliases,
            }
        return {
            "kind": kind,
            "ref": ref,
            "label": _truncate(row["content"]),
            "snippet": row["content"],
        }

    @staticmethod
    def _public(kind: Kind, row: dict[str, Any]) -> dict[str, Any]:
        if kind == "glossary":
            return {
                "ref": row["term"],
                **{k: row[k] for k in ("term", "definition", "sql_hint", "updated_at")},
            }
        ref = str(row["id"])
        if kind == "entity":
            keys = ("type", "name", "aliases", "external_ref", "attributes", "updated_at")
            return {"ref": ref, **{k: row[k] for k in keys}}
        if kind == "relation":
            return {
                "ref": ref,
                "src": str(row["src_id"]),
                "type": row["type"],
                "dst": str(row["dst_id"]),
                "attributes": row["attributes"],
                "updated_at": row["updated_at"],
            }
        entity = None if row["entity_id"] is None else str(row["entity_id"])
        return {
            "ref": ref,
            "entity": entity,
            **{k: row[k] for k in ("category", "content", "source", "updated_at")},
        }


__all__ = ["AGENT", "AuditContext", "MemoryStore", "MemoryStoreError", "WriteResult"]
