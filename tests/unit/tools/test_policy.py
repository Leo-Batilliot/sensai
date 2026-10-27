from pathlib import Path
from typing import Any

import pytest

from fakes import PERSONAS_DIR
from sensai.core.config import HitlConfig
from sensai.core.personas import load_personas
from sensai.data.schema import connect, create_schema
from sensai.mcp.client import ToolAnnotations
from sensai.mcp_servers.sql_toolbox.guard import FORBIDDEN, READ, WRITE, Classification
from sensai.tools.base import ToolInfo
from sensai.tools.policy import DatabaseClassifier, evaluate, requires_confirmation

READ_ONLY = ToolAnnotations(read_only=True, destructive=False)
ADDITIVE = ToolAnnotations(read_only=False, destructive=False)
DESTRUCTIVE = ToolAnnotations(read_only=False, destructive=True)
NONE = ToolAnnotations()


def tool(
    name: str = "sql_toolbox__run_select",
    *,
    server: str | None = "sql_toolbox",
    annotations: ToolAnnotations = READ_ONLY,
    trusted: bool = True,
) -> ToolInfo:
    original = name.split("__", 1)[1] if server else name
    return ToolInfo(
        name=name,
        server=server,
        original_name=original,
        description="",
        input_schema={"type": "object"},
        annotations=annotations,
        trusted=trusted,
    )


def classifier(kind: str, operations: tuple[str, ...] = ()) -> Any:
    def classify(sql: str) -> Classification:
        return Classification(
            kind, operations, (), "plusieurs instructions" if kind == FORBIDDEN else ""
        )

    return classify


SELECT = {"sql": "SELECT 1"}
CONFIG = HitlConfig()


# One case per rule, from the strongest to the weakest
@pytest.mark.parametrize(
    ("info", "arguments", "config", "classify", "action"),
    [
        # 1. [hitl] deny wins over a read-only tool
        (tool(), SELECT, HitlConfig(deny=("sql_toolbox__run_select",)), classifier(READ), "deny"),
        # 2. A write sent to run_select
        (tool(), {"sql": "DELETE FROM clients"}, CONFIG, classifier(WRITE, ("DELETE",)), "deny"),
        (tool(), {"sql": "ATTACH 'x' AS x"}, CONFIG, classifier(FORBIDDEN), "deny"),
        # 3. [hitl] always_confirm
        (
            tool(),
            SELECT,
            HitlConfig(always_confirm=("sql_toolbox__run_select",)),
            classifier(READ),
            "confirm",
        ),
        # 4. SQL classified as a write, on a tool made for writes
        (
            tool("sql_toolbox__execute_write", annotations=ADDITIVE),
            {"sql": "UPDATE clients SET status = 'x'"},
            CONFIG,
            classifier(WRITE, ("UPDATE",)),
            "confirm",
        ),
        # 4 bis. Forbidden SQL on any tool
        (
            tool("sql_toolbox__execute_write", annotations=ADDITIVE),
            SELECT,
            CONFIG,
            classifier(FORBIDDEN),
            "deny",
        ),
        # 5. destructiveHint
        (tool("memory_delete", server=None, annotations=DESTRUCTIVE), {}, CONFIG, None, "confirm"),
        # 6. Third-party server without an explicit readOnlyHint: true
        (
            tool("fs__write_file", server="fs", annotations=ADDITIVE, trusted=False),
            {},
            CONFIG,
            None,
            "confirm",
        ),
        (
            tool("fs__write_file", server="fs", annotations=NONE, trusted=False),
            {},
            CONFIG,
            None,
            "confirm",
        ),
        # 7. Trusted additive tool
        (tool("glossary_define", server=None, annotations=ADDITIVE), {}, CONFIG, None, "allow"),
        # 8. readOnlyHint: true, trusted or not
        (tool(), SELECT, CONFIG, classifier(READ), "allow"),
        (tool("time__get_current_time", server="time", trusted=False), {}, CONFIG, None, "allow"),
        # 9. Nothing says it is harmless, even on a trusted server
        (tool("sql_toolbox__mystery", annotations=NONE), {}, CONFIG, None, "confirm"),
        (
            tool("memory_x", server=None, annotations=ToolAnnotations(read_only=False)),
            {},
            CONFIG,
            None,
            "confirm",
        ),
    ],
)
def test_rules(
    info: ToolInfo, arguments: dict[str, Any], config: HitlConfig, classify: Any, action: str
) -> None:
    decision = evaluate(info, arguments, config, classify)
    assert decision.action == action
    assert decision.reasons


