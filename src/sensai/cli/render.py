"""Terminal output of the CLI chat.

Answers are printed as raw deltas while they stream: a live Markdown rendering
(rich.Live) flickers, it comes with CORE-16. Texts from the model or from errors are
printed with markup and emoji codes off, so "[bold]" or ":memo:" are shown as is.
"""

from __future__ import annotations

from rich.console import Console


class Renderer:
    """Prints streamed answers and status messages, keeping lines tidy between them."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console if console is not None else Console(highlight=False)
        self._line_open = False  # answer text printed without its final newline
        self._thinking_shown = False

    def thinking(self) -> None:
        """Say once per answer that the model is thinking; its text stays hidden."""
        if self._thinking_shown:
            return
        self._thinking_shown = True
        self._print("(réflexion…)", style="dim")

    def delta(self, text: str) -> None:
        # soft_wrap: rich would otherwise wrap each delta on its own and break lines
        self.console.print(text, end="", markup=False, emoji=False, soft_wrap=True)
        self._line_open = True

    def end_answer(self) -> None:
        self._close_line()
        self._thinking_shown = False

    def info(self, text: str) -> None:
        self._print(text)

    def hint(self, text: str) -> None:
        self._print(text, style="dim")

    def warning(self, text: str) -> None:
        self._print(text, style="yellow")

    def error(self, text: str) -> None:
        self._print(text, style="red")

    def _print(self, text: str, style: str | None = None) -> None:
        self._close_line()
        self.console.print(text, style=style, markup=False, emoji=False)

    def _close_line(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False
