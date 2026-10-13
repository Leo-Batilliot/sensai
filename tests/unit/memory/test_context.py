"""Memory context of a turn (MEM-04): selection, priority, token budget, refs."""

from collections.abc import Iterator

import pytest

from sensai.core.events import MemoryRef
from sensai.memory.context import EMPTY, INSTRUCTION, MemoryContext, estimate_tokens
from sensai.memory.store import MemoryStore

RULE = "Marge brute > 25 % du CA HT sur la période"
HINT = "marge_brute > 0.25 * ca_ht"


@pytest.fixture
def store() -> Iterator[MemoryStore]:
    with MemoryStore.open(":memory:") as memory:
        yield memory


def test_a_glossary_term_of_the_question_is_given_with_its_sql(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE, sql_hint=HINT)

    context = MemoryContext(store).for_question("Quels sont nos clients rentables ce trimestre ?")

    assert context.refs == (MemoryRef("glossary", "client rentable", "client rentable"),)
    assert context.text.startswith(INSTRUCTION)
    assert f"- Terme « client rentable » : {RULE} (en SQL : {HINT})" in context.text


def test_terms_match_without_accents_or_case(store: MemoryStore) -> None:
    store.define_term("Délai de paiement", "Jours entre facture et paiement")

    context = MemoryContext(store).for_question("Quel est le DELAI DE PAIEMENT moyen ?")

    assert [ref.ref for ref in context.refs] == ["Délai de paiement"]


def test_nothing_relevant_gives_the_empty_context(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE)

    assert MemoryContext(store).for_question("Quelle est la météo ?") == EMPTY
    assert MemoryContext(store).for_question("") == EMPTY


def test_an_entity_is_given_with_its_aliases_and_reference(store: MemoryStore) -> None:
    ref = store.upsert_entity(
        "client",
        "Brasserie Lemaire",
        aliases=["Lemaire", "BL"],
        external_ref="clients:42",
        attributes={"compte_strategique": True},
    ).ref

    context = MemoryContext(store).for_question("Combien a commandé Lemaire en septembre ?")

    assert context.refs == (MemoryRef("entity", ref, "Brasserie Lemaire"),)
    assert (
        "- client « Brasserie Lemaire », aussi appelé Lemaire, BL ; dans la base : clients:42"
        " ; compte_strategique = True"
    ) in context.text


def test_a_fact_is_given_with_its_category_and_a_short_label(store: MemoryStore) -> None:
    content = "Claire veut les montants de marge en k€, arrondis à l'unité, dans chaque réponse"
    ref = store.add_fact(content, "preference").ref

    context = MemoryContext(store).for_question("Quelle est la marge du mois ?")

    (fact,) = context.refs
    assert (fact.kind, fact.ref) == ("fact", ref)
    assert len(fact.label) == 60 and fact.label.endswith("…")
    assert f"- Fait (préférence) : {content}" in context.text


def test_glossary_matches_come_first_and_entries_are_not_repeated(store: MemoryStore) -> None:
    store.add_fact("Les clients rentables sont suivis chaque mois", "note")
    store.define_term("client rentable", RULE)

    context = MemoryContext(store).for_question("Liste des clients rentables")

    assert [ref.kind for ref in context.refs] == ["glossary", "fact"]


def test_at_most_max_per_kind_entries_of_each_kind(store: MemoryStore) -> None:
    for index in range(4):
        store.add_fact(f"Remise accordée numéro {index}", "note")

    context = MemoryContext(store, max_per_kind=2).for_question("Quelles remises ?")

    assert len(context.refs) == 2


def test_the_token_budget_is_respected(store: MemoryStore) -> None:
    for index in range(5):
        store.add_fact(f"Remise {index} : " + "détail " * 30, "note")
    budget = estimate_tokens(INSTRUCTION) + 2 * (estimate_tokens("- Fait (note) : Remise") + 60)

    context = MemoryContext(store, token_budget=budget).for_question("remise")

    assert 0 < len(context.refs) < 5
    assert estimate_tokens(context.text) <= budget + len(context.refs)


def test_a_broken_store_gives_no_context(store: MemoryStore) -> None:
    store.define_term("client rentable", RULE)
    store.close()  # every read now fails

    assert MemoryContext(store).for_question("clients rentables") == EMPTY


def test_a_word_of_a_definition_does_not_bring_the_term(store: MemoryStore) -> None:
    # FTS5 indexes the definitions: « client » would bring every term in
    store.define_term("mauvais payeur", "Client dont le délai moyen de paiement dépasse 60 jours")

    assert MemoryContext(store).for_question("Combien de clients actifs ?") == EMPTY
