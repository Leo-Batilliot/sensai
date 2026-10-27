"""Assembly of the application (CORE-10): one way to build the engine, for every adapter.

Services start once per application and are shared by every session (PROJECT.md §5.2):
the MCP servers (one McpManager, never one per session), the memory store and the system
prompt (the schema is read once). Each session then gets its own Engine from
new_engine(): its Ollama client, its history, its registry (the memory tools audit the
session id), its broker, the memory context, the judge and the SQL classifier of the HITL
policy (a read-only connection, closed with the Engine).

The CLI (`sensai chat`) and the web (`sensai web`, WEB-08) both build their engines here,
so that a question behaves the same way in the terminal and in the browser.
"""

from __future__ import annotations

from collections.abc import Callable

from sensai.core.ask import AskUserTool, QuestionBroker
from sensai.core.config import Config
from sensai.core.confirm import ConfirmationBroker
from sensai.core.engine import ChatClient, Engine
from sensai.core.prompts import system_prompt_template
from sensai.eval.online import OnlineJudge
from sensai.llm.ollama_client import OllamaClient
from sensai.mcp.manager import McpManager
from sensai.memory.context import MemoryContext
from sensai.memory.store import MemoryStore
from sensai.tools.file_permissions import FilePermissions
from sensai.tools.file_tools import file_tools, file_write_impact
from sensai.tools.memory_tools import memory_tools
from sensai.tools.policy import SqlClassifier
from sensai.tools.registry import Registry

MEMORY_DELETE = "memory_delete"
FILE_WRITE = "file_write"

ClientFactory = Callable[[Config], ChatClient]


def ollama_client(config: Config) -> OllamaClient:
    """An Ollama client for one session: closed with its Engine (Engine.aclose)."""
    return OllamaClient(
        config.ollama.host, timeout_s=config.ollama.timeout_s, keep_alive=config.ollama.keep_alive
    )


class Services:
    """What the application starts once: MCP servers, memory store and system prompt."""

    def __init__(
        self,
        config: Config,
        *,
        manager: McpManager | None = None,
        client_factory: ClientFactory = ollama_client,
        sql_classifier: SqlClassifier | None = None,
    ) -> None:
        self.config = config
        self.manager = manager if manager is not None else McpManager.from_config(config)
        self.client_factory = client_factory
        self._sql_classifier = sql_classifier  # tests only: None = the business database
        # Roots resolved once, relative to the launch directory
        self.files = FilePermissions.from_config(config.files)
        self._store: MemoryStore | None = None
        self._prompt: Callable[[str], str] | None = None

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            raise RuntimeError("Services.start() must run first")
        return self._store

    async def start(self) -> None:
        """Start the MCP servers, open the memory and build the system prompt.

        A server that cannot start is marked down with its reason (McpManager), never
        raised: the others keep working. The business database must exist (`sensai seed`).
        """
        await self.manager.start_all()
        self._store = MemoryStore.open(self.config.database.memory_path)
        tool_names = [info.name for info in self.registry().tools()]
        self._prompt = system_prompt_template(self.config.database.business_path, tool_names)

    async def stop(self) -> None:
        await self.manager.stop_all()
        if self._store is not None:
            self._store.close()
            self._store = None

    def registry(self, session_id: str | None = None) -> Registry:
        """MCP tools, memory tools, file tools and ask_user; memory and file tools write
        session_id in their audit."""
        store = self.store
        return Registry(
            self.manager,
            [*memory_tools(store, session_id), *file_tools(self.files, session_id), AskUserTool()],
            previewers={
                MEMORY_DELETE: lambda args: store.delete_impact(args["kind"], args["ref"]),
                FILE_WRITE: file_write_impact(self.files),
            },
        )

    def new_engine(
        self,
        broker: ConfirmationBroker | None = None,
        session_id: str | None = None,
        questions: QuestionBroker | None = None,
    ) -> Engine:
        """A fully wired Engine for one session (tools, HITL, questions, memory, judge)."""
        if self._prompt is None:
            raise RuntimeError("Services.start() must run before the first engine")
        client = self.client_factory(self.config)
        return Engine(
            self.config,
            client,
            registry=self.registry(session_id),
            system_prompt=self._prompt,
            broker=broker,
            session_id=session_id,
            memory=MemoryContext(self.store),
            judge=OnlineJudge.from_config(self.config, client),
            sql_classifier=self._sql_classifier,
            questions=questions,
        )
