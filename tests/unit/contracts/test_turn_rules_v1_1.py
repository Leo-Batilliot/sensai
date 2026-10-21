"""Turn rules added by the v1.1 contracts (events.md §4.1 and §4.3), on a hand-written turn.

The fixtures only cover v1 events until P4 adds question.jsonl, document.jsonl... (§4.2 of
PLANNING.md): this turn shows every v1.1 event once, and each case breaks one rule.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from contract_rules import check_event, check_turns

Turn = list[dict[str, Any]]


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "session_id": "s-1", "turn_id": "t-1", "ts": 0.0, **fields}


def result(call_id: str, ok: bool = True) -> dict[str, Any]:
    error = None if ok else {"code": "TOOL_FAILED", "message": "Échec."}
    return event(
        "tool_result", call_id=call_id, ok=ok, summary="Fait", data=None, error=error, duration_ms=5
    )


ANSWER = "Sur ce trimestre, le délai moyen est de 47,3 jours."
TURN: Turn = [
    event("turn_started", origin="user", user_message="Quel est le délai moyen de paiement ?"),
    event("context_injected", refs=[{"kind": "glossary", "ref": "délai", "label": "délai"}]),
    event("context_compacted", turns=6, tokens_before=6120, tokens_after=2480, summary="Résumé…"),
    event("examples_injected", examples=[{"id": "ex-07", "question": "CA HT ?", "score": 7.4}]),
    event("tool_call", call_id="k-1", server=None, tool="ask_user", arguments={}),
    event(
        "question_asked",
        question_id="q-1",
        call_id="k-1",
        question="Sur ce trimestre ou sur tout l'historique ?",
        reason="Les deux périodes donnent des délais très différents.",
        options=[{"id": "trimestre", "label": "Ce trimestre"}],
        allow_free_text=True,
        expires_in_s=120,
    ),
    event(
        "question_answered", question_id="q-1", status="answered", option_id="trimestre", text=None
    ),
    result("k-1"),
    event("tool_call", call_id="k-2", server=None, tool="document_patch", arguments={}),
    result("k-2"),
    event(
        "document_updated",
        doc_id="note-comite",
        revision=4,
        section_id="rentabilite",
        op="replace",
        summary="Section « Rentabilité » mise à jour (révision 4)",
    ),
    event("token", delta=ANSWER),
    event(
        "message_done",
        content=ANSWER,
        interrupted=False,
        usage={"prompt_tokens": 2900, "completion_tokens": 40, "ctx_used": 2940, "ctx_max": 8192},
        duration_ms=9000,
    ),
]


def check(turn: Turn, max_questions: int = 1) -> None:
    for item in turn:
        check_event(item)
    check_turns(turn, max_questions=max_questions)


def second_question(turn: Turn) -> None:
    extra = copy.deepcopy(turn[4:8])  # tool_call, question_asked, question_answered, tool_result
    for item in extra:
        item.update({k: item[k] + "b" for k in ("call_id", "question_id") if k in item})
    turn[8:8] = extra


def swap(i: int, j: int) -> Callable[[Turn], None]:
    def apply(turn: Turn) -> None:
        turn[i], turn[j] = turn[j], turn[i]

    return apply


def test_v1_1_turn_follows_the_contract() -> None:
    check(TURN)


def test_max_questions_comes_from_the_config() -> None:
    turn = copy.deepcopy(TURN)
    second_question(turn)
    check(turn, max_questions=2)


BREAKS: dict[str, Callable[[Turn], None]] = {
    "examples-before-compaction": swap(2, 3),
    "examples-after-a-tool-call": swap(3, 4),
    "two-compactions": lambda turn: turn.insert(3, copy.deepcopy(turn[2])),
    "compaction-without-gain": lambda turn: turn[2].update(tokens_after=6120),
    "result-before-answer": swap(6, 7),
    "answer-without-question": lambda turn: turn.pop(5),
    "question-never-answered": lambda turn: turn.pop(6),
    "question-from-another-tool": lambda turn: turn[4].update(tool="memory_delete"),
    "sql-for-ask_user": lambda turn: turn.insert(
        5, event("sql_generated", call_id="k-1", sql="SELECT 1", operation="SELECT")
    ),
    "two-questions": second_question,
    "document-update-after-a-failure": lambda turn: turn.__setitem__(9, result("k-2", ok=False)),
    "document-update-after-another-tool": lambda turn: turn[8].update(tool="memory_update"),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_broken_turn_is_rejected(name: str) -> None:
    turn = copy.deepcopy(TURN)
    BREAKS[name](turn)
    with pytest.raises(AssertionError):
        check(turn)
