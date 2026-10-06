import subprocess
import sys

import pytest

from sensai import __version__
from sensai.__main__ import COMMANDS, HANDLERS, main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: sensai" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(set(COMMANDS) - set(HANDLERS)))
def test_unimplemented_command_fails_cleanly(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([command]) == 1
    assert "pas encore implémenté" in capsys.readouterr().err


def test_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "sensai", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
