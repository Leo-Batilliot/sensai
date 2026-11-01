"""Scheduled tasks (X5, SCHED-01, websocket.md §4.11, §5.2 and §9)."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.testclient import WebSocketTestSession

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10
    import tomli as tomllib

from contract_rules import SERVER, check_event
from sensai.core.config import Config, ScheduleConfig
from sensai.mcp.client import ToolAnnotations
from sensai.tools.base import ServerStatus, ToolInfo, ToolResult
from sensai.web.engines.mock import MockBackend, Pacing
from sensai.web.engines.real import ReadOnlyRegistry
from sensai.web.scheduler import NO_HUMAN, RunStore, Scheduler, next_at, timetable
from sensai.web.server import create_app

INSTANT = Pacing(token=0, tool=0, judge=0, other=0)
SLOW = Pacing(token=0.02, tool=0.01, judge=0.3, other=0.01)
SESSION = "3f2b9c1e-0000-4000-8000-0000000000f1"
OTHER = "3f2b9c1e-0000-4000-8000-0000000000f2"
POINT = "Quels sont nos 5 clients les plus rentables ce trimestre ?"

Message = dict[str, Any]


def schedules(**tasks: dict[str, Any]) -> Config:
    defaults = {"at": "MON 07:00", "question": POINT}
    return Config.model_validate(
        {"schedules": {name: {**defaults, **task} for name, task in tasks.items()}}
    )


@pytest.fixture
def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., TestClient]:
    # reports/ and data/scheduler.db are relative to the launch directory
    monkeypatch.chdir(tmp_path)

    def make(config: Config, pacing: Pacing = INSTANT) -> TestClient:
        return TestClient(create_app(config, MockBackend(config, pacing=pacing)))

    return make


def until(ws: WebSocketTestSession, kind: str, limit: int = 500) -> list[Message]:
    received: list[Message] = []
    for _ in range(limit):
        received.append(ws.receive_json())
        if received[-1]["type"] == kind:
            return received
    raise AssertionError(f"{kind} jamais reçu")


def run_now(ws: WebSocketTestSession, schedule_id: str = "point-lundi") -> list[Message]:
    ws.send_json({"type": "run_schedule", "id": "c-18", "schedule_id": schedule_id})
    return until(ws, "schedule_run_finished")


# --- timetable ----------------------------------------------------------------------------


def at(*args: int) -> float:
    return datetime(*args).timestamp()  # type: ignore[arg-type]


def test_next_run() -> None:
    tuesday = at(2026, 10, 27, 19, 0)
    assert next_at("MON 07:00", tuesday) == at(2026, 11, 2, 7, 0)
    assert next_at("MON 07:00", at(2026, 11, 2, 6, 59)) == at(2026, 11, 2, 7, 0)
    assert next_at("MON 07:00", at(2026, 11, 2, 7, 0)) == at(2026, 11, 9, 7, 0)  # strictly after
    assert next_at("DAILY 07:00", tuesday) == at(2026, 10, 28, 7, 0)
    assert next_at("DAILY 23:30", tuesday) == at(2026, 10, 27, 23, 30)


def test_timetable_in_words() -> None:
    assert timetable(ScheduleConfig(at="MON 07:00", question="q")) == "chaque lundi à 07:00"
    assert timetable(ScheduleConfig(at="DAILY 18:30", question="q")) == "chaque jour à 18:30"
    assert timetable(ScheduleConfig(every_minutes=60, question="q")) == "toutes les 60 minutes"


@pytest.mark.parametrize(
    "task",
    [
        {"question": "q"},  # no timetable
        {"at": "MON 07:00", "every_minutes": 60, "question": "q"},  # both
        {"at": "LUNDI 07:00", "question": "q"},
        {"at": "MON 7:00", "question": "q"},
        {"at": "MON 24:00", "question": "q"},
        {"every_minutes": 0, "question": "q"},
        {"at": "MON 07:00", "question": ""},
    ],
)
def test_a_wrong_task_is_a_configuration_error(task: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"schedules": {"t": task}})


def test_a_task_id_is_a_simple_name() -> None:
    with pytest.raises(ValidationError, match="identifiant de tâche invalide"):
        Config.model_validate({"schedules": {"../x": {"at": "MON 07:00", "question": "q"}}})


def test_the_example_task_is_valid_and_enabled() -> None:
    # Commented out in config.example.toml (no task by default): uncommented, it must load,
    # and enabled, since "Exécuter maintenant" refuses a disabled task (D-13)
    example = (Path(__file__).parents[3] / "config.example.toml").read_text(encoding="utf-8")
    start = example.index("# [schedules.point-lundi]")
    block = example[start:].split("\n\n")[0]
    table = tomllib.loads("\n".join(line.removeprefix("# ") for line in block.splitlines()))
    config = Config.model_validate(table)
    task = config.schedules["point-lundi"]
    assert (task.at, task.enabled, task.persona) == ("MON 07:00", True, "analyste")


# --- run_schedule -------------------------------------------------------------------------


def test_run_now(make_client: Callable[..., TestClient], tmp_path: Path) -> None:
    config = schedules(**{"point-lundi": {"persona": "analyste"}})
    with (
        make_client(config) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
        client.websocket_connect(f"/ws/{OTHER}") as other,
    ):
        ws.receive_json()
        other.receive_json()
        received = run_now(ws)
        seen = until(other, "schedule_run_finished")  # every open session is told
        listing = client.get("/api/schedules").json()["schedules"]
        run_id = received[-1]["run_id"]
        report = client.get(f"/api/schedules/runs/{run_id}/report")
        missing = client.get("/api/schedules/runs/nope/report")
    started, finished = received[0], received[-1]
    for message in (started, finished, *seen):
        SERVER.validate(message)
    assert [m["type"] for m in received] == ["schedule_run_started", "schedule_run_finished"]
    assert (started["reply_to"], started["trigger"], started["session_id"]) == (
        "c-18",
        "manual",
        SESSION,
    )
    assert "reply_to" not in seen[0] and seen[0]["session_id"] == OTHER
    day = datetime.now().strftime("%Y-%m-%d")
    assert finished["status"] == "ok" and finished["badge"] == "verified"
    assert finished["report_path"] == f"reports/planifie/point-lundi-{day}.md"
    assert finished["error"] is None
    written = (tmp_path / finished["report_path"]).read_text(encoding="utf-8")
    assert report.status_code == 200 and report.text == written
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "SCHEDULE_NOT_FOUND"
    [task] = listing
    assert task["next_run"] is not None and task["every_minutes"] is None
    assert [run["run_id"] for run in task["last_runs"]] == [run_id]
    assert task["last_runs"][0]["trigger"] == "manual"


def test_a_run_writes_its_report(make_client: Callable[..., TestClient], tmp_path: Path) -> None:
    with (
        make_client(schedules(**{"point-lundi": {}})) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        finished = run_now(ws)[-1]
    report = (tmp_path / finished["report_path"]).read_text(encoding="utf-8")
    assert report.startswith("# Tâche planifiée « point-lundi »")
    assert "à la demande (chaque lundi à 07:00) · modèle qwen3:8b · profil" in report
    assert NO_HUMAN in report
    assert f"## 1. {POINT}" in report
    assert "```sql" in report and "| name |" in report
    assert "**Vérification** : Vérifiée" in report


def test_the_session_stays_free_during_a_run(make_client: Callable[..., TestClient]) -> None:
    with (
        make_client(schedules(**{"point-lundi": {}}), SLOW) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        ws.send_json({"type": "run_schedule", "id": "c-18", "schedule_id": "point-lundi"})
        until(ws, "schedule_run_started")
        ws.send_json({"type": "run_schedule", "id": "c-19", "schedule_id": "point-lundi"})
        busy = until(ws, "error")[-1]
        ws.send_json({"type": "user_message", "id": "c-20", "content": POINT})
        received: list[Message] = []
        # Both end in either order: the turn of the session, and the run beside it
        while not {"message_done", "schedule_run_finished"} <= {m["type"] for m in received}:
            received.append(ws.receive_json())
    check_event(busy)
    assert (busy["code"], busy["reply_to"], busy["message"]) == (
        "BUSY",
        "c-19",
        "Cette tâche tourne déjà.",
    )
    assert "error" not in [m["type"] for m in received]  # user_message was accepted
    # The run's own events never reach the session: only its two notices
    turns = {m["turn_id"] for m in received if m["type"] == "turn_started"}
    assert len(turns) == 1


@pytest.mark.parametrize("schedule_id", ["inconnue", "off"])
def test_unknown_schedule_is_refused(
    make_client: Callable[..., TestClient], schedule_id: str
) -> None:
    with (
        make_client(schedules(off={"enabled": False})) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        ws.send_json({"type": "run_schedule", "id": "c-18", "schedule_id": schedule_id})
        error = ws.receive_json()
        listing = client.get("/api/schedules").json()["schedules"]
    check_event(error)
    assert (error["code"], error["reply_to"]) == ("SCHEDULE_NOT_FOUND", "c-18")
    assert listing[0]["next_run"] is None and listing[0]["enabled"] is False


# --- no human, no write -------------------------------------------------------------------


def test_a_write_request_writes_nothing(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    question = "Supprime les clients inactifs depuis plus de 18 mois."
    with (
        make_client(schedules(purge={"question": question})) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        finished = run_now(ws, "purge")[-1]
    # Nobody to confirm: the deletion is refused, and the report says so
    assert finished["status"] == "ok"
    report = (tmp_path / finished["report_path"]).read_text(encoding="utf-8")
    assert "**Action non exécutée** (cancelled)" in report
    assert "**Action confirmée**" not in report


def info(name: str, read_only: bool | None, destructive: bool | None) -> ToolInfo:
    return ToolInfo(
        name=name,
        server=None,
        original_name=name,
        description="",
        input_schema={"type": "object"},
        annotations=ToolAnnotations(read_only=read_only, destructive=destructive),
        trusted=True,
    )


class FakeRegistry:
    def __init__(self) -> None:
        self.infos = [
            info("sql_toolbox__run_select", True, False),
            info("sql_toolbox__execute_write", False, True),
            info("glossary_define", False, False),
            info("file_write", False, True),
            info("time__get_current_time", None, None),
        ]
        self.calls: list[str] = []

    def tools(self) -> list[ToolInfo]:
        return self.infos

    def get(self, name: str) -> ToolInfo | None:
        return next((i for i in self.infos if i.name == name), None)

    def servers(self) -> list[ServerStatus]:
        return []

    def ollama_tools(self, allowed: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": i.name}}
            for i in self.infos
            if allowed is None or allowed(i.name)
        ]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]:
        return []

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(name)
        return ToolResult(ok=True, text="", summary="")


async def test_a_scheduled_engine_has_no_tool_that_writes() -> None:
    inner = FakeRegistry()
    registry = ReadOnlyRegistry(inner)
    offered = [tool["function"]["name"] for tool in registry.ollama_tools()]
    assert offered == ["sql_toolbox__run_select", "time__get_current_time"]
    assert registry.get("sql_toolbox__execute_write") is None
    refused = await registry.call("file_write", {"path": "reports/x.md", "content": "x"})
    assert not refused.ok and refused.error is not None and refused.error.code == "TOOL_DENIED"
    assert inner.calls == []


def test_a_scheduled_run_offers_the_tools_of_its_profile_that_only_read() -> None:
    registry = ReadOnlyRegistry(FakeRegistry())
    # ADV may write in the database: a scheduled run of this profile still cannot
    adv = {"sql_toolbox__run_select", "sql_toolbox__execute_write"}.__contains__
    assert [t["function"]["name"] for t in registry.ollama_tools(adv)] == [
        "sql_toolbox__run_select"
    ]
    nothing = registry.ollama_tools(lambda name: False)
    assert nothing == []


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("reports/../config.toml", "PATH_FORBIDDEN"),
        ("reports/note.md", "PATH_FORBIDDEN"),  # under reports/, but not reports/planifie/
        ("data/point.md", "PATH_FORBIDDEN"),
        ("reports/planifie/point.py", "EXTENSION_FORBIDDEN"),
    ],
)
def test_report_path_is_checked(
    make_client: Callable[..., TestClient], tmp_path: Path, output: str, code: str
) -> None:
    with (
        make_client(schedules(t={"output": output})) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        finished = run_now(ws, "t")[-1]
    SERVER.validate(finished)
    assert (finished["status"], finished["report_path"], finished["badge"]) == ("error", None, None)
    assert finished["error"]["code"] == code
    assert not (tmp_path / "config.toml").exists() and not (tmp_path / "data/point.md").exists()


def test_an_unknown_persona_fails_the_run(make_client: Callable[..., TestClient]) -> None:
    with (
        make_client(schedules(t={"persona": "stagiaire"})) as client,
        client.websocket_connect(f"/ws/{SESSION}") as ws,
    ):
        ws.receive_json()
        finished = run_now(ws, "t")[-1]
    assert finished["error"]["code"] == "PERSONA_NOT_FOUND"


# --- the clock ----------------------------------------------------------------------------


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_a_task_starts_at_its_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = schedules(lundi={}, horaire={"at": None, "every_minutes": 60})
    sent: list[tuple[Message, str | None, str | None]] = []

    async def broadcast(message: Message, requester: str | None, reply_to: str | None) -> None:
        sent.append((message, requester, reply_to))

    clock = Clock(at(2026, 11, 2, 6, 0))  # Monday, 06:00
    scheduler = Scheduler(config, MockBackend(config, pacing=INSTANT), broadcast, clock=clock)
    await scheduler.start()
    try:
        await scheduler.tick()
        assert sent == []  # nothing is due yet
        next_runs = {task["id"]: task["next_run"] for task in scheduler.describe()}
        assert next_runs == {"lundi": at(2026, 11, 2, 7, 0), "horaire": at(2026, 11, 2, 7, 0)}
        clock.now = at(2026, 11, 2, 7, 0, 5)
        await scheduler.tick()
        await scheduler.wait()
    finally:
        await scheduler.stop()
    kinds = sorted((m["type"], m["schedule_id"]) for m, _, _ in sent)
    assert kinds == [
        ("schedule_run_finished", "horaire"),
        ("schedule_run_finished", "lundi"),
        ("schedule_run_started", "horaire"),
        ("schedule_run_started", "lundi"),
    ]
    assert all(m.get("trigger", "schedule") == "schedule" for m, _, _ in sent)
    tasks = {task["id"]: task for task in scheduler.describe()}
    assert tasks["lundi"]["next_run"] == at(2026, 11, 9, 7, 0)
    assert tasks["horaire"]["next_run"] == at(2026, 11, 2, 8, 0, 5)
    assert tasks["lundi"]["last_runs"][0]["status"] == "ok"


async def test_an_interval_counts_from_the_last_scheduled_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "scheduler.db")
    store.start("r-1", "horaire", "schedule", at(2026, 11, 2, 7, 0))
    store.start("r-2", "horaire", "manual", at(2026, 11, 2, 7, 30))  # does not move the timetable
    config = schedules(horaire={"at": None, "every_minutes": 60})

    async def broadcast(message: Message, requester: str | None, reply_to: str | None) -> None:
        return None

    clock = Clock(at(2026, 11, 2, 7, 45))
    scheduler = Scheduler(config, MockBackend(config), broadcast, store=store, clock=clock)
    await scheduler.start()
    try:
        assert scheduler.describe()[0]["next_run"] == at(2026, 11, 2, 8, 0)
    finally:
        await scheduler.stop()


def test_a_run_cut_by_a_restart_ends_in_error(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "scheduler.db")
    store.start("r-1", "point-lundi", "manual", 1.0)
    assert store.last_runs("point-lundi")[0]["status"] is None  # running
    store.abandon_unfinished(2.0)
    run = store.last_runs("point-lundi")[0]
    assert (run["status"], run["finished_at"], run["error"]["code"]) == ("error", 2.0, "INTERNAL")
    store.close()


def test_without_tasks_the_database_is_never_created(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "scheduler.db")
    store.abandon_unfinished(1.0)
    assert store.last_runs("x") == [] and store.get("x") is None
    assert not (tmp_path / "scheduler.db").exists()


@pytest.fixture(autouse=True)
def _no_stray_files() -> Iterator[None]:
    # The tests above chdir to tmp_path: nothing lands in the repository
    repository = Path(__file__).parents[3]
    before = (repository / "data" / "scheduler.db").exists()
    yield
    assert (repository / "data" / "scheduler.db").exists() == before


# --- sensai schedule ----------------------------------------------------------------------


def test_the_cli_lists_and_runs_a_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from sensai.__main__ import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SENSAI_CONFIG", raising=False)
    (tmp_path / "config.toml").write_text(
        f'[schedules.point-lundi]\nat = "MON 07:00"\nquestion = "{POINT}"\n', encoding="utf-8"
    )
    assert main(["schedule", "--mock"]) == 0
    assert "point-lundi  chaque lundi à 07:00  prochaine : " in capsys.readouterr().out
    assert main(["schedule", "run", "point-lundi", "--mock"]) == 0
    day = datetime.now().strftime("%Y-%m-%d")
    assert f"Rapport : reports/planifie/point-lundi-{day}.md" in capsys.readouterr().out
    assert main(["schedule", "run", "inconnue", "--mock"]) == 1
    assert "n'existe pas" in capsys.readouterr().err
    assert main(["schedule", "run"]) == 2
