import subprocess
import sys
from pathlib import Path

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


def test_prompts_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # US-A4-02: the registry and the differences between two versions, from the terminal
    monkeypatch.chdir(tmp_path)  # without the config.toml of the machine: the default version
    assert main(["prompts"]) == 0
    listed = capsys.readouterr().out
    assert "* system_sql v4" in listed  # the configured version is marked
    assert "c9f2747a9340" in listed  # start of its fingerprint

    assert main(["prompts", "show", "v1"]) == 0
    assert "$memory" in capsys.readouterr().out

    assert main(["prompts", "diff", "v1", "v1"]) == 0
    assert capsys.readouterr().out == ""  # a version has no difference with itself


@pytest.mark.parametrize("argv", [["prompts", "show"], ["prompts", "diff", "v1"]])
def test_prompts_commands_check_their_arguments(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert "usage : sensai prompts" in capsys.readouterr().err


def test_an_unknown_prompt_version_is_refused_before_starting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["prompts", "show", "v9"]) == 1
    assert "Version de prompt inconnue" in capsys.readouterr().err

    assert main(["eval", "--tag", "essai", "--prompt-version", "v9"]) == 1
    assert "Versions publiées : v1" in capsys.readouterr().err
