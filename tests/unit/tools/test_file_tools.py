"""The file tools alone, and through the Engine with its confirmation."""

import contextlib
from pathlib import Path
from typing import Any

import pytest

from event_checks import check_turn
from fakes import FakeClient, answer, tool_request
from sensai.core import events
from sensai.core.config import Config, FilesConfig
from sensai.core.confirm import Decision, PendingAction
from sensai.core.engine import Engine
from sensai.tools.file_permissions import FilePermissions
from sensai.tools.file_tools import DATA_NOTICE, FileTool, file_tools, file_write_impact
from sensai.tools.policy import evaluate
from sensai.tools.registry import Registry

NOTE = "# Note pour le comité\n\n## Synthèse\nChiffre d'affaires en hausse.\n"


@pytest.fixture
def base(tmp_path: Path) -> Path:
    (tmp_path / "data" / "imports").mkdir(parents=True)
    (tmp_path / "data" / "imports" / "objectifs.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text("[hitl]\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def permissions(base: Path) -> FilePermissions:
    return FilePermissions.from_config(FilesConfig(), base_dir=base, audit_path=None)


@pytest.fixture
def tools(permissions: FilePermissions) -> dict[str, FileTool]:
    return {tool.name: tool for tool in file_tools(permissions, "s-1")}  # type: ignore[misc]


async def test_list_roots_then_a_folder(tools: dict[str, FileTool]) -> None:
    roots = await tools["file_list"].call({"path": "."})
    assert roots.ok and roots.data is not None
    assert [(root["path"], root["mode"]) for root in roots.data["roots"]] == [
        ("reports", "rw"),
        ("data/imports", "r"),
    ]
    folder = await tools["file_list"].call({"path": "data/imports"})
    assert folder.data == {
        "path": "data/imports",
        "entries": [{"name": "objectifs.csv", "type": "file", "bytes": 8}],
    }


async def test_read_marks_the_content_as_data(tools: dict[str, FileTool]) -> None:
    result = await tools["file_read"].call({"path": "data/imports/objectifs.csv"})
    assert result.ok and result.text.startswith(DATA_NOTICE)
    assert result.data is not None and result.data["content"] == "a,b\n1,2\n"
    cut = await tools["file_read"].call({"path": "data/imports/objectifs.csv", "max_bytes": 3})
    assert cut.data is not None and cut.data["truncated"] and cut.data["content"] == "a,b"


async def test_refusals_are_tool_failures(tools: dict[str, FileTool]) -> None:
    result = await tools["file_read"].call({"path": "config.toml"})
    assert result.error is not None and result.error.code == "PATH_FORBIDDEN"
    assert "Accès refusé" in result.text


async def test_write_creates_reports_and_the_file(tools: dict[str, FileTool], base: Path) -> None:
    result = await tools["file_write"].call({"path": "reports/note.md", "content": NOTE})
    assert result.ok and result.summary == "Fichier créé : reports/note.md"
    assert (base / "reports" / "note.md").read_text(encoding="utf-8") == NOTE
    assert not list((base / "reports").glob(".*.tmp"))  # atomic write leaves nothing behind


async def test_existing_file_is_kept(tools: dict[str, FileTool], base: Path) -> None:
    await tools["file_write"].call({"path": "reports/note.md", "content": "v1"})
    kept = await tools["file_write"].call({"path": "reports/note.md", "content": "v2"})
    assert kept.error is not None and kept.error.code == "FILE_EXISTS"
    assert (base / "reports" / "note.md").read_text(encoding="utf-8") == "v1"
    replaced = await tools["file_write"].call(
        {"path": "reports/note.md", "content": "v2", "overwrite": True}
    )
    assert replaced.ok and replaced.summary == "Fichier remplacé : reports/note.md"


async def test_every_tool_checks_before_opening(
    permissions: FilePermissions, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[tuple[str, str]] = []
    original = permissions.check

    def counting(op: Any, path: str, **kwargs: Any) -> Path:
        checked.append((op, path))
        return original(op, path, **kwargs)

    monkeypatch.setattr(permissions, "check", counting)
    tools = {tool.name: tool for tool in file_tools(permissions)}
    await tools["file_list"].call({"path": "data/imports"})
    await tools["file_read"].call({"path": "data/imports/objectifs.csv"})
    await tools["file_write"].call({"path": "reports/x.md", "content": "x"})
    assert checked == [
        ("list", "data/imports"),
        ("read", "data/imports/objectifs.csv"),
        ("write", "reports/x.md"),
    ]


def test_write_always_needs_a_confirmation(tools: dict[str, FileTool]) -> None:
    infos = {info.name: info for info in Registry(native_tools=list(tools.values())).tools()}
    assert evaluate(infos["file_write"], {}, Config().hitl).action == "confirm"
    assert evaluate(infos["file_read"], {}, Config().hitl).action == "allow"
    assert evaluate(infos["file_list"], {}, Config().hitl).action == "allow"


def test_preview_of_a_write(permissions: FilePermissions) -> None:
    impact = file_write_impact(permissions)({"path": "reports/note.md", "content": NOTE})
    assert impact["file"] == {
        "path": "reports/note.md",
        "bytes": len(NOTE.encode("utf-8")),
        "exists": False,
        "excerpt": NOTE,
    }
    refused = file_write_impact(permissions)({"path": "config.toml", "content": "x"})
    assert refused["blocked"][0]["reason"].startswith("PATH_FORBIDDEN")


# --- Through the Engine ---


class Broker:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.actions: list[PendingAction] = []

    async def request(self, action: PendingAction) -> Decision:
        self.actions.append(action)
        return self.decision


async def turn(
    permissions: FilePermissions, broker: Broker, *calls: list[Any]
) -> list[events.Event]:
    registry = Registry(
        native_tools=file_tools(permissions, "s-1"),
        previewers={"file_write": file_write_impact(permissions)},
    )
    engine = Engine(
        Config(),
        FakeClient(*calls, answer("C'est fait.")),
        registry=registry,
        broker=broker,
        session_id="s-1",
        sql_classifier=lambda sql: None,
    )
    async with contextlib.aclosing(engine.run_turn("Exporte la note")) as running:
        emitted = [event async for event in running]
    check_turn(emitted)
    return emitted


async def test_write_needs_confirmation_and_shows_the_file(
    permissions: FilePermissions, base: Path
) -> None:
    broker = Broker(Decision("approve", "user"))
    emitted = await turn(
        permissions, broker, tool_request("file_write", path="reports/note-comite.md", content=NOTE)
    )
    (required,) = [event for event in emitted if isinstance(event, events.ConfirmationRequired)]
    assert required.impact["file"]["path"] == "reports/note-comite.md"
    assert required.impact["file"]["excerpt"].startswith("# Note pour le comité")
    assert (base / "reports" / "note-comite.md").read_text(encoding="utf-8") == NOTE


async def test_refused_write_creates_nothing(permissions: FilePermissions, base: Path) -> None:
    broker = Broker(Decision("reject", "user"))
    await turn(permissions, broker, tool_request("file_write", path="reports/n.md", content=NOTE))
    assert broker.actions and not (base / "reports" / "n.md").exists()


async def test_reading_the_configuration_is_refused(permissions: FilePermissions) -> None:
    emitted = await turn(
        permissions,
        Broker(Decision("approve", "user")),
        tool_request("file_read", path="config.toml"),
    )
    (result,) = [event for event in emitted if isinstance(event, events.ToolResult)]
    assert result.error is not None and result.error.code == "PATH_FORBIDDEN"
