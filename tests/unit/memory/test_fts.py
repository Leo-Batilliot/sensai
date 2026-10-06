"""FTS5 query builder: rules of docs/contracts/memory.md §3.3."""

import pytest

from sensai.memory.fts import build_fts_query, query_words
from sensai.memory.schema import connect, create_schema


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Quels sont nos clients rentables ?", '"client"* OR "rentable"*'),
        ("Quel est notre chiffre d'affaires ?", '"chiffre"* OR "affaire"*'),
        ("Délai moyen de paiement", '"délai"* OR "moyen"* OR "paiement"*'),
        ("client, Client, CLIENTS", '"client"*'),
        ("prix HT", '"pri"* OR "ht"*'),
        ("top-5 l'an", '"top"* OR "an"*'),
        ("snake_case", '"snake"* OR "case"*'),
    ],
)
def test_build_fts_query(text: str, expected: str) -> None:
    assert build_fts_query(text) == expected


@pytest.mark.parametrize("text", ["", "  ", "?", "Quel est le ?", "a b c", "\"'*"])
def test_nothing_left_to_search_gives_none(text: str) -> None:
    assert build_fts_query(text) is None


def test_short_words_and_stop_words_are_dropped() -> None:
    assert query_words("Les CA des clients de la région") == ["ca", "client", "région"]


@pytest.mark.parametrize(
    "text",
    ['"; DROP TABLE x', "NEAR(a b)", "client AND OR NOT", "x* ^y {kind}: z", "c'est-à-dire ?"],
)
def test_any_text_gives_a_valid_match_expression(text: str) -> None:
    conn = connect(":memory:")
    create_schema(conn)
    query = build_fts_query(text)
    assert query is not None
    conn.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH ?", (query,)).fetchall()
    conn.close()
