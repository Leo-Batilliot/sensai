"""Scheduled tasks (X5, SCHED-01, websocket.md §4.11, §5.2 and §9).

A task of [schedules.<id>] asks its question at its time ("MON 07:00", "DAILY 07:00"),
every N minutes, or at once with `run_schedule` ("Exécuter maintenant"). It runs without
a human (V-4): its own engine, which has no tool that writes, refuses every confirmation
and gets no answer to its questions (the model then makes an assumption). The scheduler,
not the agent, writes the report under reports/planifie/, after the permission check of
the file tools (T4).

Every run is kept in data/scheduler.db and announced to every open session
(schedule_run_started, schedule_run_finished). A session opened later reads
GET /api/schedules. The database is created by the first run only: a server without
tasks never writes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sensai.core.config import SCHEDULE_AT, Config, ScheduleConfig
from sensai.tools.file_permissions import FileAccessDenied, FilePermissions
from sensai.web.engines.backend import Persona, WebBackend
from sensai.web.shares import turn_markdown
from sensai.web.websocket.session import NOT_IN_HISTORY, to_payload

logger = logging.getLogger(__name__)

Trigger = Literal["schedule", "manual"]
Event = dict[str, Any]
# (message, session that asked for it, its message id): see SessionManager.broadcast
Broadcast = Callable[[dict[str, Any], str | None, str | None], Awaitable[None]]

DEFAULT_DB = Path("data/scheduler.db")
REPORTS_DIR = Path("reports/planifie")  # the only place a run writes to (US-X5-02)
LAST_RUNS = 5
TICK_S = 30.0  # the loop wakes at least this often: a changed clock is caught up quickly
WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_NAMES = {
    "MON": "lundi",
    "TUE": "mardi",
    "WED": "mercredi",
    "THU": "jeudi",
    "FRI": "vendredi",
    "SAT": "samedi",
    "SUN": "dimanche",
}
NO_HUMAN = (
    "Exécution sans humain : aucun outil d'écriture, toute confirmation est refusée et "
    "toute question est remplacée par une hypothèse."
)
STOPPED_MESSAGE = "Serveur arrêté pendant l'exécution."


class ScheduleRefused(Exception):
    """run_schedule refused: SCHEDULE_NOT_FOUND or BUSY (websocket.md §4.11)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunFailed(Exception):
    """A run that ends with status "error": {code, message} of schedule_run_finished."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- timetable ---------------------------------------------------------------------------


def next_at(at: str, now: float) -> float:
    """The first time strictly after `now` that matches "MON 07:00" or "DAILY 07:00"."""
    match = SCHEDULE_AT.match(at)
    if match is None:  # the configuration validated it
        raise ValueError(f"heure de tâche invalide : {at!r}")
    day, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
    current = datetime.fromtimestamp(now)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        # Naive local times: 07:00 stays 07:00 across a change of summer time
        if candidate.timestamp() > now and (day == "DAILY" or WEEKDAYS[candidate.weekday()] == day):
            return candidate.timestamp()
        candidate += timedelta(days=1)
    raise AssertionError("a week always has the day")


def timetable(schedule: ScheduleConfig) -> str:
    """ "chaque lundi à 07:00", "chaque jour à 07:00", "toutes les 60 minutes"."""
    if schedule.at is None:
        return f"toutes les {schedule.every_minutes} minutes"
    day, clock = schedule.at.split()
    return f"chaque jour à {clock}" if day == "DAILY" else f"chaque {DAY_NAMES[day]} à {clock}"


# --- runs --------------------------------------------------------------------------------


class RunStore:
    """data/scheduler.db: one row per run, from its start (status null) to its end."""

    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None

    def start(self, run_id: str, schedule_id: str, trigger: Trigger, started_at: float) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO schedule_runs (run_id, schedule_id, trigger, started_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, schedule_id, trigger, started_at),
            )

    def finish(
        self,
        run_id: str,
        finished_at: float,
        status: Literal["ok", "error"],
        badge: str | None,
        report_path: str | None,
        error: dict[str, str] | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE schedule_runs SET finished_at = ?, status = ?, badge = ?, "
                "report_path = ?, error = ? WHERE run_id = ?",
                (
                    finished_at,
                    status,
                    badge,
                    report_path,
                    None if error is None else json.dumps(error, ensure_ascii=False),
                    run_id,
                ),
            )

    def abandon_unfinished(self, now: float) -> None:
        """Runs cut by a stop of the server: they end in error, never "running" forever."""
        if not self.path.is_file():
            return
        error = json.dumps({"code": "INTERNAL", "message": STOPPED_MESSAGE}, ensure_ascii=False)
        with self._connect() as db:
            db.execute(
                "UPDATE schedule_runs SET finished_at = ?, status = 'error', error = ? "
                "WHERE status IS NULL",
                (now, error),
            )

    def last_runs(self, schedule_id: str, limit: int = LAST_RUNS) -> list[dict[str, Any]]:
        """Newest first; a running run has status, finished_at and badge null."""
        if not self.path.is_file():
            return []
        rows = self._connect().execute(
            "SELECT run_id, trigger, started_at, finished_at, status, badge, report_path, error "
            "FROM schedule_runs WHERE schedule_id = ? ORDER BY started_at DESC, rowid DESC "
            "LIMIT ?",
            (schedule_id, limit),
        )
        return [_run(row) for row in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        row = (
            self._connect()
            .execute(
                "SELECT run_id, trigger, started_at, finished_at, status, badge, report_path, "
                "error FROM schedule_runs WHERE run_id = ?",
                (run_id,),
            )
            .fetchone()
        )
        return None if row is None else _run(row)

    def last_started(self, schedule_id: str, trigger: Trigger) -> float | None:
        if not self.path.is_file():
            return None
        row = (
            self._connect()
            .execute(
                "SELECT MAX(started_at) FROM schedule_runs WHERE schedule_id = ? AND trigger = ?",
                (schedule_id, trigger),
            )
            .fetchone()
        )
        return None if row[0] is None else float(row[0])

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Used only from the server's event loop, which may not be the opening thread
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS schedule_runs ("
                "run_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, trigger TEXT NOT NULL, "
                "started_at REAL NOT NULL, finished_at REAL, status TEXT, badge TEXT, "
                "report_path TEXT, error TEXT)"
            )
        return self._db


def _run(row: tuple[Any, ...]) -> dict[str, Any]:
    run_id, trigger, started_at, finished_at, status, badge, report_path, error = row
    return {
        "run_id": run_id,
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "badge": badge,
        "report_path": report_path,
        "error": None if error is None else json.loads(error),
    }


# --- the scheduler -----------------------------------------------------------------------


class Scheduler:
    """Starts the tasks of [schedules] at their time and on demand, one run at a time each."""

    def __init__(
        self,
        config: Config,
        backend: WebBackend,
        broadcast: Broadcast,
        *,
        store: RunStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.schedules = config.schedules
        self.backend = backend
        self.store = store if store is not None else RunStore()
        # Same roots as the file tools, resolved against the launch directory
        self.files = FilePermissions.from_config(config.files)
        self._broadcast = broadcast
        self._clock = clock
        self._next: dict[str, float | None] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._loop: asyncio.Task[None] | None = None

    async def start(self, *, loop: bool = True) -> None:
        """loop=False: no run at the scheduled times, and the runs of a server that may be
        running are left alone (`sensai schedule`, which runs on demand)."""
        now = self._clock()
        if loop:
            self.store.abandon_unfinished(now)
        self._next = {schedule_id: self._first(schedule_id, now) for schedule_id in self.schedules}
        if loop and any(due is not None for due in self._next.values()):
            self._loop = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        tasks = [task for task in (self._loop, *self._running.values()) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop = None
        self.store.close()

    def describe(self) -> list[dict[str, Any]]:
        """The tasks as GET /api/schedules lists them (websocket.md §9)."""
        return [
            {
                "id": schedule_id,
                "at": schedule.at,
                "every_minutes": schedule.every_minutes,
                "question": schedule.question,
                "persona": schedule.persona,
                "output": schedule.output,
                "enabled": schedule.enabled,
                "next_run": self._next.get(schedule_id) if schedule.enabled else None,
                "last_runs": self.store.last_runs(schedule_id),
            }
            for schedule_id, schedule in self.schedules.items()
        ]

    def report(self, run_id: str) -> Path | None:
        """The report of a finished run, as written: never a path that comes from a request."""
        run = self.store.get(run_id)
        if run is None or run["report_path"] is None:
            return None
        path = self.files.base_dir / run["report_path"]
        return path if path.is_file() else None

    async def run(
        self,
        schedule_id: str,
        trigger: Trigger,
        requester: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        """Start a run now; returns its run_id once schedule_run_started is sent."""
        schedule = self.schedules.get(schedule_id)
        if schedule is None or not schedule.enabled:
            raise ScheduleRefused(
                "SCHEDULE_NOT_FOUND", f"La tâche « {schedule_id} » n'existe pas ou est désactivée."
            )
        if schedule_id in self._running:
            raise ScheduleRefused("BUSY", "Cette tâche tourne déjà.")
        run_id = str(uuid.uuid4())
        started_at = self._clock()
        self.store.start(run_id, schedule_id, trigger, started_at)
        task = asyncio.create_task(
            self._execute(schedule_id, schedule, run_id, trigger, started_at)
        )
        self._running[schedule_id] = task
        started = {"schedule_id": schedule_id, "run_id": run_id, "trigger": trigger}
        await self._broadcast({"type": "schedule_run_started", **started}, requester, reply_to)
        return run_id

    async def tick(self) -> None:
        """Start the tasks whose time has come (the loop calls it; tests too, with a clock)."""
        now = self._clock()
        for schedule_id, due in self._next.items():
            if due is None or due > now:
                continue
            self._next[schedule_id] = self._after(self.schedules[schedule_id], now)
            if schedule_id not in self._running:  # still running from last time: skipped
                await self.run(schedule_id, "schedule")

    async def wait(self) -> None:
        """Until every running task has finished (tests, the CLI)."""
        while self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    # --- internals ------------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a failed tick must not stop the next ones
                logger.exception("Planificateur : échec d'un passage")
            dues = [due for due in self._next.values() if due is not None]
            delay = min([TICK_S, *(due - self._clock() for due in dues)])
            await asyncio.sleep(max(delay, 0.5))

    def _first(self, schedule_id: str, now: float) -> float | None:
        """At startup: an interval counts from the last scheduled run, if there was one."""
        schedule = self.schedules[schedule_id]
        if not schedule.enabled:
            return None
        last = self.store.last_started(schedule_id, "schedule")
        if schedule.every_minutes is not None and last is not None:
            return max(last + schedule.every_minutes * 60, now)
        return self._after(schedule, now)

    @staticmethod
    def _after(schedule: ScheduleConfig, now: float) -> float:
        if schedule.at is not None:
            return next_at(schedule.at, now)
        assert schedule.every_minutes is not None  # one of the two (ScheduleConfig)
        return now + schedule.every_minutes * 60

    async def _execute(
        self,
        schedule_id: str,
        schedule: ScheduleConfig,
        run_id: str,
        trigger: Trigger,
        started_at: float,
    ) -> None:
        badge: str | None = None
        report_path: str | None = None
        error: dict[str, str] | None = None
        try:
            badge, report_path = await self._ask(schedule_id, schedule, run_id, trigger, started_at)
        except RunFailed as failed:
            error = {"code": failed.code, "message": failed.message}
        except asyncio.CancelledError:
            error = {"code": "INTERNAL", "message": STOPPED_MESSAGE}
            raise
        except Exception:
            logger.exception("Tâche planifiée %s : erreur interne", schedule_id)
            error = {
                "code": "INTERNAL",
                "message": "Erreur interne : voir les journaux du serveur.",
            }
        finally:
            self._running.pop(schedule_id, None)
            status: Literal["ok", "error"] = "ok" if error is None else "error"
            if error is not None:
                badge = report_path = None
            self.store.finish(run_id, self._clock(), status, badge, report_path, error)
            finished = {
                "type": "schedule_run_finished",
                "schedule_id": schedule_id,
                "run_id": run_id,
                "status": status,
                "badge": badge,
                "report_path": report_path,
                "error": error,
            }
            await self._broadcast(finished, None, None)

    async def _ask(
        self,
        schedule_id: str,
        schedule: ScheduleConfig,
        run_id: str,
        trigger: Trigger,
        started_at: float,
    ) -> tuple[str | None, str]:
        """One headless turn, judge included; returns the badge and the report path."""
        engine = self.backend.scheduled_engine(f"schedule-{run_id}")
        try:
            known = {persona["id"] for persona in self.backend.personas()[1]}
            # A server without profiles (before PERS-01) runs the task with its tools
            if schedule.persona is not None and known and not engine.set_persona(schedule.persona):
                raise RunFailed(
                    "PERSONA_NOT_FOUND", f"Le profil « {schedule.persona} » n'existe pas."
                )
            events: list[Event] = []
            async for event in engine.run_turn(schedule.question):
                payload = to_payload(event)
                if payload["type"] not in NOT_IN_HISTORY:
                    events.append(payload)
            _check_turn(events)
            markdown = report_markdown(
                schedule_id, schedule, events, engine.model, engine.persona, trigger, started_at
            )
            path = self._write_report(schedule_id, schedule, markdown, started_at)
        finally:
            await engine.aclose()
        badge = next((e["badge"] for e in reversed(events) if e["type"] == "judge_report"), None)
        return badge, path

    def _write_report(
        self, schedule_id: str, schedule: ScheduleConfig, markdown: str, started_at: float
    ) -> str:
        day = datetime.fromtimestamp(started_at).strftime("%Y-%m-%d")
        name = schedule.output.replace("{id}", schedule_id).replace("{date}", day)
        reports = (self.files.base_dir / REPORTS_DIR).resolve()
        target = (self.files.base_dir / name).resolve()
        if not target.is_relative_to(reports):
            raise RunFailed(
                "PATH_FORBIDDEN",
                f"Rapport refusé : {name} n'est pas sous {REPORTS_DIR.as_posix()}/.",
            )
        try:  # the roots, extensions and protected names of [files] (T4), audited
            resolved = self.files.check("write", name, tool="scheduler")
        except FileAccessDenied as denied:
            raise RunFailed(denied.code, f"Rapport refusé : {denied.reason}.") from denied
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(markdown, encoding="utf-8")
        return self.files.shown(resolved)


def _check_turn(events: list[Event]) -> None:
    """A turn that ended in error, or never ended, is a failed run (no report)."""
    error = next((e for e in events if e["type"] == "error"), None)
    if error is not None:
        raise RunFailed(error["code"], error["message"])
    done = next((e for e in events if e["type"] == "message_done"), None)
    if done is None or done["interrupted"]:
        raise RunFailed("INTERNAL", "Le moteur n'a pas terminé sa réponse.")


def report_markdown(
    schedule_id: str,
    schedule: ScheduleConfig,
    events: list[Event],
    model: str,
    persona: Persona | None,
    trigger: Trigger,
    started_at: float,
) -> str:
    """The report of one run: when and how, then the turn as in a conversation export."""
    when = datetime.fromtimestamp(started_at).strftime("%d/%m/%Y à %H:%M")
    how = "à l'heure prévue" if trigger == "schedule" else "à la demande"
    profile = f" · profil {persona['name']}" if persona else ""
    lines = [
        f"# Tâche planifiée « {schedule_id} »",
        "",
        f"Exécutée le {when}, {how} ({timetable(schedule)}) · modèle {model}{profile}",
        "",
        f"> {NO_HUMAN}",
        "",
        "---",
        "",
    ]
    started = next(e for e in events if e["type"] == "turn_started")
    lines += turn_markdown(1, started, events)
    return "\n".join(lines).rstrip() + "\n"
