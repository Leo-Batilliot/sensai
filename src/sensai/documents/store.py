"""DocumentStore: the committee note in memory.db, written section by section (ARTIF-01, M4).

Contract: docs/contracts/memory.md §7. The note lives in memory.db (one agent database to
reset) but apart from the memory entries: it has no kind nor ref and never shows in
memory_search, context_injected or GET /api/memory.

- One section per write, 4 000 characters at most: the note is never rewritten whole.
- Every effective write bumps documents.revision and adds its document_revisions row in
  the same transaction. A write that changes nothing records nothing.
- Optimistic lock: a write names the revision it was based on (base_revision); if the
  note changed meanwhile, it fails with REVISION_CONFLICT and the model reads it again.
- restore(r) never deletes anything: it writes a new revision whose content is the note
  at revision r, rebuilt by undoing the later revisions from their `before`.
- The note is created from its template ([documents] template) on first access.

Errors are DocumentError with the codes of memory.md §7.4: the tool layer turns them
into a failed tool result for the model, never an exception.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from sensai.memory import schema
from sensai.memory.store import AGENT, AuditContext

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10: tomllib is not in the stdlib yet
    import tomli as tomllib

DOC_ID = "note-comite"  # the only note of v1 (memory.md §7.1)
DEFAULT_TEMPLATE = "note_comite"
MAX_CONTENT = 4000
MAX_SECTION_ID = 64

Op = Literal["replace", "append", "insert_after", "delete", "restore"]
PatchOp = Literal["replace", "append", "insert_after"]

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class DocumentError(Exception):
    """Base class: a stable `code` and a French message for the model."""

    code = "document_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RevisionConflict(DocumentError):
    """The note changed since the revision the write was based on."""

    code = "REVISION_CONFLICT"

    def __init__(self, base: int, current: int) -> None:
        super().__init__(
            f"La note a changé : révision courante {current}, pas {base}. Relis-la avec "
            "document_read avant de recommencer."
        )
        self.base = base
        self.current = current


class SectionNotFound(DocumentError):
    code = "section_not_found"


class ContentTooLong(DocumentError):
    code = "content_too_long"


class InvalidPatch(DocumentError):
    code = "invalid_patch"


@dataclass(frozen=True)
class Template:
    """Title and sections (id, title) of a new note."""

    title: str
    sections: tuple[tuple[str, str], ...]


def load_template(name: str = DEFAULT_TEMPLATE) -> Template:
    """documents/templates/<name>.toml, shipped with the package."""
    text = resources.files("sensai.documents").joinpath("templates", f"{name}.toml")
    data = tomllib.loads(text.read_text(encoding="utf-8"))
    return Template(data["title"], tuple((row["id"], row["title"]) for row in data["sections"]))


@dataclass(frozen=True)
class DocumentWrite:
    """Result of a write, sent back to the model (memory.md §7.3). changed is False when
    nothing was written: no new revision, no document_updated."""

    doc_id: str
    revision: int
    op: Op
    section_id: str | None
    summary: str
    changed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "revision": self.revision,
            "op": self.op,
            "section_id": self.section_id,
            "summary": self.summary,
        }


def slug(title: str) -> str:
    """'Encours et retards' -> 'encours-et-retards': lowercase, no accents, '-' between words."""
    decomposed = unicodedata.normalize("NFD", title.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NOT_SLUG.sub("-", plain).strip("-")[:MAX_SECTION_ID].strip("-") or "section"


def _section(row: dict[str, Any]) -> dict[str, Any]:
    """A section as recorded in document_revisions (before / after)."""
    return {key: row[key] for key in ("section_id", "position", "title", "content")}


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


class DocumentStore:
    """Read and write the committee note. Open it with DocumentStore.open(path)."""

    def __init__(
        self, conn: sqlite3.Connection, template: Template | None = None, doc_id: str = DOC_ID
    ) -> None:
        self._conn = conn
        self.template = template or load_template()
        self.doc_id = doc_id

    @classmethod
    def open(cls, path: str | Path, template: str = DEFAULT_TEMPLATE) -> DocumentStore:
        """Open memory.db (created or migrated if needed) with the template of new notes."""
        return cls(schema.open_database(path), load_template(template))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DocumentStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- low-level helpers -------------------------------------------------------------

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, params)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._conn:
            yield

    def _document(self) -> dict[str, Any]:
        """The note's row, created from the template on first access."""
        rows = self._rows("SELECT * FROM documents WHERE id = ?", (self.doc_id,))
        if rows:
            return rows[0]
        now = schema.now_iso()
        with self._transaction():
            self._conn.execute(
                "INSERT INTO documents (id, title, revision, created_at, updated_at)"
                " VALUES (?, ?, 0, ?, ?)",
                (self.doc_id, self.template.title, now, now),
            )
            self._conn.executemany(
                "INSERT INTO document_sections (doc_id, section_id, position, title, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (self.doc_id, section_id, position, title, now)
                    for position, (section_id, title) in enumerate(self.template.sections, 1)
                ],
            )
        return self._rows("SELECT * FROM documents WHERE id = ?", (self.doc_id,))[0]

    def _sections(self) -> list[dict[str, Any]]:
        self._document()
        return self._rows(
            "SELECT * FROM document_sections WHERE doc_id = ? ORDER BY position", (self.doc_id,)
        )

    def _require(self, section_id: str) -> dict[str, Any]:
        for row in self._sections():
            if row["section_id"] == section_id:
                return row
        known = ", ".join(row["section_id"] for row in self._sections()) or "aucune"
        raise SectionNotFound(f"Aucune section « {section_id} » dans la note. Sections : {known}.")

    @property
    def revision(self) -> int:
        return int(self._document()["revision"])

    def _check_revision(self, base_revision: int) -> None:
        current = self.revision
        if base_revision != current:
            raise RevisionConflict(base_revision, current)

    def _record(
        self,
        op: Op,
        section_id: str | None,
        before: Any,
        after: Any,
        ctx: AuditContext,
        now: str,
    ) -> int:
        """Bump the revision and add its row; inside the caller's transaction."""
        revision = self.revision + 1
        self._conn.execute(
            "UPDATE documents SET revision = ?, updated_at = ? WHERE id = ?",
            (revision, now, self.doc_id),
        )
        self._conn.execute(
            "INSERT INTO document_revisions"
            " (doc_id, revision, op, section_id, before, after, actor, session_id, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.doc_id,
                revision,
                op,
                section_id,
                _dump(before),
                _dump(after),
                ctx.actor,
                ctx.session_id,
                now,
            ),
        )
        return revision

    # --- read ---------------------------------------------------------------------------

    def outline(self) -> dict[str, Any]:
        """The plan of the note: what document_read returns without argument."""
        document = self._document()
        return {
            "doc_id": self.doc_id,
            "title": document["title"],
            "revision": document["revision"],
            "sections": [
                {
                    "section_id": row["section_id"],
                    "position": row["position"],
                    "title": row["title"],
                    "chars": len(row["content"]),
                }
                for row in self._sections()
            ],
        }

    def outline_text(self) -> str:
        """The plan for the system prompt ($document_outline): titles and revision only."""
        plan = self.outline()
        lines = [f"Note « {plan['title']} », révision {plan['revision']} (base_revision) :"]
        for section in plan["sections"]:
            state = f"{section['chars']} caractères" if section["chars"] else "vide"
            lines.append(f"- {section['section_id']} : {section['title']} ({state})")
        return "\n".join(lines)

    def read(self, section_id: str | None = None) -> dict[str, Any]:
        """document_read: the plan without section_id, else the content of one section."""
        if section_id is None:
            return self.outline()
        row = self._require(section_id)
        return {
            "doc_id": self.doc_id,
            "revision": self.revision,
            "section_id": row["section_id"],
            "title": row["title"],
            "content": row["content"],
        }

    def revisions(self) -> list[dict[str, Any]]:
        """Every revision, oldest first (GET /api/documents/current/revisions)."""
        return self._rows(
            "SELECT revision, op, section_id, actor, at FROM document_revisions"
            " WHERE doc_id = ? ORDER BY revision",
            (self.doc_id,),
        )

    def to_dict(self) -> dict[str, Any]:
        """The whole note, in the shape of GET /api/documents/current."""
        document = self._document()
        return {
            "id": self.doc_id,
            "title": document["title"],
            "revision": document["revision"],
            "sections": [
                {
                    key: row[key]
                    for key in ("section_id", "position", "title", "content", "updated_at")
                }
                for row in self._sections()
            ],
        }

    def to_markdown(self) -> str:
        """The note as Markdown, the same text for the same revision (export, X3)."""
        lines = [f"# {self._document()['title']}"]
        for row in self._sections():
            lines += ["", f"## {row['title']}"]
            if row["content"].strip():
                lines += ["", row["content"].strip()]
        return "\n".join(lines) + "\n"

    # --- write --------------------------------------------------------------------------

    def patch(
        self,
        section_id: str,
        op: PatchOp,
        content: str,
        base_revision: int,
        title: str | None = None,
        ctx: AuditContext = AGENT,
    ) -> DocumentWrite:
        """document_patch: replace or append to one section, or insert a new one after it."""
        self._check_revision(base_revision)
        before = self._require(section_id)
        if len(content) > MAX_CONTENT:
            raise ContentTooLong(
                f"Contenu de {len(content)} caractères, {MAX_CONTENT} au plus : découpe-le en "
                "plusieurs append."
            )
        if op == "insert_after":
            if not title or not title.strip():
                raise InvalidPatch("insert_after demande le titre de la nouvelle section.")
            return self._insert_after(before, title.strip(), content, ctx)
        if op == "append":
            text = f"{before['content']}\n\n{content}" if before["content"] else content
        elif op == "replace":
            text = content
        else:
            raise InvalidPatch(f"op inconnue : « {op} » (replace, append ou insert_after).")
        if text == before["content"]:
            return DocumentWrite(
                self.doc_id,
                self.revision,
                op,
                section_id,
                f"Section « {before['title']} » : aucune modification",
                changed=False,
            )
        now = schema.now_iso()
        with self._transaction():
            self._conn.execute(
                "UPDATE document_sections SET content = ?, updated_at = ?"
                " WHERE doc_id = ? AND section_id = ?",
                (text, now, self.doc_id, section_id),
            )
            after = {**_section(before), "content": text}
            revision = self._record(op, section_id, _section(before), after, ctx, now)
        summary = f"Section « {before['title']} » mise à jour (révision {revision})"
        return DocumentWrite(self.doc_id, revision, op, section_id, summary)

    def _insert_after(
        self, anchor: dict[str, Any], title: str, content: str, ctx: AuditContext
    ) -> DocumentWrite:
        taken = {row["section_id"] for row in self._sections()}
        base = slug(title)
        new_id, n = base, 1
        while new_id in taken:
            n += 1
            suffix = f"-{n}"
            new_id = base[: MAX_SECTION_ID - len(suffix)].rstrip("-") + suffix
        position = anchor["position"] + 1
        now = schema.now_iso()
        with self._transaction():
            # Make room after the anchor, in the same transaction (ARTIF-01 pitfall)
            self._conn.execute(
                "UPDATE document_sections SET position = position + 1"
                " WHERE doc_id = ? AND position >= ?",
                (self.doc_id, position),
            )
            self._conn.execute(
                "INSERT INTO document_sections"
                " (doc_id, section_id, position, title, content, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self.doc_id, new_id, position, title, content, now),
            )
            after = {"section_id": new_id, "position": position, "title": title, "content": content}
            revision = self._record("insert_after", new_id, None, after, ctx, now)
        summary = f"Section « {title} » ajoutée (révision {revision})"
        return DocumentWrite(self.doc_id, revision, "insert_after", new_id, summary)

    def delete_impact(self, section_id: str) -> dict[str, Any]:
        """Preview of delete_section(), in the shape of confirmation_required.impact."""
        row = self._require(section_id)
        return {
            "operation": "DELETE",
            "rows_affected": 1,
            "per_table": {"document_sections": -1},
            "sample": [
                {"section_id": section_id, "title": row["title"], "chars": len(row["content"])}
            ],
            "blocked": [],
        }

    def delete_section(
        self, section_id: str, base_revision: int, ctx: AuditContext = AGENT
    ) -> DocumentWrite:
        """document_delete_section. The confirmation is the HITL policy's job."""
        self._check_revision(base_revision)
        before = self._require(section_id)
        now = schema.now_iso()
        with self._transaction():
            self._conn.execute(
                "DELETE FROM document_sections WHERE doc_id = ? AND section_id = ?",
                (self.doc_id, section_id),
            )
            revision = self._record("delete", section_id, _section(before), None, ctx, now)
        summary = f"Section « {before['title']} » supprimée (révision {revision})"
        return DocumentWrite(self.doc_id, revision, "delete", section_id, summary)

    def restore(self, revision: int, ctx: AuditContext = AGENT) -> DocumentWrite:
        """document_restore: a new revision whose content is the note at `revision`."""
        current = self.revision
        if not 0 <= revision <= current:
            raise SectionNotFound(f"Révision {revision} inconnue : la note est à la {current}.")
        if revision == current:
            return DocumentWrite(
                self.doc_id, current, "restore", None, "Note déjà à cette révision", changed=False
            )
        title = self._document()["title"]
        before = {"title": title, "sections": [_section(row) for row in self._sections()]}
        after = {"title": title, "sections": self._state_at(revision)}
        now = schema.now_iso()
        with self._transaction():
            self._conn.execute("DELETE FROM document_sections WHERE doc_id = ?", (self.doc_id,))
            self._conn.executemany(
                "INSERT INTO document_sections"
                " (doc_id, section_id, position, title, content, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (self.doc_id, s["section_id"], s["position"], s["title"], s["content"], now)
                    for s in after["sections"]
                ],
            )
            new_revision = self._record("restore", None, before, after, ctx, now)
        summary = f"Note revenue à la révision {revision} (révision {new_revision})"
        return DocumentWrite(self.doc_id, new_revision, "restore", None, summary)

    def _state_at(self, revision: int) -> list[dict[str, Any]]:
        """The sections at `revision`: the current ones, with every later revision undone."""
        sections = [_section(row) for row in self._sections()]
        later = self._rows(
            "SELECT op, before, after FROM document_revisions"
            " WHERE doc_id = ? AND revision > ? ORDER BY revision DESC",
            (self.doc_id, revision),
        )
        for row in later:
            before = None if row["before"] is None else json.loads(row["before"])
            after = None if row["after"] is None else json.loads(row["after"])
            if row["op"] in ("replace", "append"):
                sections = [
                    before if s["section_id"] == before["section_id"] else s for s in sections
                ]
            elif row["op"] == "insert_after":
                sections = [s for s in sections if s["section_id"] != after["section_id"]]
                for s in sections:
                    if s["position"] > after["position"]:
                        s["position"] -= 1
            elif row["op"] == "delete":
                sections.append(before)
            else:  # restore
                sections = [dict(s) for s in before["sections"]]
            sections.sort(key=lambda s: s["position"])
        return sections


__all__ = [
    "DOC_ID",
    "MAX_CONTENT",
    "ContentTooLong",
    "DocumentError",
    "DocumentStore",
    "DocumentWrite",
    "InvalidPatch",
    "RevisionConflict",
    "SectionNotFound",
    "Template",
    "load_template",
    "slug",
]
