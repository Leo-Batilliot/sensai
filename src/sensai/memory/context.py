"""Memory context of a turn (MEM-04): what the model is told before it answers.

PROJECT §6.4, memory.md §4 (point P-2):
1. Glossary terms whose words all appear in the question (matched in Python on the
   normalized text, plurals and accents included), then the best FTS5 matches of the
   question among entities and facts. FTS5 is not used for the glossary: it indexes
   the definitions too, and a word as common as « client » would bring in every term.
2. At most MAX_PER_KIND entries per kind, under a fixed token budget.
3. A "contexte métier mémorisé" block for the system prompt, with the instruction to
   apply it and to say so when it changes the SQL; and the refs of the context_injected
   event, which says what was given to the model, not what it applied.

Token counts are estimated (about 4 characters per token in French): good enough for a
budget, and no tokenizer is needed.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from sensai.core.events import MemoryKind, MemoryRef
from sensai.memory.fts import query_words
from sensai.memory.store import LABEL_MAX_CHARS, MemoryStore

logger = logging.getLogger(__name__)

MAX_PER_KIND = 5
TOKEN_BUDGET = 500
CHARS_PER_TOKEN = 4
SEARCH_LIMIT = 20  # FTS5 hits read before filtering by kind and budget

INSTRUCTION = (
    "Ces éléments ont été retenus lors de conversations précédentes. Applique-les quand "
    "la question les concerne et, si l'un d'eux change ta requête SQL, dis-le dans ta "
    "réponse."
)
_CATEGORIES = {"preference": "préférence", "business_rule": "règle métier", "note": "note"}


@dataclass(frozen=True)
class InjectedContext:
    """The block for the system prompt ('' when nothing) and the refs it contains."""

    text: str
    refs: tuple[MemoryRef, ...]


EMPTY = InjectedContext("", ())


def _plain(text: str) -> str:
    """Casefolded, without accents: 'Délai' and 'delai' compare equal."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _words(text: str) -> set[str]:
    return set(query_words(_plain(text)))


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)  # ceiling division


class MemoryContext:
    """Selects the memory entries of a question. Reads only: never writes the store."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        max_per_kind: int = MAX_PER_KIND,
        token_budget: int = TOKEN_BUDGET,
    ) -> None:
        self.store = store
        self.max_per_kind = max_per_kind
        self.token_budget = token_budget

    def for_question(self, question: str) -> InjectedContext:
        """The context of this question, EMPTY if nothing applies or the store fails.

        A memory problem never stops a turn: the model just answers without it.
        """
        try:
            candidates = self._candidates(question)
        except Exception:
            logger.exception("memory context unavailable")
            return EMPTY
        lines: list[str] = []
        refs: list[MemoryRef] = []
        used = estimate_tokens(INSTRUCTION)
        for ref, line in candidates:
            cost = estimate_tokens(line) + 1
            if used + cost > self.token_budget:
                continue  # a shorter entry further down may still fit
            used += cost
            lines.append(line)
            refs.append(ref)
        if not refs:
            return EMPTY
        return InjectedContext("\n".join([INSTRUCTION, *lines]), tuple(refs))

    # --- selection ----------------------------------------------------------------------

    def _candidates(self, question: str) -> list[tuple[MemoryRef, str]]:
        """Entries in order of priority, at most max_per_kind per kind, no duplicates."""
        ordered: list[tuple[MemoryKind, str]] = [
            ("glossary", row["ref"]) for row in self._terms_in(question)
        ]
        ordered += [
            (hit["kind"], hit["ref"])
            for hit in self.store.search(question, None, SEARCH_LIMIT)
            if hit["kind"] != "glossary"
        ]
        seen: set[tuple[str, str]] = set()
        per_kind: dict[str, int] = {}
        selected: list[tuple[MemoryRef, str]] = []
        for kind, ref in ordered:
            if (kind, ref.casefold()) in seen or per_kind.get(kind, 0) >= self.max_per_kind:
                continue
            seen.add((kind, ref.casefold()))
            entry = self.store.get(kind, ref)
            per_kind[kind] = per_kind.get(kind, 0) + 1
            selected.append(self._describe(kind, entry))
        return selected

    def _terms_in(self, question: str) -> list[dict[str, Any]]:
        """Glossary terms all of whose words are in the question, longest terms first."""
        asked = _words(question)
        terms = [
            row
            for row in self.store.dump()["glossary"]
            if (words := _words(row["term"])) and words <= asked
        ]
        return sorted(terms, key=lambda row: -len(_words(row["term"])))

    def _describe(self, kind: MemoryKind, entry: dict[str, Any]) -> tuple[MemoryRef, str]:
        """The ref of context_injected (memory.md §4) and the line given to the model."""
        if kind == "glossary":
            line = f"- Terme « {entry['term']} » : {entry['definition']}"
            if entry["sql_hint"]:
                line += f" (en SQL : {entry['sql_hint']})"
            return MemoryRef(kind, entry["ref"], entry["term"]), line
        if kind == "entity":
            line = f"- {entry['type']} « {entry['name']} »"
            if entry["aliases"]:
                line += f", aussi appelé {', '.join(entry['aliases'])}"
            if entry["external_ref"]:
                line += f" ; dans la base : {entry['external_ref']}"
            if entry["attributes"]:
                pairs = ", ".join(f"{key} = {value}" for key, value in entry["attributes"].items())
                line += f" ; {pairs}"
            return MemoryRef(kind, entry["ref"], entry["name"]), line
        category = _CATEGORIES.get(entry["category"], entry["category"])
        line = f"- Fait ({category}) : {entry['content']}"
        content = entry["content"]
        label = content if len(content) <= LABEL_MAX_CHARS else content[: LABEL_MAX_CHARS - 1] + "…"
        return MemoryRef(kind, entry["ref"], label), line


__all__ = ["EMPTY", "InjectedContext", "MemoryContext", "estimate_tokens"]
