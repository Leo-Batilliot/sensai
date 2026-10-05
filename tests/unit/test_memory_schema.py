import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from sensai.memory.schema import SCHEMA_VERSION, connect, create_schema, now_iso

TABLES = {"entities", "relations", "facts", "glossary", "memory_audit", "memory_fts"}


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    create_schema(conn)
    yield conn
    conn.close()


def add_entity(
    db: sqlite3.Connection, name: str, entity_type: str = "client", aliases: list[str] | None = None
) -> int:
    cursor = db.execute(
        "INSERT INTO entities (type, name, aliases) VALUES (?, ?, ?)",
        (entity_type, name, json.dumps(aliases or [])),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def add_fact(db: sqlite3.Connection, content: str, entity_id: int | None = None) -> int:
    cursor = db.execute(
        "INSERT INTO facts (entity_id, category, content, source) VALUES (?, 'note', ?, 'user')",
        (entity_id, content),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def search(db: sqlite3.Connection, query: str) -> set[tuple[str, str]]:
    rows = db.execute("SELECT kind, ref FROM memory_fts WHERE memory_fts MATCH ?", (query,))
    return set(rows.fetchall())


# --- structure ---------------------------------------------------------------------------


def test_all_tables_created(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'memory_fts_%'"
    ).fetchall()
    assert {name for (name,) in rows} == TABLES


def test_schema_version(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_connect_enforces_foreign_keys(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO facts (entity_id, category, content, source) VALUES (99, 'note', 'x', 'user')"
        )


# --- timestamps --------------------------------------------------------------------------


def test_timestamps_default_to_utc_iso(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO glossary (term, definition) VALUES ('ca', 'chiffre d''affaires')")
    created, updated = db.execute("SELECT created_at, updated_at FROM glossary").fetchone()
    assert created == updated
    datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")


def test_now_iso_is_accepted(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO glossary (term, definition, created_at, updated_at) VALUES ('ca', 'x', ?, ?)",
        (now_iso(), now_iso()),
    )


@pytest.mark.parametrize(
    "value",
    [
        datetime.now(timezone.utc).isoformat(),  # microseconds and +00:00
        "2026-10-05 10:00:00",
        "2026-10-05",
        "2026-02-30T10:00:00Z",
    ],
)
def test_other_timestamp_formats_are_rejected(db: sqlite3.Connection, value: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO glossary (term, definition, created_at) VALUES ('ca', 'x', ?)", (value,)
        )


# --- constraints -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("type", "supplier"),
        ("name", "  "),
        ("aliases", '{"a": 1}'),
        ("aliases", "[not json"),
        ("attributes", "[]"),
        ("external_ref", "clients"),
        ("external_ref", "Clients:4"),
        ("external_ref", "clients:4a"),
        ("external_ref", "clients:"),
    ],
)
def test_entities_reject_invalid_values(db: sqlite3.Connection, column: str, value: str) -> None:
    row: dict[str, str | None] = {
        "type": "client",
        "name": "Brasserie Lemaire",
        "aliases": "[]",
        "attributes": "{}",
        "external_ref": None,
    }
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO entities (type, name, aliases, attributes, external_ref)"
            " VALUES (:type, :name, :aliases, :attributes, :external_ref)",
            row,
        )


def test_entity_external_ref_accepts_table_and_id(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO entities (type, name, external_ref) VALUES ('person', 'Julien', 'sales_reps:3')"
    )


def test_entity_name_is_unique_per_type_ignoring_case(db: sqlite3.Connection) -> None:
    add_entity(db, "Brasserie Lemaire")
    add_entity(db, "Brasserie Lemaire", entity_type="person")
    with pytest.raises(sqlite3.IntegrityError):
        add_entity(db, "brasserie LEMAIRE")


def test_glossary_term_is_unique_ignoring_case(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO glossary (term, definition) VALUES ('Client rentable', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO glossary (term, definition) VALUES ('client rentable', 'y')")
    row = db.execute("SELECT definition FROM glossary WHERE term = 'CLIENT RENTABLE'").fetchone()
    assert row == ("x",)


@pytest.mark.parametrize("relation_type", ["Managed_by", "managed by", "managed-by", "_x", ""])
def test_relation_type_must_be_snake_case(db: sqlite3.Connection, relation_type: str) -> None:
    a, b = add_entity(db, "A"), add_entity(db, "B")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO relations (src_id, type, dst_id) VALUES (?, ?, ?)", (a, relation_type, b)
        )


def test_relation_cannot_point_to_itself(db: sqlite3.Connection) -> None:
    a = add_entity(db, "A")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO relations (src_id, type, dst_id) VALUES (?, 'knows', ?)", (a, a))


def test_relation_is_unique(db: sqlite3.Connection) -> None:
    a, b = add_entity(db, "A"), add_entity(db, "B", entity_type="person")
    db.execute("INSERT INTO relations (src_id, type, dst_id) VALUES (?, 'managed_by', ?)", (a, b))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO relations (src_id, type, dst_id) VALUES (?, 'managed_by', ?)", (a, b)
        )


@pytest.mark.parametrize(
    ("column", "value"), [("category", "rule"), ("source", "system"), ("content", " ")]
)
def test_facts_reject_invalid_values(db: sqlite3.Connection, column: str, value: str) -> None:
    row = {"category": "note", "source": "user", "content": "x"}
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO facts (category, source, content) VALUES (:category, :source, :content)",
            row,
        )


