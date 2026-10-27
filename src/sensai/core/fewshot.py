"""Dynamic examples (A7, FEWSHOT-01): the validated question -> SQL pairs closest to a question.

Contract: docs/contracts/prompts.md §2. The bank (data/examples.jsonl) is kept apart from
the golden set, so that the evaluation never measures an answer it was shown. For each
question, an in-memory FTS5 index (bm25 on the question and the tags of each example)
gives the k closest examples; they fill the $examples hole of the prompts that have one,
and the engine emits examples_injected.

The question goes through memory.fts.build_fts_query, never raw to MATCH (memory.md
§3.3). Relative dates stay written with :today, as in the bank: the model replaces it
with the date of the date tool, like in the v1 prompt.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from sensai.core.events import ExampleRef
from sensai.memory.fts import build_fts_query

BANK_FILE = "examples.jsonl"

INSTRUCTION = (
    "Requêtes déjà validées sur des questions proches : inspire-toi de leur forme, sans les "
    "recopier si la question diffère. Remplace :today par la date renvoyée par l'outil de date."
)


@dataclass(frozen=True)
class Example:
    """One line of the bank (schemas/example.v1.json)."""

    id: str
    question: str
    sql: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Selected:
    """An example chosen for a question, with its positive bm25 score."""

    example: Example
    score: float

    def ref(self) -> ExampleRef:
        return ExampleRef(self.example.id, self.example.question, self.score)


def load_bank(text: str | None = None) -> list[Example]:
    """Every example of the bank of the package (or of `text`), in file order."""
    if text is None:
        text = resources.files("sensai.data").joinpath(BANK_FILE).read_text(encoding="utf-8")
    examples = []
    for line in text.splitlines():
        if line.strip():
            row = json.loads(line)
            examples.append(Example(row["id"], row["question"], row["sql"], tuple(row["tags"])))
    return examples


class ExampleIndex:
    """FTS5 index of the bank, built once and only read afterwards."""

    def __init__(self, examples: Sequence[Example]) -> None:
        self.examples = {example.id: example for example in examples}
        # Shared by the sessions of one event loop; nothing writes after the build
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            "CREATE VIRTUAL TABLE examples_fts USING fts5("
            "id UNINDEXED, question, tags, tokenize = 'unicode61 remove_diacritics 2')"
        )
        with self._conn:
            self._conn.executemany(
                "INSERT INTO examples_fts (id, question, tags) VALUES (?, ?, ?)",
                [
                    (e.id, e.question, " ".join(tag.replace("_", " ") for tag in e.tags))
                    for e in examples
                ],
            )

    @classmethod
    def from_package(cls) -> ExampleIndex:
        return cls(load_bank())

    def close(self) -> None:
        self._conn.close()

    def select(self, question: str, k: int, min_score: float = 0.0) -> list[Selected]:
        """The k closest examples, best first, with a score of at least min_score.

        Ties keep the order of the bank, so that the same question always gets the same
        examples. Nothing to search for, or k = 0: no example.
        """
        query = build_fts_query(question)
        if query is None or k <= 0:
            return []
        rows = self._conn.execute(
            "SELECT id, bm25(examples_fts) FROM examples_fts WHERE examples_fts MATCH ?"
            " ORDER BY bm25(examples_fts), rowid LIMIT ?",
            (query, k),
        ).fetchall()
        # bm25() is negative, lower is closer: its opposite is the score of the contract
        chosen = [Selected(self.examples[id_], round(-rank, 4)) for id_, rank in rows]
        return [selected for selected in chosen if selected.score >= min_score]


def examples_block(selected: Sequence[Selected]) -> str:
    """The text of the $examples hole: the instruction, then each question and its SQL."""
    lines = [INSTRUCTION]
    for item in selected:
        lines += ["", f"Question : {item.example.question}", f"SQL : {item.example.sql}"]
    return "\n".join(lines)


__all__ = [
    "BANK_FILE",
    "Example",
    "ExampleIndex",
    "Selected",
    "examples_block",
    "load_bank",
]
