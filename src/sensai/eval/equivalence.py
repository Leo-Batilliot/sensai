"""Execution accuracy (EX): is the agent's result equivalent to the reference? (judge.md §3)

1. Same number of rows.
2. Column names and extra agent columns are ignored: each reference column must match a
   distinct agent column. Every assignment is tried (results have few columns).
3. Row order counts only when the reference has a main ORDER BY; otherwise rows are
   compared as multisets.
4. Numbers are equal if |a - b| <= max(0.01, 0.001 * |b|); NULL only equals NULL; text
   is compared as is.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

Row = Sequence[Any]

ABSOLUTE_TOLERANCE = 0.01
RELATIVE_TOLERANCE = 0.001


def same_value(agent: Any, reference: Any) -> bool:
    if reference is None or agent is None:
        return agent is None and reference is None
    if _is_number(agent) and _is_number(reference):
        bound = max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(reference))
        return abs(agent - reference) <= bound
    return bool(agent == reference)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def has_main_order_by(sql: str) -> bool:
    """Whether the outermost statement sorts its rows: ORDER BY outside any parentheses."""
    sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)  # string contents may hold parentheses
    depth = 0
    tokens = re.findall(r"[()]|[A-Za-z_]+", sql)
    for index, token in enumerate(tokens):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif (
            depth == 0
            and token.upper() == "ORDER"
            and index + 1 < len(tokens)
            and tokens[index + 1].upper() == "BY"
        ):
            return True
    return False


def equivalent(agent: Sequence[Row], reference: Sequence[Row], *, ordered: bool) -> bool:
    """judge.md §3: the agent's rows give the reference rows, column by column."""
    if len(agent) != len(reference):
        return False
    if not reference:
        return True
    ref_width, agent_width = len(reference[0]), len(agent[0])
    if agent_width < ref_width:
        return False
    # Agent columns each reference column could be, judged on the values alone
    candidates = [
        [col for col in range(agent_width) if _column_matches(agent, reference, col, ref, ordered)]
        for ref in range(ref_width)
    ]
    return _assign(agent, reference, candidates, [], ordered)


def _column_matches(
    agent: Sequence[Row], reference: Sequence[Row], col: int, ref: int, ordered: bool
) -> bool:
    agent_values = [row[col] for row in agent]
    ref_values = [row[ref] for row in reference]
    if ordered:
        return all(same_value(a, r) for a, r in zip(agent_values, ref_values, strict=True))
    return _same_multiset([(value,) for value in agent_values], [(value,) for value in ref_values])


def _assign(
    agent: Sequence[Row],
    reference: Sequence[Row],
    candidates: list[list[int]],
    chosen: list[int],
    ordered: bool,
) -> bool:
    """Backtracking over the assignments of reference columns to distinct agent columns."""
    if len(chosen) == len(candidates):
        if ordered:
            return True  # every column already matched row by row
        projected = [tuple(row[col] for col in chosen) for row in agent]
        return _same_multiset(projected, [tuple(row) for row in reference])
    for col in candidates[len(chosen)]:
        if col not in chosen and _assign(agent, reference, candidates, [*chosen, col], ordered):
            return True
    return False


def _same_multiset(agent: list[tuple[Any, ...]], reference: list[tuple[Any, ...]]) -> bool:
    """Each reference row pairs with its own agent row (tolerance makes sorting unsafe)."""
    free = list(range(len(agent)))
    for ref_row in reference:
        match = next(
            (
                index
                for index in free
                if all(same_value(a, r) for a, r in zip(agent[index], ref_row, strict=True))
            ),
            None,
        )
        if match is None:
            return False
        free.remove(match)
    return True


__all__ = ["equivalent", "has_main_order_by", "same_value"]