# --- cascades ----------------------------------------------------------------------------


def test_deleting_an_entity_cascades_to_relations_and_facts(db: sqlite3.Connection) -> None:
    client, rep = add_entity(db, "Brasserie Lemaire"), add_entity(db, "Julien", "person")
    db.execute(
        "INSERT INTO relations (src_id, type, dst_id) VALUES (?, 'managed_by', ?)", (client, rep)
    )
    add_fact(db, "Compte stratégique", client)
    general = add_fact(db, "Montants en k€")
    db.execute("DELETE FROM entities WHERE id = ?", (rep,))
    assert db.execute("SELECT COUNT(*) FROM relations").fetchone() == (0,)
    db.execute("DELETE FROM entities WHERE id = ?", (client,))
    assert db.execute("SELECT id FROM facts").fetchall() == [(general,)]


# --- audit -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "before", "after", "valid"),
    [
        ("create", None, "{}", True),
        ("update", "{}", "{}", True),
        ("delete", "{}", None, True),
        ("create", "{}", "{}", False),
        ("update", None, "{}", False),
        ("update", "{}", None, False),
        ("delete", "{}", "{}", False),
        ("create", None, "not json", False),
    ],
)
def test_audit_before_and_after_match_the_operation(
    db: sqlite3.Connection, op: str, before: str | None, after: str | None, valid: bool
) -> None:
    statement = (
        "INSERT INTO memory_audit (op, kind, ref, before, after, actor)"
        " VALUES (?, 'fact', '1', ?, ?, 'agent')"
    )
    if valid:
        db.execute(statement, (op, before, after))
    else:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(statement, (op, before, after))


@pytest.mark.parametrize(("column", "value"), [("kind", "facts"), ("actor", "system")])
def test_audit_rejects_unknown_kind_or_actor(
    db: sqlite3.Connection, column: str, value: str
) -> None:
    row = {"kind": "fact", "actor": "agent"}
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO memory_audit (op, kind, ref, after, actor)"
            " VALUES ('create', :kind, '1', '{}', :actor)",
            row,
        )


# --- full-text search --------------------------------------------------------------------


def test_glossary_is_indexed_and_kept_in_sync(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO glossary (term, definition) VALUES ('client rentable', 'Marge brute > 25 %')"
    )
    assert search(db, "rentable") == {("glossary", "client rentable")}
    assert search(db, "marge") == {("glossary", "client rentable")}

    db.execute("UPDATE glossary SET term = 'compte rentable' WHERE term = 'client rentable'")
    assert search(db, "client") == set()
    assert search(db, "compte") == {("glossary", "compte rentable")}

    db.execute("DELETE FROM glossary")
    assert search(db, "rentable") == set()


def test_entity_aliases_are_searchable(db: sqlite3.Connection) -> None:
    entity = add_entity(db, "Brasserie Lemaire", aliases=["BL"])
    assert search(db, "lemaire") == {("entity", str(entity))}
    assert search(db, "BL") == {("entity", str(entity))}

    db.execute("UPDATE entities SET aliases = '[\"Lemaire SAS\"]' WHERE id = ?", (entity,))
    assert search(db, "BL") == set()
    assert search(db, "SAS") == {("entity", str(entity))}

    db.execute("DELETE FROM entities WHERE id = ?", (entity,))
    assert search(db, "lemaire") == set()


def test_facts_are_indexed_and_removed_by_cascade(db: sqlite3.Connection) -> None:
    entity = add_entity(db, "Brasserie Lemaire")
    fact = add_fact(db, "Paie toujours en retard", entity)
    assert search(db, "retard") == {("fact", str(fact))}

    db.execute("UPDATE facts SET content = 'Paie à 45 jours' WHERE id = ?", (fact,))
    assert search(db, "retard") == set()
    assert search(db, "jours") == {("fact", str(fact))}

    db.execute("DELETE FROM entities WHERE id = ?", (entity,))
    assert search(db, "jours") == set()


def test_search_ignores_accents(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO glossary (term, definition) VALUES ('délai de paiement', 'Jours entre facture et paiement')"
    )
    assert search(db, "delai") == {("glossary", "délai de paiement")}
    assert search(db, "DÉLAI") == {("glossary", "délai de paiement")}


# Pins the query rules of memory.md §3.3: raw user text must never reach MATCH.


def test_raw_french_text_is_not_a_valid_fts_query(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        search(db, "Quel est notre chiffre d'affaires ?")


def test_plural_needs_the_normalized_prefix_query(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO glossary (term, definition) VALUES ('client rentable', 'Marge brute > 25 %')"
    )
    assert search(db, "clients rentables") == set()  # no stemming in unicode61
    # "Quels sont nos clients rentables ?" once normalized (memory.md §3.3)
    assert search(db, '"client"* OR "rentable"*') == {("glossary", "client rentable")}
