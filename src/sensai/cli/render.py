"""Terminal output of the CLI chat.

Answers are printed as raw deltas while they stream: a live Markdown rendering
(rich.Live) flickers, it comes with CORE-16. Texts from the model or from errors are
printed with markup and emoji codes off, so "[bold]" or ":memo:" are shown as is.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


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

    def confirmation(
        self, tool: str, sql: str | None, impact: dict[str, Any], expires_in_s: float
    ) -> None:
        """The action waiting for a decision: what runs, and what it changes."""
        lines = [f"Outil : {tool}"]
        if sql:
            lines.append(f"SQL : {' '.join(sql.split())}")
        per_table = impact.get("per_table") or {}
        sample = impact.get("sample") or []
        blocked = impact.get("blocked") or []
        if per_table or sample or blocked:
            lines.append(
                f"Opération : {impact.get('operation')} · lignes visées : {impact.get('rows_affected')}"
            )
            lines += [f"  {table} : {delta:+d}" for table, delta in per_table.items()]
            if sample:
                lines.append("Exemples :")
                lines += [f"  {_sample_line(row)}" for row in sample]
            for block in blocked:
                count = block.get("count")
                lines.append(f"Bloqué : {block.get('reason')}" + (f" ({count})" if count else ""))
        else:
            lines.append("Aperçu de l'impact indisponible pour cet outil.")
        lines.append(f"Sans réponse dans {expires_in_s:g} s, l'action est refusée.")
        self._close_line()
        self.console.print(
            Panel(Text("\n".join(lines)), title="Confirmation requise", border_style="yellow")
        )

    def _print(self, text: str, style: str | None = None) -> None:
        self._close_line()
        self.console.print(text, style=style, markup=False, emoji=False)

    def _close_line(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False


def _sample_line(row: Any) -> str:
    if isinstance(row, dict):
        return ", ".join(f"{key}={value}" for key, value in row.items())
    return str(row)
