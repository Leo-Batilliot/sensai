"""MemoryStore (MEM-02): CRUD on the four kinds, audit of every write, FTS5 search."""

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from sensai.memory.errors import AmbiguousEntity, Conflict, EntityNotFound, InvalidPatch, NotFound
from sensai.memory.store import AuditContext, Kind, MemoryStore, normalize_name

RULE = "Marge brute > 25 % du CA HT sur la période"


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    with MemoryStore.open(":memory:") as memory:
        yield memory


@pytest.fixture
def lemaire(store: MemoryStore) -> str:
    return store.upsert_entity("client", "Brasserie Lemaire", aliases=["Lemaire", "BL"]).ref


@pytest.fixture
def julien(store: MemoryStore) -> str:
    return store.upsert_entity("person", "Julien Marchand", aliases=["Julien"]).ref


def audit_ops(store: MemoryStore) -> list[tuple[str, str, str]]:
    return [(row["op"], row["kind"], row["ref"]) for row in store.audit_log()]


# --- opening -----------------------------------------------------------------------------


def test_open_creates_the_file_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "data" / "memory.db"
    with MemoryStore.open(path) as memory:
        memory.define_term("client rentable", RULE)
    with MemoryStore.open(path) as memory:
        assert memory.get("glossary", "client rentable")["definition"] == RULE


def test_open_refuses_an_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="v99"):
        MemoryStore.open(path)


# --- glossary ----------------------------------------------------------------------------


def test_define_term_creates_then_audits(store: MemoryStore) -> None:
    result = store.define_term("client rentable", RULE, sql_hint="marge / ca > 0.25")
    assert result.to_dict() == {
        "op": "create",
        "kind": "glossary",
        "ref": "client rentable",
        "summary": "Terme « client rentable » défini",
    }
    [audit] = store.audit_log()
    assert (audit["op"], audit["kind"], audit["ref"], audit["actor"]) == (
        "create",
        "glossary",
        "client rentable",
        "agent",
    )
    assert audit["before"] is None
    assert audit["after"]["sql_hint"] == "marge / ca > 0.25"


def test_same_definition_twice_is_unchanged_and_not_audited(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE)
    assert store.define_term("client rentable", RULE).op == "unchanged"
    assert len(store.audit_log()) == 1


def test_redefining_a_term_ignores_case_and_keeps_its_ref(store: MemoryStore) -> None:
    store.define_term("Client rentable", "Marge > 20 %")
    result = store.define_term("CLIENT RENTABLE", RULE)
    assert (result.op, result.ref) == ("update", "Client rentable")
    assert store.get("glossary", "client rentable")["definition"] == RULE
    update = store.audit_log()[-1]
    assert (update["before"]["definition"], update["after"]["definition"]) == ("Marge > 20 %", RULE)


def test_term_case_is_folded_beyond_ascii(store: MemoryStore) -> None:
    store.define_term("Élan commercial", "x")
    assert store.define_term("élan commercial", "y").op == "update"
    assert len(store.dump()["glossary"]) == 1


def test_renaming_a_term_changes_its_ref(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE)
    result = store.update("glossary", "client rentable", {"term": "compte rentable"})
    assert (result.op, result.ref) == ("update", "compte rentable")
    with pytest.raises(NotFound):
        store.get("glossary", "client rentable")
    assert store.audit_log()[-1]["ref"] == "compte rentable"
    assert [hit["ref"] for hit in store.search("compte")] == ["compte rentable"]


def test_renaming_a_term_onto_another_is_a_conflict(store: MemoryStore) -> None:
    store.define_term("ca", "chiffre d'affaires")
    store.define_term("marge", "x")
    with pytest.raises(Conflict):
        store.update("glossary", "marge", {"term": "CA"})


# --- entities ----------------------------------------------------------------------------


def test_upsert_entity_creates_with_clean_aliases(store: MemoryStore) -> None:
    result = store.upsert_entity(
        "client",
        "Brasserie Lemaire",
        aliases=["Lemaire", "lemaire", "brasserie lemaire"],
        attributes={"segment": "PME"},
        external_ref="clients:42",
    )
    assert (result.op, result.kind) == ("create", "entity")
    entity = store.get("entity", result.ref)
    assert entity["aliases"] == ["Lemaire"]
    assert entity["attributes"] == {"segment": "PME"}
    assert entity["external_ref"] == "clients:42"


