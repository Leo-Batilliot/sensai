"""Conversation history sent to the model on every turn.

An exchange is recorded only once its answer is complete: an interrupted answer (Ctrl+C,
stream cut) never enters the history, so the model never sees a half-written reply
(PROJECT.md §6.1). Truncation to the context window comes with CORE-13.
"""

from __future__ import annotations

from sensai.llm.ollama_client import Message


class History:
    """Completed user/assistant exchanges of one session, oldest first."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def __len__(self) -> int:
        return len(self._messages)

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def messages_for(self, question: str) -> list[Message]:
        """Messages to send for a new question: the whole history, then the question."""
        return [*self._messages, {"role": "user", "content": question}]

    def commit(self, question: str, answer: str) -> None:
        """Record a completed exchange. Only the answer text is kept, never the thinking."""
        self._messages.append({"role": "user", "content": question})
        self._messages.append({"role": "assistant", "content": answer})

    def clear(self) -> None:
        self._messages.clear()
