"""Entry point: python -m sensai {chat,web,eval,seed,prompts,schedule}."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from sensai import __version__

if TYPE_CHECKING:
    from sensai.core.config import Config
    from sensai.web.engines.backend import WebBackend

# Subcommand -> planning task that implements it
COMMANDS: dict[str, tuple[str, str]] = {
    "chat": ("Chatbot en ligne de commande", "CORE-04"),
    "web": ("Serveur web et interface", "WEB-04"),
    "eval": ("Harness d'évaluation hors ligne", "EVAL-06"),
    "seed": ("Génère la base de démonstration", "DATA-02"),
    "prompts": ("Versions publiées des prompts", "PROMPT-01"),
    "schedule": ("Tâches planifiées : liste, ou exécution immédiate", "SCHED-01"),
}


def _add_chat_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help="modèle Ollama (défaut : [ollama] model)")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")
    parser.add_argument("--host", help="URL du serveur Ollama (défaut : [ollama] host)")
    _add_prompt_version_argument(parser)


def _add_prompt_version_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prompt-version", help="version du prompt système (défaut : [agent] prompt_version)"
    )


def _run_chat(args: argparse.Namespace) -> int:
    # Imported here so that `sensai --version` and the other commands stay fast
    from sensai.cli.app import run_chat

    return run_chat(args.config, args.model, args.host, prompt_version=args.prompt_version)


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
    from sensai.web.frontend import stale
    from sensai.web.paths import STATIC_DIR, WEB_DIR
    from sensai.web.server import create_app

    try:
        config = load_config(args.config, overrides={"web.host": args.host, "web.port": args.port})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    problem = prompt_problem(config)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    backend = _web_backend(config, args.mock, "web")
    if backend is None:
        return 1
    if not args.mock:
        notice = demo_notice(config)  # the mock backend replays fixtures: never altered
        if notice is not None:
            print(notice, file=sys.stderr)
    if stale(STATIC_DIR, WEB_DIR):  # web/dist is committed: a front change not rebuilt
        print(
            "Interface web plus ancienne que web/src : lancez `make web-build` (Node 22), "
            "puis committez web/dist.",
            file=sys.stderr,
        )
    # Single worker: web sessions live in memory
    app = create_app(config, backend)
    uvicorn.run(app, host=config.web.host, port=config.web.port)
    return 0


def _web_backend(config: Config, mock: bool, command: str) -> WebBackend | None:
    """The engine side of `sensai web` and `sensai schedule`: None if it cannot start."""
    if mock:
        from sensai.web.engines.mock import MockBackend

        return MockBackend(config)
    from sensai.web.engines.real import RealBackend

    business_db = config.database.business_path
    if not business_db.is_file():
        print(
            f"Base de démonstration absente ({business_db}) : lancez `make seed`, "
            f"ou `sensai {command} --mock` pour rejouer les scénarios sans base ni Ollama.",
            file=sys.stderr,
        )
        return None
    return RealBackend(config)


def _add_schedule_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "action", nargs="?", choices=("list", "run"), default="list", help="défaut : list"
    )
    parser.add_argument("schedule_id", nargs="?", help="la tâche à lancer (run)")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")
    parser.add_argument(
        "--mock", action="store_true", help="rejoue les scénarios de démonstration (sans Ollama)"
    )


def _run_schedule(args: argparse.Namespace) -> int:
    import asyncio

    from sensai.core.config import ConfigError, load_config

    if args.action == "run" and not args.schedule_id:
        print("usage : sensai schedule run TÂCHE", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not config.schedules:
        print("Aucune tâche planifiée : ajoutez une section [schedules.<id>] à config.toml.")
        return 0 if args.action == "list" else 1
    problem = prompt_problem(config)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 1
    backend = _web_backend(config, args.mock, "schedule")
    if backend is None:
        return 1
    return asyncio.run(
        _schedule(config, backend, args.schedule_id if args.action == "run" else None)
    )


async def _schedule(config: Config, backend: WebBackend, schedule_id: str | None) -> int:
    """`sensai schedule`: the tasks and their last run; `sensai schedule run ID`: one run
    now, exactly as "Exécuter maintenant" in the web (X5)."""
    from datetime import datetime

    from sensai.web.scheduler import Scheduler, ScheduleRefused, timetable

    async def broadcast(message: dict[str, object], requester: object, reply_to: object) -> None:
        return None  # no session to tell: the result is printed below

    def when(epoch: float | None) -> str:
        return "—" if epoch is None else datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M")

    await backend.start()
    scheduler = Scheduler(config, backend, broadcast)
    await scheduler.start(loop=False)
    try:
        if schedule_id is None:
            for task in scheduler.describe():
                schedule = config.schedules[task["id"]]
                state = f"prochaine : {when(task['next_run'])}" if task["enabled"] else "désactivée"
                last = task["last_runs"][0] if task["last_runs"] else None
                done = (
                    "jamais lancée"
                    if last is None
                    else f"dernière : {when(last['started_at'])} {last['status'] or 'en cours'}"
                )
                print(f"{task['id']}  {timetable(schedule)}  {state}  {done}")
            return 0
        try:
            run_id = await scheduler.run(schedule_id, "manual")
        except ScheduleRefused as refused:
            print(refused.message, file=sys.stderr)
            return 1
        print(f"{schedule_id} : exécution sans humain, en lecture seule…")
        await scheduler.wait()
        run = scheduler.store.get(run_id)
        assert run is not None
        if run["status"] != "ok":
            print(f"Échec : {run['error']['message']} ({run['error']['code']})", file=sys.stderr)
            return 1
        print(f"Rapport : {run['report_path']} · vérification : {run['badge'] or 'aucune'}")
        return 0
    finally:
        await scheduler.stop()
        await backend.stop()


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
        metavar="compare AVANT APRÈS | calibrate",
        help="compare deux rapports (tags ou chemins .json), ou calibre le juge sur "
        "eval/judge_calibration.jsonl, au lieu de lancer le jeu",
    )
    parser.add_argument(
        "--suite",
        default="eval/golden.jsonl",
        help="jeu de questions, ou jeu d'attaques (eval/adversarial.jsonl)",
    )
    parser.add_argument(
        "--only", action="append", help="jeu d'attaques : ne rejouer que ce cas (répétable)"
    )
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
    _add_prompt_version_argument(parser)
    parser.add_argument(
        "--no-fewshot",
        action="store_true",
        help="sans exemples dynamiques ([fewshot] enabled = false), témoin d'A7",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="mémoire vide pour chaque question (sans eval/memory_rules.json), témoin de M3",
    )
    parser.add_argument(
        "--numeric-only",
        action="store_true",
        help="calibrate : contrôle numérique seul, sans appeler le modèle du juge",
    )


def _run_eval(args: argparse.Namespace) -> int:
    from sensai.eval.report import ReportError, check_tag, compare, find_report, load_report

    if args.compare == ["calibrate"]:
        return _run_calibration(args)
    from sensai.eval.adversarial import is_adversarial_suite

    if not args.compare and is_adversarial_suite(args.suite):
        return _run_adversarial(args)
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
            args.config,
            overrides={
                "ollama.model": args.model,
                "ollama.host": args.host,
                "agent.prompt_version": args.prompt_version,
                "fewshot.enabled": False if args.no_fewshot else None,
            },
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    problem = prompt_problem(config)
    if problem is not None:
        print(f"sensai eval : {problem}", file=sys.stderr)
        return 1
    return asyncio.run(_eval_run(args, config))


def _run_adversarial(args: argparse.Namespace) -> int:
    """`sensai eval --suite eval/adversarial.jsonl`: the attacks on the real model."""
    import asyncio

    from sensai.core.config import ConfigError, load_config
    from sensai.eval.adversarial import load_cases

    try:
        config = load_config(
            args.config,
            overrides={
                "ollama.model": args.model,
                "ollama.host": args.host,
                "agent.prompt_version": args.prompt_version,
            },
        )
        cases = load_cases(args.suite)
    except (ConfigError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.only:
        cases = [case for case in cases if case.id in args.only]
    problem = prompt_problem(config)
    if problem is not None:
        print(f"sensai eval : {problem}", file=sys.stderr)
        return 1
    return asyncio.run(_adversarial_run(args, config, cases))


async def _adversarial_run(args: argparse.Namespace, config: Config, cases: list[Any]) -> int:
    from sensai.eval.adversarial import CaseResult, build_report, run_suite, write_report
    from sensai.eval.runtime import check_models

    problem = await check_models(config)
    if problem is not None:
        print(f"sensai eval : {problem}", file=sys.stderr)
        return 1

    def progress(result: CaseResult) -> None:
        detail = " ; ".join(result.reasons) or (result.error or "")
        print(f"{result.id:<10} {result.verdict:<10} {result.duration_ms / 1000:5.1f} s  {detail}")

    results = await run_suite(cases, config, seed=args.seed, on_result=progress)
    info = {
        "run_day": date.today().isoformat(),
        "model": config.ollama.model,
        "seed": args.seed,
        "suite": args.suite,
        "temperature": 0.0,
        "prompt_version": config.agent.prompt_version,
    }
    report = build_report(results, info)
    path = write_report(report)
    total = report["rates"]["total"]
    print(
        f"held {total['held']}, soft_break {total['soft_break']}, hard_break "
        f"{total['hard_break']} sur {total['n']} cas. Rapports : {path} et "
        f"{path.with_suffix('.md')}"
    )
    return 1 if total["hard_break"] else 0


async def _eval_run(args: argparse.Namespace, config: Config) -> int:
    """`sensai eval --suite … --tag …`: the golden set on the real engine (EVAL-06)."""
    from sensai.eval.golden import REFERENCE_DATE
    from sensai.eval.harness import MEMORY_RULES_PATH, QuestionResult, run_and_report
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
            memory_rules_path=None if args.no_memory else MEMORY_RULES_PATH,
            on_result=progress,
            teardown=runtime.aclose,
        )
    ex = load_report(path)["metrics"]["ex"]
    value = "—" if ex["value"] is None else f"{ex['value']:.2f}"
    print(f"EX {value} sur {ex['n']} questions. Rapports : {path} et {path.with_suffix('.md')}")
    return 0


def prompt_problem(config: Config) -> str | None:
    """Why the configured prompt cannot be used (unknown version, published file changed),
    in French; None if it can. Checked before starting, like the model."""
    from sensai.core.errors import PromptVersionNotFound
    from sensai.core.prompts import RegistryError, check_version

    try:
        check_version(config.agent.prompt_version)
    except PromptVersionNotFound as exc:
        return exc.message
    except RegistryError as exc:
        return str(exc)
    return None


def _add_prompts_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "action",
        nargs="?",
        default="list",
        choices=("list", "show", "diff"),
        help="list (défaut), show VERSION ou diff AVANT APRÈS",
    )
    parser.add_argument("versions", nargs="*", metavar="VERSION")
    parser.add_argument("--config", help="fichier de configuration (défaut : ./config.toml)")


def _run_prompts(args: argparse.Namespace) -> int:
    """`sensai prompts list | show VERSION | diff AVANT APRÈS` (A4)."""
    import difflib

    from sensai.core.config import ConfigError, load_config
    from sensai.core.errors import PromptVersionNotFound
    from sensai.core.prompts import PromptRegistry, RegistryError, load

    expected = {"list": 0, "show": 1, "diff": 2}[args.action]
    if len(args.versions) != expected:
        print("usage : sensai prompts [list | show VERSION | diff AVANT APRÈS]", file=sys.stderr)
        return 2
    try:
        current = load_config(args.config).agent.prompt_version
        registry = PromptRegistry.load()
        entries = [registry.get(version) for version in args.versions]
    except (ConfigError, RegistryError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except PromptVersionNotFound as exc:
        print(exc.message, file=sys.stderr)
        return 1
    if args.action == "list":
        for entry in registry.entries:
            mark = "*" if entry.version == current else " "
            parent = f", parent {entry.parent}" if entry.parent else ""
            print(
                f"{mark} {entry.name} {entry.version}  {entry.created}  {entry.sha256[:12]}"
                f"{parent}\n    {entry.notes}"
            )
    elif args.action == "show":
        print(load(entries[0].file), end="")
    else:
        before, after = entries
        diff = difflib.unified_diff(
            load(before.file).splitlines(keepends=True),
            load(after.file).splitlines(keepends=True),
            fromfile=before.file,
            tofile=after.file,
        )
        print("".join(diff), end="")
    return 0


def _run_calibration(args: argparse.Namespace) -> int:
    """`sensai eval calibrate`: precision and recall of the judge (EVAL-07)."""
    import asyncio

    from sensai.core.config import ConfigError, load_config
    from sensai.eval.report import ReportError, check_tag

    tag = args.tag or "calibration"
    try:
        check_tag(tag)
        config = load_config(
            args.config, overrides={"ollama.model": args.model, "ollama.host": args.host}
        )
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    return asyncio.run(_calibrate(args, config, tag))


async def _calibrate(args: argparse.Namespace, config: Config, tag: str) -> int:
    from sensai.eval.calibration import (
        CALIBRATION_PATH,
        build_calibration_report,
        load_cases,
        run_calibration,
        write_calibration_report,
    )
    from sensai.eval.judge import Judge
    from sensai.eval.report import report_paths
    from sensai.eval.runtime import SeededOllamaClient, check_models

    model = None if args.numeric_only else config.ollama.effective_judge_model
    tolerance = config.eval.numeric_tolerance
    if model is None:
        outcomes = await run_calibration(load_cases(CALIBRATION_PATH), None, tolerance)
    else:
        problem = await check_models(config)
        if problem is not None:
            print(f"sensai eval calibrate : {problem}", file=sys.stderr)
            return 1
        async with SeededOllamaClient(
            config.ollama.host, seed=args.seed, timeout_s=config.ollama.timeout_s
        ) as client:
            judge = Judge(client, model, tolerance=tolerance, num_ctx=config.ollama.num_ctx)
            outcomes = await run_calibration(load_cases(CALIBRATION_PATH), judge, tolerance)
    info = {
        "suite": CALIBRATION_PATH.as_posix(),
        "judge_model": model,
        "ollama_seed": None if model is None else args.seed,
        "numeric_tolerance": tolerance,
    }
    report = build_calibration_report(outcomes, tag=tag, info=info)
    json_path, md_path = report_paths(tag, date.today())
    write_calibration_report(report, json_path, md_path)
    for name, matrix in report["detectors"].items():
        print(f"{name} : précision {matrix['precision']}, rappel {matrix['recall']}")
    print(f"Rapports : {json_path} et {md_path}")
    return 0


# Subcommands already implemented; the others print "pas encore implémenté"
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "chat": _run_chat,
    "web": _run_web,
    "eval": _run_eval,
    "seed": _run_seed,
    "prompts": _run_prompts,
    "schedule": _run_schedule,
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
        elif name == "prompts":
            _add_prompts_arguments(command)
        elif name == "schedule":
            _add_schedule_arguments(command)
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