def test_upsert_entity_completes_the_existing_one(store: MemoryStore, lemaire: str) -> None:
    result = store.upsert_entity(
        "client", "BRASSERIE LEMAIRE", aliases=["BL", "Lemaire SAS"], attributes={"vip": True}
    )
    assert (result.op, result.ref) == ("update", lemaire)
    entity = store.get("entity", lemaire)
    assert entity["name"] == "Brasserie Lemaire"
    # "Lemaire SAS" is "Lemaire" once its legal form is left out (MEM-05)
    assert entity["aliases"] == ["Lemaire", "BL"]
    assert entity["attributes"] == {"vip": True}
    assert store.upsert_entity("client", "Brasserie Lemaire", aliases=["bl"]).op == "unchanged"


# --- aliases (MEM-05) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("Brasserie Lemaire", "brasserie lemaire"),
        ("BRASSERIE LEMAIRE S.A.R.L.", "brasserie lemaire"),
        ("Élan Bureautique SAS", "elan bureautique"),
        ("  Café-Restaurant  du Port ", "cafe restaurant du port"),
        ("SA", "sa"),  # nothing but a legal form: kept
    ],
)
def test_normalize_name(name: str, normalized: str) -> None:
    assert normalize_name(name) == normalized


def test_no_duplicate_between_an_alias_and_the_name(store: MemoryStore, lemaire: str) -> None:
    # US-M3-02: "Lemaire" is an alias of "Brasserie Lemaire": the upsert completes it
    result = store.upsert_entity("client", "Lemaire", attributes={"segment": "PME"})

    assert (result.op, result.ref) == ("update", lemaire)
    assert len(store.dump()["entities"]) == 1
    entity = store.get("entity", lemaire)
    assert (entity["name"], entity["aliases"]) == ("Brasserie Lemaire", ["Lemaire", "BL"])
    assert entity["attributes"] == {"segment": "PME"}


def test_the_full_name_given_later_becomes_an_alias(store: MemoryStore) -> None:
    first = store.upsert_entity("client", "Lemaire").ref

    result = store.upsert_entity("client", "Brasserie Lemaire SARL", aliases=["Lemaire"])

    assert (result.op, result.ref) == ("update", first)
    assert store.get("entity", first)["aliases"] == ["Brasserie Lemaire SARL"]
    assert len(store.dump()["entities"]) == 1


def test_legal_forms_and_accents_name_the_same_entity(store: MemoryStore) -> None:
    elan = store.upsert_entity("client", "Élan Bureautique").ref

    assert store.upsert_entity("client", "ELAN BUREAUTIQUE SAS").op == "unchanged"
    assert store.resolve_entity("elan bureautique sarl") == int(elan)


def test_an_update_keeps_the_old_value_in_the_audit(store: MemoryStore, lemaire: str) -> None:
    store.upsert_entity("client", "Lemaire", external_ref="clients:43")
    store.upsert_entity("client", "BL", external_ref="clients:81")

    assert store.get("entity", lemaire)["external_ref"] == "clients:81"
    updates = [row for row in store.audit_log("entity", lemaire) if row["op"] == "update"]
    assert updates[-1]["before"]["external_ref"] == "clients:43"


def test_aliases_shared_by_two_entities_are_ambiguous(store: MemoryStore) -> None:
    store.upsert_entity("client", "Brasserie Lemaire", aliases=["Lemaire"])
    store.upsert_entity("client", "Lemaire Frères", aliases=["Frères Lemaire"])

    with pytest.raises(AmbiguousEntity):
        store.upsert_entity("client", "Maison Lemaire", aliases=["Lemaire", "Frères Lemaire"])


def test_same_name_with_another_type_is_another_entity(store: MemoryStore, lemaire: str) -> None:
    result = store.upsert_entity("person", "Brasserie Lemaire")
    assert result.op == "create"
    assert result.ref != lemaire


def test_renaming_an_entity_onto_another_is_a_conflict(store: MemoryStore) -> None:
    store.upsert_entity("client", "Élan Bureautique")
    other = store.upsert_entity("client", "Atelier Morel").ref
    with pytest.raises(Conflict):
        store.update("entity", other, {"name": "ÉLAN BUREAUTIQUE"})


