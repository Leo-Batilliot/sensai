"""Entry point: python -m sensai {chat,web,eval,seed}."""

from __future__ import annotations

import argparse
import sys

from sensai import __version__

# Subcommand -> planning task that implements it
COMMANDS: dict[str, tuple[str, str]] = {
    "chat": ("Chatbot en ligne de commande", "CORE-04"),
    "web": ("Serveur web et interface", "WEB-04"),
    "eval": ("Harness d'évaluation hors ligne", "EVAL-06a"),
    "seed": ("Génère la base de démonstration", "DATA-02"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sensai", description="Copilote BI local (Ollama).")
    parser.add_argument("--version", action="version", version=f"sensai {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for name, (help_text, _task) in COMMANDS.items():
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    _help, task = COMMANDS[args.command]
    print(f"sensai {args.command} : pas encore implémenté ({task}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
