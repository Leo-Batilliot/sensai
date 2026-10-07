"""Entry point: python -m sensai {chat,web,eval,seed}."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date

from sensai import __version__

# Subcommand -> planning task that implements it
COMMANDS: dict[str, tuple[str, str]] = {
    "chat": ("Chatbot en ligne de commande", "CORE-04"),
    "web": ("Serveur web et interface", "WEB-04"),
    "eval": ("Harness d'évaluation hors ligne", "EVAL-06a"),
    "seed": ("Génère la base de démonstration", "DATA-02"),
}


def _add_chat_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="modèle Ollama (défaut : [ollama] model)")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")
    parser.add_argument("--host", help="URL du serveur Ollama (défaut : [ollama] host)")


def _run_chat(args: argparse.Namespace) -> int:
    # Imported here so that `sensai --version` and the other commands stay fast
    from sensai.cli.app import run_chat

    return run_chat(args.config, args.model, args.host)


def _add_web_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")
    parser.add_argument("--host", help="adresse d'écoute (défaut : [web] host)")
    parser.add_argument("--port", type=int, help="port d'écoute (défaut : [web] port)")


def _run_web(args: argparse.Namespace) -> int:
    # Imported here so that `sensai --version` and the other commands stay fast
    import uvicorn

    from sensai.core.config import ConfigError, load_config
    from sensai.web.server import create_app

    try:
        config = load_config(args.config, overrides={"web.host": args.host, "web.port": args.port})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    # Default single worker: web sessions will live in memory (WEB-04)
    uvicorn.run(create_app(config), host=config.web.host, port=config.web.port)
    return 0


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"date invalide : {value!r} (format AAAA-MM-JJ)") from None


def _add_seed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seed", type=int, default=42, help="graine aléatoire (défaut : 42)")
    parser.add_argument(
        "--today", type=_iso_date, help="date de référence AAAA-MM-JJ (défaut : aujourd'hui)"
    )
    parser.add_argument("--out", help="fichier de la base (défaut : [database] business_path)")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")


def _run_seed(args: argparse.Namespace) -> int:
    from sensai.core.config import ConfigError, load_config
    from sensai.data.seed import MANIFEST_NAME, generate

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    out = args.out or config.database.business_path
    manifest = generate(out, seed=args.seed, today=args.today)
    volumes = manifest.volumes
    print(
        f"Base générée : {out} (graine {manifest.seed}, date de référence {manifest.today}) — "
        f"{volumes['clients']} clients, {volumes['orders']} commandes, "
        f"{volumes['invoices']} factures. Manifeste : {MANIFEST_NAME}"
    )
    return 0


# Subcommands already implemented; the others print "pas encore implémenté"
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "chat": _run_chat,
    "web": _run_web,
    "seed": _run_seed,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sensai", description="Copilote BI local (Ollama).")
    parser.add_argument("--version", action="version", version=f"sensai {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for name, (help_text, _task) in COMMANDS.items():
        command = sub.add_parser(name, help=help_text)
        if name == "chat":
            _add_chat_arguments(command)
        elif name == "web":
            _add_web_arguments(command)
        elif name == "seed":
            _add_seed_arguments(command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    handler = HANDLERS.get(args.command)
    if handler is not None:
        return handler(args)
    _help, task = COMMANDS[args.command]
    print(f"sensai {args.command} : pas encore implémenté ({task}).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
