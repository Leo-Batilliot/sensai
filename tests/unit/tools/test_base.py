import pytest

from sensai.tools.base import TOOL_NAME, ToolError, ToolResult


def test_failure_builds_a_consistent_result() -> None:
    result = ToolResult.failure("UNKNOWN_TOOL", "Outil inconnu : foo.")

    assert result.ok is False
    assert result.error == ToolError(code="UNKNOWN_TOOL", message="Outil inconnu : foo.")
    assert result.text == result.summary == "Outil inconnu : foo."


@pytest.mark.parametrize(
    ("ok", "error"),
    [(True, ToolError(code="TOOL_FAILED", message="boom")), (False, None)],
    ids=["ok-with-error", "failure-without-error"],
)
def test_ok_and_error_must_agree(ok: bool, error: ToolError | None) -> None:
    with pytest.raises(ValueError, match="if and only if"):
        ToolResult(ok=ok, text="x", summary="x", error=error)


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("sql_toolbox__run_select", True),
        ("memory_delete", True),
        ("nom avec espaces", False),
        ("", False),
        ("x" * 65, False),
    ],
)
def test_tool_names_follow_p17(name: str, valid: bool) -> None:
    assert bool(TOOL_NAME.match(name)) is valid
