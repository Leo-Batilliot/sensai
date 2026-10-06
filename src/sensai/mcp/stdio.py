"""stdio transport: an MCP server run as a subprocess, one JSON-RPC message per line.

The server reads requests on stdin and writes messages on stdout; stderr is only for
its logs. Both outputs are read continuously: a server whose pipe buffer fills up
blocks on its next write.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
from collections import deque
from collections.abc import Callable
from typing import Protocol

from sensai.core.errors import McpCommandNotFound, McpServerDied
from sensai.mcp import jsonrpc
from sensai.mcp.jsonrpc import Message

logger = logging.getLogger(__name__)

# asyncio streams refuse lines longer than their limit (64 KiB by default): a large tool
# result fits in one line, so the limit is raised.
STREAM_LIMIT = 16 * 1024 * 1024
STDERR_TAIL_LINES = 20
STOP_GRACE_S = 2.0

OnMessage = Callable[[Message], None]
OnClosed = Callable[[str], None]


class Transport(Protocol):
    """What McpClient needs from a transport; tests replace the subprocess with a fake."""

    async def start(self, on_message: OnMessage, on_closed: OnClosed) -> None: ...
    async def send(self, message: Message) -> None: ...
    async def close(self) -> None: ...


class StdioTransport:
    """Starts one MCP server process and exchanges line-delimited messages with it.

    `on_message` is called for every JSON object read on stdout, `on_closed` once when
    stdout ends, whether the server crashed or `close` stopped it.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | tuple[str, ...] = (),
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        stop_grace_s: float = STOP_GRACE_S,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args)
        # The server inherits our environment (PATH and HOME are needed by uvx and npx),
        # completed by the variables of the configuration.
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.stop_grace_s = stop_grace_s
        self.stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._server_logger = logging.getLogger(f"{__name__}.server.{name}")
        self._process: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process else None

    async def start(self, on_message: OnMessage, on_closed: OnClosed) -> None:
        executable = shutil.which(self.command, path=self.env.get("PATH"))
        if executable is None:
            raise McpCommandNotFound(self.name, self.command)
        try:
            self._process = await asyncio.create_subprocess_exec(
                executable,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
                cwd=self.cwd,
                limit=STREAM_LIMIT,
                # Own process group: stopping the server also stops its children
                # (uvx and npx start the real server as a child process).
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise McpServerDied(self.name, f"démarrage impossible : {exc}") from exc
        logger.debug("MCP server %s started (pid %s)", self.name, self._process.pid)
        self._readers = [
            asyncio.ensure_future(self._read_stdout(on_message, on_closed)),
            asyncio.ensure_future(self._read_stderr()),
        ]

    async def send(self, message: Message) -> None:
        process = self._process
        if self._closed or process is None or process.stdin is None:
            raise McpServerDied(self.name, "connexion fermée")
        async with self._write_lock:
            try:
                process.stdin.write(jsonrpc.encode(message))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise McpServerDied(self.name, "entrée standard fermée") from exc

    async def close(self) -> None:
        """Stop the server: close stdin, then SIGTERM, then SIGKILL, 2 s apart."""
        process = self._process
        if process is None or self._closed:
            return
        self._closed = True
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        if not await self._wait_exit(process):
            self._signal(process, signal.SIGTERM)
            if not await self._wait_exit(process):
                self._signal(process, signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
                await process.wait()
        # The readers end on EOF; a leftover child that still holds the pipes must not
        # keep close() waiting.
        _, pending = await asyncio.wait(self._readers, timeout=self.stop_grace_s)
        for task in pending:
            task.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)
        logger.debug("MCP server %s stopped (code %s)", self.name, process.returncode)

    async def _wait_exit(self, process: asyncio.subprocess.Process) -> bool:
        try:
            await asyncio.wait_for(process.wait(), self.stop_grace_s)
        except asyncio.TimeoutError:
            return False
        return True

    @staticmethod
    def _signal(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        with contextlib.suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, sig)
            else:
                process.send_signal(sig)

    async def _read_stdout(self, on_message: OnMessage, on_closed: OnClosed) -> None:
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        reason = "sortie standard fermée"
        try:
            while line := await stdout.readline():
                if not line.strip():
                    continue
                try:
                    message = jsonrpc.decode(line)
                except ValueError:
                    # Some servers print banners on stdout: skip them, the content stays
                    # out of INFO logs because it may hold business data.
                    logger.warning("MCP server %s: ignored a line that is not JSON-RPC", self.name)
                    logger.debug("MCP server %s: ignored line %r", self.name, line[:500])
                    continue
                try:
                    on_message(message)
                except Exception:
                    logger.exception("MCP server %s: failed to handle a message", self.name)
        except ValueError:
            # readline() raises ValueError when a line exceeds STREAM_LIMIT: the stream
            # cannot be resynchronised, the connection is lost.
            reason = "message trop long"
        finally:
            on_closed(reason)

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        stderr = self._process.stderr
        while True:
            try:
                line = await stderr.readline()
            except ValueError:
                # Over-long log line: readline() dropped it, keep draining the pipe
                continue
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            self.stderr_tail.append(text)
            self._server_logger.debug("%s", text)