def test_entity_patch_values_are_checked(store: MemoryStore, lemaire: str) -> None:
    for patch in (
        {"aliases": "Lemaire"},
        {"attributes": ["x"]},
        {"type": "supplier"},
        {"external_ref": "Clients:4"},
        {"name": None},
        {"name": 3},
    ):
        with pytest.raises(InvalidPatch):
            store.update("entity", lemaire, patch)
    assert len(store.audit_log()) == 1


# --- entity designation (memory.md §3.1) -------------------------------------------------


def test_resolve_entity_by_ref_name_or_alias(store: MemoryStore, lemaire: str) -> None:
    for designation in (lemaire, "brasserie lemaire", "LEMAIRE", "bl"):
        assert store.resolve_entity(designation) == int(lemaire)


def test_resolve_entity_prefers_the_name_over_an_alias(store: MemoryStore, lemaire: str) -> None:
    lemaire_person = store.upsert_entity("person", "Lemaire").ref
    assert store.resolve_entity("Lemaire") == int(lemaire_person)
    assert store.resolve_entity("BL") == int(lemaire)


def test_resolve_entity_reports_ambiguity_and_absence(store: MemoryStore, lemaire: str) -> None:
    person = store.upsert_entity("person", "Brasserie Lemaire").ref
    with pytest.raises(AmbiguousEntity) as excinfo:
        store.resolve_entity("Brasserie Lemaire")
    assert excinfo.value.code == "ambiguous_entity"
    assert {ref for _, _, ref in excinfo.value.candidates} == {lemaire, person}
    for designation in ("Inconnu", "999"):
        with pytest.raises(EntityNotFound):
            store.resolve_entity(designation)


# --- relations ---------------------------------------------------------------------------


def test_relate_creates_once(store: MemoryStore, lemaire: str, julien: str) -> None:
    result = store.relate("Lemaire", "managed_by", "Julien")
    assert result.op == "create"
    assert result.summary == "Relation Brasserie Lemaire —managed_by→ Julien Marchand créée"
    assert store.get("relation", result.ref) | {"updated_at": None} == {
        "ref": result.ref,
        "src": lemaire,
        "type": "managed_by",
        "dst": julien,
        "attributes": {},
        "updated_at": None,
    }
    again = store.relate(lemaire, "managed_by", julien)
    assert (again.op, again.ref) == ("unchanged", result.ref)
    assert len(store.audit_log(kind="relation")) == 1


