import asyncio
import io
import threading

import pytest
from rich.console import Console

from sensai.cli.confirm import CliConfirmationBroker
from sensai.cli.render import Renderer
from sensai.core.confirm import PendingAction

ACTION = PendingAction(
    action_id="a-1",
    call_id="k-1",
    tool="memory_delete",
    sql=None,
    impact={
        "operation": "DELETE",
        "rows_affected": 1,
        "per_table": {},
        "sample": [],
        "blocked": [],
    },
    expires_in_s=120,
)


def renderer() -> tuple[Renderer, io.StringIO]:
    out = io.StringIO()
    return Renderer(Console(file=out, width=100, color_system=None, highlight=False)), out


def answering(text: str) -> CliConfirmationBroker:
    return CliConfirmationBroker(renderer()[0], lambda prompt: text)


@pytest.mark.parametrize("text", ["o", "oui", "y", "yes", "  OUI  "])
async def test_explicit_yes_approves(text: str) -> None:
    decision = await answering(text).request(ACTION)
    assert (decision.decision, decision.reason) == ("approve", "user")


@pytest.mark.parametrize("text", ["", "n", "non", "peut-être", "ouiii"])
async def test_anything_else_rejects(text: str) -> None:
    decision = await answering(text).request(ACTION)
    assert (decision.decision, decision.reason) == ("reject", "user")


async def test_end_of_input_rejects() -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    decision = await CliConfirmationBroker(renderer()[0], closed).request(ACTION)
    assert decision.decision == "reject"


async def test_ctrl_c_refuses_the_pending_action() -> None:
    release = threading.Event()
    render, out = renderer()
    broker = CliConfirmationBroker(render, lambda prompt: release.wait(5) and "o")
    assert not broker.interrupt()  # nothing waiting yet
    pending = asyncio.ensure_future(broker.request(ACTION))
    await asyncio.sleep(0.05)
    assert broker.interrupt()
    decision = await pending
    assert (decision.decision, decision.reason) == ("reject", "cancelled")
    assert "interruption" in out.getvalue()
    release.set()  # the late "o" is ignored


async def test_timeout_is_reported() -> None:
    release = threading.Event()
    render, out = renderer()
    broker = CliConfirmationBroker(render, lambda prompt: release.wait(5) and "o")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(broker.request(ACTION), 0.05)
    assert "Délai dépassé" in out.getvalue()
    release.set()


def test_panel_shows_the_impact() -> None:
    render, out = renderer()
    render.confirmation(
        "sql_toolbox__execute_write",
        "DELETE FROM clients\n WHERE status = 'inactive'",
        {
            "operation": "DELETE",
            "rows_affected": 25,
            "per_table": {"clients": -25, "contacts": -41},
            "sample": [{"id": 12, "name": "Atelier Morel"}],
            "blocked": [{"reason": "factures à conserver", "count": None}],
        },
        120,
    )
    text = out.getvalue()
    for expected in (
        "Confirmation requise",
        "DELETE FROM clients WHERE status = 'inactive'",
        "lignes visées : 25",
        "clients : -25",
        "contacts : -41",
        "id=12, name=Atelier Morel",
        "Bloqué : factures à conserver",
        "120 s",
    ):
        assert expected in text


def test_panel_without_preview() -> None:
    render, out = renderer()
    render.confirmation("fs__write_file", None, ACTION.impact | {"rows_affected": 0}, 30)
    assert "Aperçu de l'impact indisponible" in out.getvalue()
