"""Entry point: python -m sensai {chat,web,eval,seed}."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING

from sensai import __version__

if TYPE_CHECKING:
    from sensai.core.config import Config

# Subcommand -> planning task that implements it
COMMANDS: dict[str, tuple[str, str]] = {
    "chat": ("Chatbot en ligne de commande", "CORE-04"),
    "web": ("Serveur web et interface", "WEB-04"),
    "eval": ("Harness d'évaluation hors ligne", "EVAL-06"),
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
    parser.add_argument(
        "--mock",
        action="store_true",
        help="rejoue les scénarios de tests/fixtures/events au lieu d'appeler le moteur",
    )


def _run_web(args: argparse.Namespace) -> int:
    # Imported here so that `sensai --version` and the other commands stay fast
    import uvicorn

    from sensai.core.config import ConfigError, load_config
    from sensai.core.demo import demo_notice
    from sensai.web.server import create_app

    try:
        config = load_config(args.config, overrides={"web.host": args.host, "web.port": args.port})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.mock:
        from sensai.web.engines.mock import MockBackend

        backend = MockBackend(config)
    else:
        from sensai.web.engines.real import RealBackend

        business_db = config.database.business_path
        if not business_db.is_file():
            print(
                f"Base de démonstration absente ({business_db}) : lancez `make seed`, "
                "ou `sensai web --mock` pour rejouer les scénarios sans base ni Ollama.",
                file=sys.stderr,
            )
            return 1
        backend = RealBackend(config)
        notice = demo_notice(config)  # the mock backend replays fixtures: never altered
        if notice is not None:
            print(notice, file=sys.stderr)
    # Single worker: web sessions live in memory
    app = create_app(config, backend)
    uvicorn.run(app, host=config.web.host, port=config.web.port)
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


def _add_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "compare",
        nargs="*",
        metavar="compare AVANT APRÈS",
        help="compare deux rapports (tags ou chemins .json) au lieu de lancer le jeu",
    )
    parser.add_argument("--suite", default="eval/golden.jsonl", help="jeu de questions")
    parser.add_argument("--tag", help="nom de la version mesurée, ex. v0.5")
    parser.add_argument(
        "--today", type=_iso_date, help="date de référence AAAA-MM-JJ (défaut : 2026-10-20)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="graine des données et d'Ollama (défaut : 42)"
    )
    parser.add_argument("--model", help="modèle Ollama mesuré (défaut : [ollama] model)")
    parser.add_argument("--host", help="URL du serveur Ollama (défaut : [ollama] host)")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")


def _run_eval(args: argparse.Namespace) -> int:
    from sensai.eval.report import ReportError, check_tag, compare, find_report, load_report

    if args.compare:
        if len(args.compare) != 3 or args.compare[0] != "compare":
            print("usage : sensai eval compare AVANT APRÈS", file=sys.stderr)
            return 2
        try:
            before, after = (load_report(find_report(arg)) for arg in args.compare[1:])
        except ReportError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(compare(before, after), end="")
        return 0
    if not args.tag:
        print("usage : sensai eval --tag TAG [--suite …] (ex. --tag v0.5)", file=sys.stderr)
        return 2
    try:
        check_tag(args.tag)
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return 2
    import asyncio

    from sensai.core.config import ConfigError, load_config

    try:
        config = load_config(
            args.config, overrides={"ollama.model": args.model, "ollama.host": args.host}
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return asyncio.run(_eval_run(args, config))


async def _eval_run(args: argparse.Namespace, config: Config) -> int:
    """`sensai eval --suite … --tag …`: the golden set on the real engine (EVAL-06)."""
    from sensai.eval.golden import REFERENCE_DATE
    from sensai.eval.harness import QuestionResult, run_and_report
    from sensai.eval.report import load_report
    from sensai.eval.runtime import EvalRuntime, check_models

    problem = await check_models(config)
    if problem is not None:
        print(f"sensai eval : {problem}", file=sys.stderr)
        return 1
    today = args.today or REFERENCE_DATE

    def progress(result: QuestionResult) -> None:
        ex = {None: "    ", True: "EX 1", False: "EX 0"}[result.ex]
        print(f"{result.id}  {ex}  {result.duration_ms / 1000:5.1f} s  {result.error or ''}")

    async with EvalRuntime(config, seed=args.seed) as runtime:
        path = await run_and_report(
            args.suite,
            runtime.factory,
            tag=args.tag,
            today=today,
            seed=args.seed,
            info=runtime.info(),
            on_result=progress,
            teardown=runtime.aclose,
        )
    ex = load_report(path)["metrics"]["ex"]
    value = "—" if ex["value"] is None else f"{ex['value']:.2f}"
    print(f"EX {value} sur {ex['n']} questions. Rapports : {path} et {path.with_suffix('.md')}")
    return 0


# Subcommands already implemented; the others print "pas encore implémenté"
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "chat": _run_chat,
    "web": _run_web,
    "eval": _run_eval,
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
        elif name == "eval":
            _add_eval_arguments(command)
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
