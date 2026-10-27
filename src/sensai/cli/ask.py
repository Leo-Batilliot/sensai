"""CLI side of the clarifying questions: a numbered choice, or a free answer.

The panel with the question is printed from the question_asked event (see
Renderer.question); the broker only reads the answer. As for confirmations
(cli/confirm.py), the keyboard is read in a thread, so the event loop keeps running: the
Engine bounds the wait with its delay, and Ctrl+C cancels the question. A thread blocked
on input() cannot be stopped: after a timeout or Ctrl+C, the next Enter only ends that
read and its answer is ignored.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sensai.cli.render import Renderer
from sensai.core.ask import Answer, PendingQuestion

CHOICE_PROMPT = "Votre choix : "
RETRY_PROMPT = "Numéro inconnu. Votre choix : "
TEXT_PROMPT = "Votre réponse : "


class CliQuestionBroker:
    """QuestionBroker of the CLI: an empty line leaves the choice to the agent."""

    def __init__(self, renderer: Renderer, read_line: Callable[[str], str] = input) -> None:
        self.render = renderer
        self.read_line = read_line
        self._interrupt: asyncio.Future[None] | None = None

    async def ask(self, question: PendingQuestion) -> Answer:
        self._interrupt = asyncio.get_running_loop().create_future()
        reading = asyncio.ensure_future(asyncio.to_thread(self._read, question))
        try:
            await asyncio.wait({reading, self._interrupt}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # The Engine's delay ran out (or the turn is torn down)
            self.render.warning(
                "Délai dépassé : l'agent fait une hypothèse. Appuyez sur Entrée pour continuer."
            )
            raise
        finally:
            self._interrupt = None
        if not reading.done():
            self.render.warning(
                "Question annulée (interruption). Appuyez sur Entrée pour continuer."
            )
            return Answer("cancelled")
        try:
            return reading.result()
        except (EOFError, KeyboardInterrupt):
            return Answer("cancelled")

    def interrupt(self) -> bool:
        """Ctrl+C: cancel the pending question. False when nothing is waiting."""
        if self._interrupt is None or self._interrupt.done():
            return False
        self._interrupt.set_result(None)
        return True

    def _read(self, question: PendingQuestion) -> Answer:
        """Runs in a thread: only reads, never prints (the prompt says what went wrong)."""
        if not question.options:
            return _free(self.read_line(TEXT_PROMPT))
        prompt = CHOICE_PROMPT
        while True:
            line = self.read_line(prompt).strip()
            if not line:
                return Answer("cancelled")
            if line.isdigit():
                number = int(line)
                if 1 <= number <= len(question.options):
                    return Answer("answered", option_id=question.options[number - 1]["id"])
                if number == 0 and question.allow_free_text:
                    return _free(self.read_line(TEXT_PROMPT))
            elif question.allow_free_text:
                return _free(line)  # typed straight away, without choosing 0 first
            prompt = RETRY_PROMPT


def _free(line: str) -> Answer:
    text = line.strip()
    return Answer("answered", text=text) if text else Answer("cancelled")
