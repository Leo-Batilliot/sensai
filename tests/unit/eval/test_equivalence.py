"""Execution accuracy (EX): equivalence of two results, judge.md §3."""

import pytest

from sensai.eval.equivalence import equivalent, has_main_order_by, same_value

TOP = [("Brasserie Lemaire", 8808.3), ("Imprimerie Vasseur", 7128.57)]


@pytest.mark.parametrize(
    ("agent", "reference", "expected"),
    [
        (8808.3, 8808.3, True),
        (8808.31, 8808.3, True),  # within 0.01
        (8817.0, 8808.3, True),  # within 0.1 % of the reference
        (8820.0, 8808.3, False),
        (40, 40.0, True),
        (None, None, True),
        (None, 0, False),
        (0, None, False),
        ("Lemaire", "Lemaire", True),
        ("lemaire", "Lemaire", False),  # text as is
        ("40", 40, False),
    ],
)
def test_same_value(agent: object, reference: object, expected: bool) -> None:
    assert same_value(agent, reference) is expected


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT name FROM clients ORDER BY name LIMIT 5", True),
        ("select name from clients order  by name", True),
        ("SELECT COUNT(*) FROM clients", False),
        ("SELECT * FROM (SELECT name FROM clients ORDER BY name)", False),
        (
            "WITH m AS (SELECT client_id FROM orders ORDER BY 1) SELECT * FROM m ORDER BY 1",
            True,
        ),
        ("SELECT 'ORDER BY' AS label FROM clients", False),
        ("SELECT name FROM clients -- ORDER BY name", False),
    ],
)
def test_main_order_by(sql: str, expected: bool) -> None:
    assert has_main_order_by(sql) is expected


def test_same_rows_in_the_same_order() -> None:
    assert equivalent([list(row) for row in TOP], TOP, ordered=True)


def test_order_counts_only_when_the_reference_sorts() -> None:
    swapped = [list(TOP[1]), list(TOP[0])]

    assert not equivalent(swapped, TOP, ordered=True)
    assert equivalent(swapped, TOP, ordered=False)


def test_column_names_order_and_extra_columns_are_ignored() -> None:
    agent = [[8808.3, 47.3, "Brasserie Lemaire"], [7128.57, 30.8, "Imprimerie Vasseur"]]

    assert equivalent(agent, TOP, ordered=True)


def test_each_reference_column_needs_its_own_agent_column() -> None:
    reference = [(5, 5), (7, 7)]

    assert not equivalent([[5], [7]], reference, ordered=True)
    assert equivalent([[5, 5], [7, 7]], reference, ordered=True)


def test_ambiguous_columns_try_every_assignment() -> None:
    # Column 0 of the agent could be either reference column, but only one assignment works
    agent = [[1, 1, 2], [2, 2, 3]]
    reference = [(1, 2), (2, 3)]

    assert equivalent(agent, reference, ordered=True)


def test_unordered_rows_are_compared_as_multisets() -> None:
    reference = [("a", 1), ("b", 2), ("a", 1)]

    assert equivalent([["a", 1], ["a", 1], ["b", 2]], reference, ordered=False)
    assert not equivalent([["a", 1], ["b", 2], ["b", 2]], reference, ordered=False)


def test_rows_must_pair_whole_not_column_by_column() -> None:
    # Same values in each column, but not on the same rows
    reference = [("a", 1), ("b", 2)]

    assert not equivalent([["a", 2], ["b", 1]], reference, ordered=False)


def test_row_count_and_missing_columns() -> None:
    assert not equivalent([list(TOP[0])], TOP, ordered=False)
    assert not equivalent([["Brasserie Lemaire"], ["Imprimerie Vasseur"]], TOP, ordered=True)
    assert equivalent([], [], ordered=False)
