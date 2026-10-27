import json
import os
from pathlib import Path

import pytest

from sensai.core.config import FilesConfig
from sensai.tools.file_permissions import FileAccessDenied, FilePermissions


@pytest.fixture
def base(tmp_path: Path) -> Path:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "note.md").write_text("# Note\n", encoding="utf-8")
    (tmp_path / "data" / "imports").mkdir(parents=True)
    (tmp_path / "data" / "imports" / "objectifs.csv").write_text("a,b\n", encoding="utf-8")
    (tmp_path / "data" / "business.db").write_bytes(b"SQLite")
    (tmp_path / "config.toml").write_text("[hitl]\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "reports" / "big.txt").write_text("x" * 2000, encoding="utf-8")
    return tmp_path


@pytest.fixture
def permissions(base: Path) -> FilePermissions:
    files = FilesConfig(max_read_bytes=1000)
    return FilePermissions.from_config(files, base_dir=base, audit_path="data/file_audit.jsonl")


def audit_lines(base: Path) -> list[dict[str, str]]:
    path = base / "data" / "file_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("op", "path", "code"),
    [
        ("write", "reports/nouveau.md", None),
        ("read", "reports/note.md", None),
        ("read", "data/imports/objectifs.csv", None),
        ("list", "reports", None),
        ("list", "data/imports", None),
        ("read", "reports/../config.toml", "PATH_FORBIDDEN"),
        ("read", "config.toml", "PATH_FORBIDDEN"),
        ("read", ".env", "PATH_FORBIDDEN"),
        ("read", "data/business.db", "PATH_FORBIDDEN"),
        ("read", "/etc/passwd", "PATH_FORBIDDEN"),
        ("read", "src/sensai/__init__.py", "PATH_FORBIDDEN"),
        ("list", ".", "PATH_FORBIDDEN"),  # the launch directory itself is not a root
        ("read", "", "PATH_FORBIDDEN"),
        ("read", "reports/no\x00te.md", "PATH_FORBIDDEN"),
        ("write", "reports/.env", "PATH_FORBIDDEN"),
        ("write", "reports/x.py", "EXTENSION_FORBIDDEN"),
        ("write", "reports", "PATH_FORBIDDEN"),
        ("write", "data/imports/objectifs.csv", "MODE_FORBIDDEN"),
        ("read", "reports/missing.md", "FILE_NOT_FOUND"),
        ("list", "reports/missing", "FILE_NOT_FOUND"),
        ("read", "reports/big.txt", "FILE_TOO_LARGE"),
    ],
)
def test_paths(permissions: FilePermissions, op: str, path: str, code: str | None) -> None:
    if code is None:
        resolved = permissions.check(op, path)  # type: ignore[arg-type]
        assert permissions.root_of(resolved) is not None
    else:
        with pytest.raises(FileAccessDenied) as denied:
            permissions.check(op, path)  # type: ignore[arg-type]
        assert denied.value.code == code
        assert denied.value.reason


def test_absolute_path_inside_a_root_is_accepted(permissions: FilePermissions, base: Path) -> None:
    assert (
        permissions.check("read", str(base / "reports" / "note.md"))
        == (base / "reports" / "note.md").resolve()
    )


@pytest.mark.skipif(os.name != "posix", reason="symbolic links need privileges on Windows")
def test_a_link_that_leaves_a_root_is_refused(permissions: FilePermissions, base: Path) -> None:
    (base / "reports" / "escape.md").symlink_to(base / "config.toml")
    (base / "reports" / "outside").symlink_to(base / "data", target_is_directory=True)
    with pytest.raises(FileAccessDenied) as denied:
        permissions.check("read", "reports/escape.md")
    assert denied.value.code == "PATH_FORBIDDEN"
    with pytest.raises(FileAccessDenied):
        permissions.check("write", "reports/outside/x.md")


def test_refusals_and_writes_are_audited(permissions: FilePermissions, base: Path) -> None:
    with pytest.raises(FileAccessDenied):
        permissions.check("read", "config.toml", tool="file_read", session_id="s-1")
    permissions.check("write", "reports/ok.md", tool="file_write", session_id="s-1")
    permissions.check("read", "reports/note.md")  # allowed reads are not logged
    denied, allowed = audit_lines(base)
    assert denied["decision"] == "denied" and denied["path"] == "config.toml"
    assert denied["tool"] == "file_read" and denied["session_id"] == "s-1"
    assert denied["reason"].startswith("PATH_FORBIDDEN")
    assert allowed["decision"] == "allowed" and allowed["path"] == "reports/ok.md"


def test_verify_decides_without_auditing(permissions: FilePermissions, base: Path) -> None:
    with pytest.raises(FileAccessDenied):
        permissions.verify("read", "config.toml")
    assert audit_lines(base) == []


def test_default_roots_and_shown_paths(base: Path) -> None:
    permissions = FilePermissions.from_config(FilesConfig(), base_dir=base, audit_path=None)
    assert [(root.shown, root.mode) for root in permissions.roots] == [
        ("reports", "rw"),
        ("data/imports", "r"),
    ]
    assert permissions.shown((base / "reports" / "x.md").resolve()) == "reports/x.md"


def test_paths_with_spaces_and_accents(tmp_path: Path) -> None:
    base = tmp_path / "3eme année"
    (base / "reports").mkdir(parents=True)
    permissions = FilePermissions([("reports", "rw")], write_extensions=[".md"], base_dir=base)
    assert permissions.check("write", "reports/note comité.md").name == "note comité.md"