def test_deny_codes() -> None:
    sql = evaluate(tool(), {"sql": "DELETE FROM x"}, CONFIG, classifier(WRITE, ("DELETE",)))
    assert sql.code == "SQL_FORBIDDEN"
    assert "preview_write" in sql.reasons[0]
    config = evaluate(tool(), SELECT, HitlConfig(deny=("sql_toolbox__run_select",)), None)
    assert config.code == "TOOL_DENIED"
    assert evaluate(tool(), SELECT, CONFIG, None).code is None


def test_strictest_rule_wins() -> None:
    # Read-only and destructive at once: confirm beats allow
    contradictory = tool(annotations=ToolAnnotations(read_only=True, destructive=True))
    assert evaluate(contradictory, {}, CONFIG).action == "confirm"
    # always_confirm and deny together: deny
    both = HitlConfig(
        deny=("sql_toolbox__run_select",), always_confirm=("sql_toolbox__run_select",)
    )
    assert evaluate(tool(), SELECT, both).action == "deny"


def test_arguments_from_the_model_are_never_trusted() -> None:
    # A made-up argument cannot lower the decision
    info = tool("fs__delete", server="fs", annotations=NONE, trusted=False)
    assert evaluate(info, {"dangerous": False, "read_only": True}, CONFIG).action == "confirm"
    # The SQL counts through its real classification, not through what the model says
    lying = {"sql": "DELETE FROM clients", "purpose": "simple lecture"}
    assert evaluate(tool(), lying, CONFIG, classifier(WRITE, ("DELETE",))).action == "deny"


def test_sql_rules_need_a_classifier() -> None:
    # Without a classification (no database, SQL that does not compile), the annotations
    # decide; for run_select the server's own guard still refuses writes
    assert evaluate(tool(), {"sql": "DELETE FROM x"}, CONFIG, None).action == "allow"
    assert evaluate(tool(), {"sql": "   "}, CONFIG, classifier(WRITE)).action == "allow"


def test_requires_confirmation() -> None:
    assert not requires_confirmation(tool(), CONFIG)
    assert requires_confirmation(
        tool("memory_delete", server=None, annotations=DESTRUCTIVE), CONFIG
    )


def test_database_classifier(tmp_path: Path) -> None:
    missing = DatabaseClassifier(tmp_path / "missing.db")
    assert missing("SELECT 1") is None

    path = tmp_path / "business.db"
    conn = connect(path)
    create_schema(conn)
    conn.close()
    classify = DatabaseClassifier(path)
    try:
        read = classify("SELECT name FROM clients")
        write = classify("DELETE FROM clients")
        assert read is not None and read.kind == READ
        assert write is not None and write.kind == WRITE
        assert classify("SELECT nope FROM nowhere") is None  # does not compile
    finally:
        classify.close()
        classify.close()  # closing twice is harmless


def test_a_trusted_preview_needs_no_confirmation() -> None:
    preview = tool("sql_toolbox__preview_write")  # trusted, readOnlyHint: true
    write = classifier(WRITE, ("DELETE",))
    assert evaluate(preview, {"sql": "DELETE FROM clients"}, CONFIG, write).action == "allow"
    # The same claim from a third-party server is not believed
    untrusted = tool("other__preview_write", server="other", trusted=False)
    assert evaluate(untrusted, {"sql": "DELETE FROM clients"}, CONFIG, write).action == "confirm"
    # And the real write still asks
    execute = tool("sql_toolbox__execute_write", annotations=DESTRUCTIVE)
    assert evaluate(execute, {"sql": "DELETE FROM clients"}, CONFIG, write).action == "confirm"


# --- agent profiles (A5) ----------------------------------------------------------------

PERSONAS = load_personas(PERSONAS_DIR)
EXECUTE = tool("sql_toolbox__execute_write", annotations=DESTRUCTIVE)
DELETE = {"sql": "DELETE FROM clients WHERE id = 7"}


def test_tool_outside_the_persona_is_denied() -> None:
    decision = evaluate(
        EXECUTE, DELETE, CONFIG, classifier(WRITE, ("DELETE",)), persona=PERSONAS["analyste"]
    )
    assert (decision.action, decision.code) == ("deny", "TOOL_DENIED")
    assert "outil hors du profil « Contrôle de gestion »" in decision.reasons
    # A tool of the profile is judged by the usual rules
    assert evaluate(tool(), SELECT, CONFIG, persona=PERSONAS["analyste"]).action == "allow"


def test_a_persona_never_removes_a_confirmation() -> None:
    write = classifier(WRITE, ("DELETE",))
    adv = PERSONAS["adv"]
    assert adv.allows(EXECUTE.name)
    assert evaluate(EXECUTE, DELETE, CONFIG, write, persona=adv).action == "confirm"
    # Nor allows a tool that [hitl] denies
    denied = HitlConfig(deny=(EXECUTE.name,))
    assert evaluate(EXECUTE, DELETE, denied, write, persona=adv).action == "deny"