@pytest.mark.parametrize(
    ("src", "relation_type", "dst", "error"),
    [
        ("Lemaire", "managed_by", "Inconnu", EntityNotFound),
        ("Lemaire", "Managed By", "Julien", InvalidPatch),
        ("Lemaire", "knows", "BL", InvalidPatch),
    ],
)
def test_relate_rejects_bad_links(
    store: MemoryStore,
    lemaire: str,
    julien: str,
    src: str,
    relation_type: str,
    dst: str,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        store.relate(src, relation_type, dst)
    assert store.dump()["relations"] == []
    assert audit_ops(store) == [("create", "entity", lemaire), ("create", "entity", julien)]


def test_changing_a_relation_onto_an_existing_one_is_a_conflict(
    store: MemoryStore, lemaire: str, julien: str
) -> None:
    store.relate("Lemaire", "managed_by", "Julien")
    other = store.relate("Lemaire", "knows", "Julien").ref
    with pytest.raises(Conflict):
        store.update("relation", other, {"type": "managed_by"})


def test_relation_endpoints_cannot_be_patched(
    store: MemoryStore, lemaire: str, julien: str
) -> None:
    ref = store.relate("Lemaire", "managed_by", "Julien").ref
    with pytest.raises(InvalidPatch, match="src_id"):
        store.update("relation", ref, {"src_id": 1})


# --- facts -------------------------------------------------------------------------------


def test_add_fact_general_or_attached(store: MemoryStore, lemaire: str) -> None:
    general = store.add_fact("Claire veut les montants en k€", "preference", source="user")
    attached = store.add_fact("Compte stratégique", "note", entity="BL")
    assert store.get("fact", general.ref)["entity"] is None
    assert store.get("fact", general.ref)["source"] == "user"
    assert store.get("fact", attached.ref)["entity"] == lemaire


def test_add_fact_rejects_an_unknown_category(store: MemoryStore) -> None:
    with pytest.raises(InvalidPatch):
        store.add_fact("x", "rule")
    assert store.audit_log() == []


def test_fact_can_be_moved_to_another_entity(store: MemoryStore, lemaire: str, julien: str) -> None:
    ref = store.add_fact("Paie à 45 jours", "note", entity="Lemaire").ref
    store.update("fact", ref, {"entity": "Julien", "content": "Relance le lundi"})
    fact = store.get("fact", ref)
    assert (fact["entity"], fact["content"]) == (julien, "Relance le lundi")


# --- update, generic rules ---------------------------------------------------------------


def test_update_rejects_unknown_fields_and_empty_patches(store: MemoryStore) -> None:
    store.define_term("ca", "chiffre d'affaires")
    for patch in ({"created_at": "2026-01-01T00:00:00Z"}, {"id": 3}, {}):
        with pytest.raises(InvalidPatch):
            store.update("glossary", "ca", patch)


@pytest.mark.parametrize(
    ("kind", "ref", "patch"),
    [
        ("glossary", "inconnu", {"definition": "x"}),
        ("entity", "42", {"name": "x"}),
        ("relation", "42", {"type": "knows"}),
        ("fact", "abc", {"content": "x"}),
    ],
)
def test_update_and_delete_of_a_missing_entry(
    store: MemoryStore, kind: Kind, ref: str, patch: dict[str, str]
) -> None:
    with pytest.raises(NotFound) as excinfo:
        store.update(kind, ref, patch)
    assert excinfo.value.code == "not_found"
    with pytest.raises(NotFound):
        store.delete(kind, ref)


def test_update_sets_updated_at_and_audits_before_and_after(
    store: MemoryStore, lemaire: str
) -> None:
    store.update("entity", lemaire, {"external_ref": "clients:42"})
    entity = store.get("entity", lemaire)
    datetime.strptime(entity["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    update = store.audit_log(kind="entity", ref=lemaire)[-1]
    assert update["op"] == "update"
    assert (update["before"]["external_ref"], update["after"]["external_ref"]) == (
        None,
        "clients:42",
    )


def test_audit_records_the_user_and_the_session(store: MemoryStore) -> None:
    store.define_term("ca", "x", ctx=AuditContext(actor="user", session_id="s-1"))
    [audit] = store.audit_log()
    assert (audit["actor"], audit["session_id"]) == ("user", "s-1")


# --- delete ------------------------------------------------------------------------------


def test_delete_a_term(store: MemoryStore) -> None:
    store.define_term("Client rentable", RULE)
    result = store.delete("glossary", "client rentable")
    assert (result.op, result.ref) == ("delete", "Client rentable")
    assert store.dump()["glossary"] == []
    assert store.search("rentable") == []
    delete = store.audit_log()[-1]
    assert (delete["op"], delete["before"]["definition"], delete["after"]) == ("delete", RULE, None)


def test_delete_impact_of_an_entity_counts_the_cascades(
    store: MemoryStore, lemaire: str, julien: str
) -> None:
    relation = store.relate("Lemaire", "managed_by", "Julien").ref
    fact = store.add_fact("Compte stratégique", "note", entity="Lemaire").ref
    store.add_fact("Montants en k€", "preference")
    audit_before = store.audit_log()

    impact = store.delete_impact("entity", lemaire)

    assert impact == {
        "operation": "DELETE",
        "rows_affected": 1,
        "per_table": {"entities": -1, "relations": -1, "facts": -1},
        "sample": [
            {"kind": "entity", "ref": lemaire, "label": "Brasserie Lemaire"},
            {
                "kind": "relation",
                "ref": relation,
                "label": "Brasserie Lemaire —managed_by→ Julien Marchand",
            },
            {"kind": "fact", "ref": fact, "label": "Compte stratégique"},
        ],
        "blocked": [],
    }
    # a preview writes nothing
    assert store.audit_log() == audit_before
    assert len(store.dump()["facts"]) == 2


def test_delete_impact_matches_what_delete_removes(
    store: MemoryStore, lemaire: str, julien: str
) -> None:
    store.relate("Lemaire", "managed_by", "Julien")
    for index in range(6):
        store.add_fact(f"Note {index}", "note", entity="Julien")
    impact = store.delete_impact("entity", julien)
    before = {table: len(rows) for table, rows in store.dump().items()}

    store.delete("entity", julien)

    after = {table: len(rows) for table, rows in store.dump().items()}
    assert {
        table: after[table] - before[table] for table in before if after[table] != before[table]
    } == impact["per_table"]
    assert len(impact["sample"]) == 5


@pytest.mark.parametrize(
    ("kind", "label"), [("glossary", "Client rentable"), ("fact", "Montants en k€")]
)
def test_delete_impact_of_a_single_entry(store: MemoryStore, kind: Kind, label: str) -> None:
    ref = (
        store.define_term("Client rentable", RULE).ref
        if kind == "glossary"
        else store.add_fact("Montants en k€", "preference").ref
    )
    table = "glossary" if kind == "glossary" else "facts"
    impact = store.delete_impact(kind, ref.lower() if kind == "glossary" else ref)
    assert impact["per_table"] == {table: -1}
    assert impact["sample"] == [{"kind": kind, "ref": ref, "label": label}]


def test_delete_impact_of_a_missing_entry(store: MemoryStore) -> None:
    with pytest.raises(NotFound):
        store.delete_impact("entity", "42")


def test_deleting_an_entity_audits_every_cascaded_entry(
    store: MemoryStore, lemaire: str, julien: str
) -> None:
    relation = store.relate("Lemaire", "managed_by", "Julien").ref
    fact = store.add_fact("Compte stratégique", "note", entity="Lemaire").ref
    general = store.add_fact("Montants en k€", "preference").ref

    result = store.delete("entity", lemaire)

    assert (
        result.summary
        == "Entité « Brasserie Lemaire » : supprimé (avec 1 relation(s) et 1 fait(s))"
    )
    assert audit_ops(store)[-3:] == [
        ("delete", "relation", relation),
        ("delete", "fact", fact),
        ("delete", "entity", lemaire),
    ]
    memory = store.dump()
    assert [row["ref"] for row in memory["entities"]] == [julien]
    assert memory["relations"] == []
    assert [row["ref"] for row in memory["facts"]] == [general]
    assert store.search("stratégique") == []


# --- search ------------------------------------------------------------------------------


def test_search_takes_raw_french_questions(store: MemoryStore, lemaire: str) -> None:
    store.define_term("client rentable", RULE, sql_hint="marge / ca > 0.25")
    store.define_term("chiffre d'affaires", "Somme des factures HT")
    assert store.search("Quels sont nos clients rentables ?") == [
        {
            "kind": "glossary",
            "ref": "client rentable",
            "label": "client rentable",
            "snippet": f"client rentable : {RULE}",
            "sql_hint": "marge / ca > 0.25",
        }
    ]
    assert [hit["ref"] for hit in store.search("Quel est notre chiffre d'affaires ?")] == [
        "chiffre d'affaires"
    ]
    assert store.search("lemaire")[0] == {
        "kind": "entity",
        "ref": lemaire,
        "label": "Brasserie Lemaire",
        "snippet": "Brasserie Lemaire (Lemaire, BL)",
    }


def test_search_ignores_accents_and_filters_by_kind(store: MemoryStore, lemaire: str) -> None:
    store.define_term("délai de paiement", "Jours entre facture et paiement")
    fact = store.add_fact("Lemaire paie avec du délai", "note", entity="Lemaire").ref
    assert {hit["ref"] for hit in store.search("delai")} == {"délai de paiement", fact}
    assert [hit["ref"] for hit in store.search("delai", kind="fact")] == [fact]
    assert [hit["kind"] for hit in store.search("lemaire", kind="entity")] == ["entity"]


def test_search_respects_the_limit_and_empty_queries(store: MemoryStore) -> None:
    for index in range(8):
        store.add_fact(f"Remise client numéro {index}", "note")
    assert len(store.search("remise")) == 5
    assert len(store.search("remise", limit=2)) == 2
    assert store.search("Quel est le ?") == []


# --- dump --------------------------------------------------------------------------------


def test_dump_has_the_shape_of_the_memory_api(store: MemoryStore, lemaire: str) -> None:
    store.define_term("ca", "chiffre d'affaires")
    memory = store.dump()
    assert set(memory) == {"glossary", "entities", "relations", "facts"}
    assert set(memory["glossary"][0]) == {"ref", "term", "definition", "sql_hint", "updated_at"}
    assert set(memory["entities"][0]) == {
        "ref",
        "type",
        "name",
        "aliases",
        "external_ref",
        "attributes",
        "updated_at",
    }
