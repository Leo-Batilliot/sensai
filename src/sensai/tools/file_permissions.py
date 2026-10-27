"""Where the file tools may go: the permission check done before any file operation.

The agent reaches files only under the roots declared in [files] (reports/ read-write,
data/imports/ read-only by default). The check works on the real path: it follows
symbolic links and resolves "..", so "reports/../config.toml" or a link from reports/ to
/etc/passwd are refused like "/etc/passwd" itself. Paths are compared as resolved paths,
never as strings. Every decision is appended to an audit file.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sensai.core.config import FilesConfig

logger = logging.getLogger(__name__)

Op = Literal["list", "read", "write"]
DEFAULT_AUDIT_PATH = Path("data/file_audit.jsonl")


class FileAccessDenied(Exception):
    """A refused file operation, with the code the model and the interface receive."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Root:
    path: Path  # resolved
    mode: Literal["r", "rw"]
    shown: str  # as written in the configuration, for messages and listings


class FilePermissions:
    """Checks every path before the file tools open anything."""

    def __init__(
        self,
        roots: Sequence[tuple[str | Path, Literal["r", "rw"]]],
        *,
        deny: Sequence[str] = (),
        write_extensions: Sequence[str] = (),
        max_read_bytes: int = 200_000,
        base_dir: str | Path | None = None,
        audit_path: str | Path | None = DEFAULT_AUDIT_PATH,
    ) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()
        self.roots = [
            Root(self._resolve(str(path)), mode, Path(path).as_posix()) for path, mode in roots
        ]
        self.deny = [pattern.lower() for pattern in deny]
        self.write_extensions = {extension.lower() for extension in write_extensions}
        self.max_read_bytes = max_read_bytes
        self.audit_path = None if audit_path is None else self.base_dir / audit_path

    @classmethod
    def from_config(
        cls,
        files: FilesConfig,
        *,
        base_dir: str | Path | None = None,
        audit_path: str | Path | None = DEFAULT_AUDIT_PATH,
    ) -> FilePermissions:
        return cls(
            [(root.path, root.mode) for root in files.roots],
            deny=files.deny,
            write_extensions=files.write_extensions,
            max_read_bytes=files.max_read_bytes,
            base_dir=base_dir,
            audit_path=audit_path,
        )

    def check(self, op: Op, path: str, *, tool: str = "", session_id: str | None = None) -> Path:
        """The resolved path if `op` is allowed on it, else FileAccessDenied (logged)."""
        try:
            resolved = self.verify(op, path)
        except FileAccessDenied as denied:
            self._audit(tool, path, "denied", f"{denied.code}: {denied.reason}", session_id)
            raise
        if op == "write":
            self._audit(tool, path, "allowed", "", session_id)
        return resolved

    def shown(self, path: Path) -> str:
        """A resolved path as the user knows it: relative to the launch directory."""
        try:
            return path.relative_to(self.base_dir).as_posix()
        except ValueError:
            return path.as_posix()

    def root_of(self, path: Path) -> Root | None:
        """The root that contains this resolved path, if any."""
        for root in self.roots:
            if _is_under(path, root.path):
                return root
        return None

    def denied_name(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in self.deny)

    def verify(self, op: Op, path: str) -> Path:
        """The same decision as check(), without an audit line: for previews."""
        # 1. Refuse what is never a path we want, before touching the file system
        if not path or not path.strip():
            raise FileAccessDenied("PATH_FORBIDDEN", "chemin vide")
        if "\x00" in path:
            raise FileAccessDenied("PATH_FORBIDDEN", "caractère nul dans le chemin")
        if any(self.denied_name(part) for part in Path(path).parts):
            raise FileAccessDenied("PATH_FORBIDDEN", "fichier protégé, toujours refusé")
        # 2. The real path: links followed, ".." resolved
        resolved = self._resolve(path)
        if any(self.denied_name(part) for part in resolved.parts):
            raise FileAccessDenied("PATH_FORBIDDEN", "fichier protégé, toujours refusé")
        # 3. Under a root
        root = self.root_of(resolved)
        if root is None:
            allowed = ", ".join(f"{root.shown}/ ({root.mode})" for root in self.roots)
            raise FileAccessDenied(
                "PATH_FORBIDDEN", f"hors des dossiers autorisés ({allowed or 'aucun'})"
            )
        # 4. Mode, extension, existence and size
        if op == "write":
            if root.mode != "rw":
                raise FileAccessDenied("MODE_FORBIDDEN", f"{root.shown}/ est en lecture seule")
            if resolved == root.path or resolved.is_dir():
                raise FileAccessDenied("PATH_FORBIDDEN", "c'est un dossier, pas un fichier")
            if resolved.suffix.lower() not in self.write_extensions:
                allowed = ", ".join(sorted(self.write_extensions))
                raise FileAccessDenied(
                    "EXTENSION_FORBIDDEN", f"extension refusée (autorisées : {allowed})"
                )
        elif op == "read":
            if not resolved.is_file():
                raise FileAccessDenied("FILE_NOT_FOUND", "fichier introuvable")
            size = resolved.stat().st_size
            if size > self.max_read_bytes:
                raise FileAccessDenied(
                    "FILE_TOO_LARGE",
                    f"{size} octets, au-delà de la limite de {self.max_read_bytes} octets",
                )
        elif not resolved.is_dir():
            raise FileAccessDenied("FILE_NOT_FOUND", "dossier introuvable")
        return resolved

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.base_dir / candidate
        # strict=False: a file to write does not exist yet; existing links are followed
        return candidate.resolve(strict=False)

    def _audit(
        self, tool: str, path: str, decision: str, reason: str, session_id: str | None
    ) -> None:
        if self.audit_path is None:
            return
        entry = {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id,
            "tool": tool,
            "path": path.replace("\x00", "\\0"),
            "decision": decision,
            "reason": reason,
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as audit:
                audit.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # The audit must never stop the agent; the refusal itself still applies
            logger.exception("File audit not written")


def _is_under(path: Path, root: Path) -> bool:
    """Compare resolved paths with the platform's case rules (Windows ignores case)."""
    try:
        common = os.path.commonpath([os.path.normcase(path), os.path.normcase(root)])
    except ValueError:  # different drives on Windows
        return False
    return common == os.path.normcase(root)
