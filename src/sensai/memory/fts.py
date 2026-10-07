"""Turn free French text into a safe FTS5 query (docs/contracts/memory.md §3.3).

Raw user text must never reach MATCH: an apostrophe, a '?' or a '-' are FTS5 syntax.
And unicode61 does no stemming, so plurals are cut down to a prefix query.
"""

from __future__ import annotations

import re

# Letters and digits only: '_' is a word character for \w but not for us
_WORD = re.compile(r"[^\W_]+")

# Short French stop words that would match almost every entry
STOP_WORDS = frozenset(
    {
        "au", "aux", "avec", "ce", "ces", "cet", "cette", "dans", "de", "des", "du",
        "elle", "en", "est", "et", "il", "je", "la", "le", "les", "leur", "leurs",
        "ma", "mes", "mon", "ne", "nos", "notre", "nous", "on", "ou", "par", "pas",
        "pour", "qu", "que", "quel", "quelle", "quelles", "quels", "qui", "quoi", "sa",
        "ses", "son", "sont", "sur", "ta", "tes", "ton", "tu", "un", "une", "vos",
        "votre", "vous",
    }
)  # fmt: skip


def query_words(text: str) -> list[str]:
    """Normalized search words, in order of appearance, without duplicates."""
    words: list[str] = []
    for raw in _WORD.findall(text.casefold()):
        if len(raw) < 2 or raw in STOP_WORDS:
            continue
        word = raw[:-1] if len(raw) > 3 and raw[-1] in "sx" else raw
        if word not in words:
            words.append(word)
    return words


def build_fts_query(text: str) -> str | None:
    """'Quels sont nos clients rentables ?' -> '"client"* OR "rentable"*'.

    None when nothing is left to search for: the caller returns no result instead of
    running an empty MATCH, which is a syntax error.
    """
    words = query_words(text)
    if not words:
        return None
    return " OR ".join(f'"{word}"*' for word in words)
