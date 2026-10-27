"""DocumentStore (ARTIF-01, M4): the committee note, section by section, versioned."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from sensai.documents.store import (
    DOC_ID,
    MAX_CONTENT,
    ContentTooLong,
    DocumentStore,
    InvalidPatch,
    RevisionConflict,
    SectionNotFound,
    load_template,
    slug,
)
from sensai.memory.schema import open_database
from sensai.memory.store import AuditContext, MemoryStore

SECTIONS = ["synthese", "chiffres-cles", "rentabilite", "encours-retards", "actions"]
USER = AuditContext(actor="user", session_id="s-1")


@pytest.fixture
def note() -> Iterator[DocumentStore]:
    with DocumentStore(open_database(":memory:")) as store:
        yield store


def sections(note: DocumentStore) -> list[dict[str, object]]:
    return note.to_dict()["sections"]


def ids(note: DocumentStore) -> list[str]:
    return [section["section_id"] for section in note.outline()["sections"]]


# --- creation and reading ----------------------------------------------------------------


def test_a_new_note_follows_its_template(note: DocumentStore) -> None:
    plan = note.outline()

    assert (plan["doc_id"], plan["title"], plan["revision"]) == (
        DOC_ID,
        "Note au comité de direction",
        0,
    )
    assert ids(note) == SECTIONS
    assert all(section["chars"] == 0 for section in plan["sections"])
    assert note.revisions() == []


def test_the_template_is_package_data() -> None:
    template = load_template("note_comite")
    assert [section_id for section_id, _ in template.sections] == SECTIONS


def test_read_a_section(note: DocumentStore) -> None:
    note.patch("rentabilite", "replace", "Top 5 du trimestre.", 0)

    assert note.read("rentabilite") == {
        "doc_id": DOC_ID,
        "revision": 1,
        "section_id": "rentabilite",
        "title": "Rentabilité",
        "content": "Top 5 du trimestre.",
    }
    assert note.read() == note.outline()


def test_the_outline_for_the_prompt_has_titles_and_revision_only(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "Secret de la synthèse.", 0)

    text = note.outline_text()

    assert "révision 1" in text
    assert "- synthese : Synthèse (22 caractères)" in text
    assert "- actions : Actions proposées (vide)" in text
    assert "Secret" not in text  # contents only come from document_read


# --- patches -------------------------------------------------------------------------------


def test_a_patch_only_touches_one_section(note: DocumentStore) -> None:
    for section_id in SECTIONS:
        note.patch(section_id, "replace", f"Texte de {section_id}.", note.revision)
    before = {s["section_id"]: (s["content"], s["updated_at"]) for s in sections(note)}

    result = note.patch("rentabilite", "replace", "Nouveau texte.", note.revision)

    after = {s["section_id"]: (s["content"], s["updated_at"]) for s in sections(note)}
    assert {k: v for k, v in after.items() if k != "rentabilite"} == {
        k: v for k, v in before.items() if k != "rentabilite"
    }
    assert after["rentabilite"][0] == "Nouveau texte."
    assert result.to_dict() == {
        "doc_id": DOC_ID,
        "revision": 6,
        "op": "replace",
        "section_id": "rentabilite",
        "summary": "Section « Rentabilité » mise à jour (révision 6)",
    }


def test_append_adds_a_paragraph(note: DocumentStore) -> None:
    note.patch("actions", "append", "Relancer Lemaire.", 0)
    note.patch("actions", "append", "Revoir les conditions.", 1)

    assert note.read("actions")["content"] == "Relancer Lemaire.\n\nRevoir les conditions."


def test_insert_after_creates_a_section_and_shifts_the_next_ones(note: DocumentStore) -> None:
    result = note.patch("rentabilite", "insert_after", "Trois clients.", 0, title="Top clients")

    assert result.section_id == "top-clients"
    assert ids(note) == ["synthese", "chiffres-cles", "rentabilite", "top-clients", "encours-retards", "actions"]  # fmt: skip
    positions = [section["position"] for section in sections(note)]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert note.read("top-clients")["content"] == "Trois clients."


def test_inserted_section_ids_stay_unique() -> None:
    assert slug("Écarts & risques : T4") == "ecarts-risques-t4"
    with DocumentStore(open_database(":memory:")) as note:
        note.patch("synthese", "insert_after", "", 0, title="Rentabilité")
        assert ids(note)[1] == "rentabilite-2"


def test_a_write_that_changes_nothing_records_nothing(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "Même texte.", 0)

    result = note.patch("synthese", "replace", "Même texte.", 1)

    assert (result.changed, result.revision, note.revision) == (False, 1, 1)
    assert len(note.revisions()) == 1


def test_a_stale_revision_is_a_conflict(note: DocumentStore) -> None:
    # Edited from the interface meanwhile: the model must read again (memory.md §7.1)
    note.patch("synthese", "replace", "Par l'utilisateur.", 0, ctx=USER)

    with pytest.raises(RevisionConflict, match="révision courante 1") as exc:
        note.patch("synthese", "replace", "Par l'agent.", 0)

    assert exc.value.code == "REVISION_CONFLICT"
    assert note.read("synthese")["content"] == "Par l'utilisateur."


def test_errors_of_a_patch(note: DocumentStore) -> None:
    with pytest.raises(SectionNotFound, match="Sections : synthese"):
        note.patch("risques", "replace", "x", 0)
    with pytest.raises(ContentTooLong, match="append") as too_long:
        note.patch("synthese", "replace", "x" * (MAX_CONTENT + 1), 0)
    with pytest.raises(InvalidPatch, match="titre"):
        note.patch("synthese", "insert_after", "x", 0)
    assert (too_long.value.code, note.revision) == ("content_too_long", 0)


# --- deletion, revisions and restore ----------------------------------------------------


def test_delete_impact_and_deletion(note: DocumentStore) -> None:
    note.patch("encours-retards", "replace", "Trois factures.", 0)

    assert note.delete_impact("encours-retards") == {
        "operation": "DELETE",
        "rows_affected": 1,
        "per_table": {"document_sections": -1},
        "sample": [
            {
                "section_id": "encours-retards",
                "title": "Encours et retards de paiement",
                "chars": 15,
            }
        ],  # fmt: skip
        "blocked": [],
    }
    result = note.delete_section("encours-retards", 1)

    assert (result.op, result.revision) == ("delete", 2)
    assert "encours-retards" not in ids(note)


def test_every_write_is_a_revision_with_its_author(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "A", 0)
    note.patch("synthese", "append", "B", 1, ctx=USER)
    note.delete_section("actions", 2)

    assert [(r["revision"], r["op"], r["section_id"], r["actor"]) for r in note.revisions()] == [
        (1, "replace", "synthese", "agent"),
        (2, "append", "synthese", "user"),
        (3, "delete", "actions", "agent"),
    ]


def test_restore_creates_a_new_revision_with_the_old_content(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "Version 1.", 0)  # revision 1
    snapshot = sections(note)
    note.patch("synthese", "replace", "Version 2.", 1)
    note.patch("rentabilite", "insert_after", "Ajout.", 2, title="Top clients")
    note.delete_section("actions", 3)

    result = note.restore(1, ctx=USER)

    assert (result.op, result.revision, result.section_id) == ("restore", 5, None)
    restored = sections(note)
    assert [(s["section_id"], s["position"], s["title"], s["content"]) for s in restored] == [
        (s["section_id"], s["position"], s["title"], s["content"]) for s in snapshot
    ]
    assert len(note.revisions()) == 5  # nothing is erased


def test_restore_then_undo_the_restore(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "Version 1.", 0)
    note.patch("synthese", "replace", "Version 2.", 1)
    note.restore(1)

    note.restore(2)

    assert note.read("synthese")["content"] == "Version 2."


def test_restore_an_unknown_revision(note: DocumentStore) -> None:
    with pytest.raises(SectionNotFound, match="Révision 3 inconnue"):
        note.restore(3)
    assert note.restore(0).changed is False


def test_to_markdown_is_stable(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "Le trimestre est bon.\n", 0)

    text = note.to_markdown()

    assert text == note.to_markdown()
    assert text.startswith(
        "# Note au comité de direction\n\n## Synthèse\n\nLe trimestre est bon.\n"
    )
    assert "## Chiffres clés\n\n## Rentabilité" in text  # an empty section keeps its title


def test_revisions_record_whole_sections(note: DocumentStore) -> None:
    note.patch("synthese", "replace", "A", 0)

    row = note._conn.execute("SELECT before, after FROM document_revisions").fetchone()

    assert json.loads(row[0]) == {"section_id": "synthese", "position": 1, "title": "Synthèse", "content": ""}  # fmt: skip
    assert json.loads(row[1])["content"] == "A"


# --- same database as the memory -------------------------------------------------------


def test_the_note_lives_in_memory_db_apart_from_the_memory(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with MemoryStore.open(path) as memory, DocumentStore.open(path) as note:
        note.patch("synthese", "replace", "Brasserie Lemaire en tête.", 0)

        assert memory.search("Lemaire") == []  # never a memory entry (memory.md §7.1)
        assert all(not rows for rows in memory.dump().values())

    with DocumentStore.open(path) as again:
        assert again.read("synthese")["content"] == "Brasserie Lemaire en tête."
