"""CLI side of the human-in-the-loop pause: ask "Confirmer ? [o/N]" on the keyboard.

The panel describing the action is printed from the confirmation_required event (see
Renderer.confirmation); the broker only reads the answer. The keyboard is read in a
thread, so the event loop keeps running: the Engine bounds the wait with its delay, and
Ctrl+C refuses the action. A thread blocked on input() cannot be stopped: after a
timeout or Ctrl+C, the next Enter only ends that read and its answer is ignored.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sensai.cli.render import Renderer
from sensai.core.confirm import Decision, PendingAction

PROMPT = "Confirmer ? [o/N] "
# Anything else, an empty line included, is a refusal
APPROVALS = frozenset({"o", "oui", "y", "yes"})


class CliConfirmationBroker:
    """ConfirmationBroker of the CLI: default answer no, only an explicit yes approves."""

    def __init__(self, renderer: Renderer, read_line: Callable[[str], str] = input) -> None:
        self.render = renderer
        self.read_line = read_line
        self._interrupt: asyncio.Future[None] | None = None

    async def request(self, action: PendingAction) -> Decision:
        self._interrupt = asyncio.get_running_loop().create_future()
        reading = asyncio.ensure_future(asyncio.to_thread(self.read_line, PROMPT))
        try:
            await asyncio.wait({reading, self._interrupt}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # The Engine's delay ran out (or the turn is torn down)
            self.render.warning("Délai dépassé, action refusée. Appuyez sur Entrée pour continuer.")
            raise
        finally:
            self._interrupt = None
        if not reading.done():
            self.render.warning("Action refusée (interruption). Appuyez sur Entrée pour continuer.")
            return Decision("reject", "cancelled")
        try:
            answer = reading.result()
        except (EOFError, KeyboardInterrupt):
            return Decision("reject", "user")
        if answer.strip().lower() in APPROVALS:
            return Decision("approve", "user")
        return Decision("reject", "user")

    def interrupt(self) -> bool:
        """Ctrl+C: refuse the pending action. False when nothing is waiting."""
        if self._interrupt is None or self._interrupt.done():
            return False
        self._interrupt.set_result(None)
        return True
