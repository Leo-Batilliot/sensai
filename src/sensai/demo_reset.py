"""Reset of the demo (CORE-19): the machine back to the start of act 1, in one command.

`sensai demo-reset` (or `make demo-reset`) before every rehearsal and before the keynote
(docs/keynote/scenario.md). Everything the acts write goes back to its starting point:

- business.db generated again (seed 42, today's date), with its manifest and data/imports;
- memory.db removed then created again: an empty memory, since the rule of act 2 is learnt
  live, and a new committee note (M4) from its template;
- shared links (X3), scheduled runs (X5), the file audit (T4) and reports/ emptied.

Then it checks what would spoil the demo: the profile and prompt version of a new session
(the web keeps its sessions in memory, so a restart of `sensai web` starts them again from
these), the existing MCP server in the uv cache (nothing downloaded on stage), and it
preloads the model. A failed check is a warning: the data is reset anyway and the command
says what to fix. A file still open by `sensai web` or `sensai chat` stops it: Windows
refuses to delete an open database.

A Python command rather than a shell script: the demo machine runs Windows, without make.
Every path is relative to the launch directory, as everywhere in the configuration.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sensai.core.app_factory import ollama_client
from sensai.core.config import Config
from sensai.core.demo import demo_notice
from sensai.core.errors import SensaiError
from sensai.core.personas import PersonaError, load_personas
from sensai.data.seed import generate
from sensai.documents.store import DocumentStore
from sensai.llm.ollama_client import OllamaClient
from sensai.tools.file_permissions import DEFAULT_AUDIT_PATH
from sensai.web.scheduler import DEFAULT_DB as SCHEDULER_DB

DEMO_SEED = 42
# Read-write root of the file tools (T4) and folder of the scheduled reports (X5)
REPORTS_DIR = Path("reports")
SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")
CACHE_CHECK_TIMEOUT_S = 60


@dataclass(frozen=True)
class Step:
    """One line of the report: what was done or checked; ok=False is a warning."""

    text: str
    ok: bool = True


class ResetError(Exception):
    """A file the reset cannot replace or remove, in French: the application has it open."""


def reset_data(config: Config, today: date | None = None) -> list[Step]:
    """Put back every file the acts write. Raises ResetError on a file still in use."""
    business_db = config.database.business_path
    memory_db = config.database.memory_path
    try:
        manifest = generate(business_db, seed=DEMO_SEED, today=today)
        _remove_database(memory_db)
        # Created again now, so that the first act does not create it on stage
        with DocumentStore.open(memory_db, config.documents.template) as documents:
            title = documents.outline()["title"]
        shares = _empty_dir(config.web.share_dir)
        _remove_database(SCHEDULER_DB)
        DEFAULT_AUDIT_PATH.unlink(missing_ok=True)
        reports = _empty_dir(REPORTS_DIR)
    except OSError as exc:
        raise ResetError(
            f"Remise à zéro interrompue ({exc.filename or exc}) : fermez `sensai web` et "
            "`sensai chat`, puis relancez la commande."
        ) from exc
    return [
        Step(
            f"Base de démonstration régénérée : {business_db} (graine {manifest.seed}, "
            f"date de référence {manifest.today})"
        ),
        Step(f"Mémoire vide et note du comité neuve (« {title} ») : {memory_db}"),
        Step(f"Liens de partage supprimés : {shares} ({config.web.share_dir})"),
        Step(f"Exécutions planifiées effacées : {SCHEDULER_DB}"),
        Step(f"Journal des accès aux fichiers effacé : {DEFAULT_AUDIT_PATH}"),
        Step(f"Rapports supprimés : {reports} ({REPORTS_DIR})"),
    ]


def check_session(config: Config) -> list[Step]:
    """What a new session starts with: its profile (A5), its prompt (A4), the act 3 mode."""
    steps = []
    try:
        personas = load_personas(config.agent.personas_dir)
    except PersonaError as exc:
        steps.append(Step(str(exc), ok=False))
    else:
        persona = personas.get(config.agent.persona)
        if persona is None:
            steps.append(
                Step(f"Profil par défaut inconnu : {config.agent.persona} ([agent] persona)", False)
            )
        else:
            steps.append(Step(f"Profil des nouvelles sessions : {persona.name} ({persona.id})"))
    steps.append(Step(f"Version du prompt système : {config.agent.prompt_version}"))
    notice = demo_notice(config)
    if notice is not None:
        steps.append(Step(notice))
    return steps


def check_mcp_cache(
    config: Config,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> list[Step]:
    """Every server started by uvx is in the uv cache: nothing is downloaded on stage."""
    steps = []
    for name, server in config.mcp.servers.items():
        package = next((arg for arg in server.args if not arg.startswith("-")), None)
        if Path(server.command).stem != "uvx" or package is None:
            continue
        # The same lookup as the McpManager, which starts the server from the PATH
        uvx = which(server.command)
        if uvx is None:
            steps.append(
                Step(
                    f"Serveur {name} : `{server.command}` introuvable dans le PATH, il ne "
                    "démarrera pas. Activez le venv (.venv\\Scripts\\Activate.ps1 sous Windows, "
                    "source .venv/bin/activate ailleurs) ou installez uv.",
                    ok=False,
                )
            )
            continue
        try:
            result = run(
                [uvx, "--offline", package, "--help"],
                capture_output=True,
                timeout=CACHE_CHECK_TIMEOUT_S,
                check=False,
            )
            cached = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cached = False
        if cached:
            steps.append(Step(f"Serveur {name} : {package} en cache, sans téléchargement"))
        else:
            steps.append(
                Step(
                    f"Serveur {name} : {package} absent du cache uv. Avec Internet, lancez "
                    f"une fois `uvx {package} --help`.",
                    ok=False,
                )
            )
    return steps


async def preload_models(config: Config, client: OllamaClient | None = None) -> list[Step]:
    """Load the agent's model, and the judge's if another, with the num_ctx of the turns:
    with another num_ctx, Ollama would load it again at the first question."""
    models = dict.fromkeys((config.ollama.model, config.ollama.effective_judge_model))
    steps = []
    async with client or ollama_client(config) as ollama:
        for model in models:
            try:
                await ollama.load(model, num_ctx=config.ollama.num_ctx)
            except SensaiError as exc:
                steps.append(Step(f"Modèle {model} non préchargé : {exc.message}", ok=False))
            else:
                steps.append(
                    Step(
                        f"Modèle {model} préchargé (num_ctx {config.ollama.num_ctx}, "
                        f"gardé {config.ollama.keep_alive})"
                    )
                )
    return steps


def _remove_database(path: Path) -> None:
    for name in (path.name, *(path.name + suffix for suffix in SQLITE_SIDE_FILES)):
        path.with_name(name).unlink(missing_ok=True)


def _empty_dir(directory: Path) -> int:
    """Remove what the folder holds and keep the folder; the number of entries removed."""
    directory.mkdir(parents=True, exist_ok=True)
    entries = list(directory.iterdir())
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    return len(entries)
