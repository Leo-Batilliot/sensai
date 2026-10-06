"""Test doubles shared by the unit tests: no network, no Ollama."""

import asyncio
import signal
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from sensai.core.errors import ModelNotFound
from sensai.llm.ollama_client import Chunk, Message, ModelDetails, Usage

MODEL = "qwen3:8b"
OTHER_MODEL = "llama3.1:8b"


@dataclass(frozen=True)
class Pause:
    """Stream item: the user presses Ctrl+C (ctrl_c), and/or the stream waits until
    cancelled (hang). Without hang, the stream goes on: the engine sees its cancel flag
    on the next chunk."""

    ctrl_c: bool = False
    hang: bool = True


HANG = Pause()
CTRL_C = Pause(ctrl_c=True, hang=False)

StreamItem = Chunk | Exception | Pause


def answer(*parts: str, prompt_tokens: int = 12, completion_tokens: int = 3) -> list[StreamItem]:
    """A complete reply: one chunk per part, then the final chunk with the token counts."""
    usage = Usage(prompt_tokens, completion_tokens, total_duration_ms=50.0)
    final = Chunk(done=True, done_reason="stop", usage=usage)
    return [*(Chunk(content=part) for part in parts), final]


class FakeClient:
    """Stands in for OllamaClient: each chat_stream call plays the next scripted reply."""

    def __init__(
        self,
        *replies: list[StreamItem],
        models: tuple[str, ...] = (MODEL, OTHER_MODEL),
        capabilities: tuple[str, ...] = ("completion", "tools"),
        show_error: Exception | None = None,
    ) -> None:
        self.replies = list(replies)
        self.models = models
        self.capabilities = capabilities
        self.show_error = show_error
        self.calls: list[tuple[str, list[Message], dict[str, Any] | None]] = []
        self.open_streams = 0
        self.closed = False

    async def chat_stream(
        self, model: str, messages: list[Message], *, options: dict[str, Any] | None = None
    ) -> AsyncGenerator[Chunk, None]:
        self.calls.append((model, messages, options))
        reply = self.replies.pop(0)
        self.open_streams += 1
        try:
            for item in reply:
                await asyncio.sleep(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, Pause):
                    if item.ctrl_c:
                        signal.raise_signal(signal.SIGINT)
                        await asyncio.sleep(0)  # lets the SIGINT callback run
                    if item.hang:
                        await asyncio.Event().wait()
                    continue
                yield item
        finally:
            self.open_streams -= 1

    async def show(self, model: str) -> ModelDetails:
        if self.show_error is not None:
            raise self.show_error
        if model not in self.models:
            raise ModelNotFound(model)
        return ModelDetails(name=model, capabilities=self.capabilities, context_length=8192)

    async def list_models(self) -> list[str]:
        if self.show_error is not None:
            raise self.show_error
        return list(self.models)

    async def aclose(self) -> None:
        self.closed = True
