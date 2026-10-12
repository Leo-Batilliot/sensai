"""Confirmation policy (A3): allow, confirm or deny each tool call, before it runs.

Deterministic and never asked to the model: the decision only uses the configuration,
the annotations declared by the tool's server (believed only for trusted servers) and,
for SQL, a classification computed by SQLite itself (sql_toolbox.guard). Every matching
rule gives its reason; the strictest action wins: deny, then confirm, then allow.
Annotations are hints: what nothing marks as safe needs a confirmation.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sensai.core.config import HitlConfig
from sensai.mcp_servers.sql_toolbox.guard import FORBIDDEN, READ, WRITE, Classification, SqlGuard
from sensai.tools.base import ToolInfo

logger = logging.getLogger(__name__)

Action = Literal["allow", "confirm", "deny"]
SqlClassifier = Callable[[str], "Classification | None"]

_STRICTNESS: dict[str, int] = {"allow": 0, "confirm": 1, "deny": 2}
# Our read tool, which must never receive a write
READ_TOOL = ("sql_toolbox", "run_select")


@dataclass(frozen=True)
class PolicyDecision:
    action: Action
    reasons: list[str] = field(default_factory=list)  # French, shown in the confirmation
    code: str | None = None  # error code of a deny: TOOL_DENIED or SQL_FORBIDDEN


def evaluate(
    tool: ToolInfo,
    arguments: dict[str, Any],
    config: HitlConfig,
    classify: SqlClassifier | None = None,
) -> PolicyDecision:
    """Decide for one call. `arguments` only matter through the SQL classification."""
    rules: list[tuple[Action, str, str | None]] = []
    hints = tool.annotations

    # 1. The administrator's lists
    if tool.name in config.deny:
        rules.append(("deny", "outil interdit par la configuration ([hitl] deny)", "TOOL_DENIED"))
    if tool.name in config.always_confirm:
        rules.append(("confirm", "confirmation imposée par la configuration", None))

    # 2. What the SQL would really do, whatever the tool claims
    classification = _classify_sql(arguments, classify)
    if classification is not None:
        if (tool.server, tool.original_name) == READ_TOOL and classification.kind != READ:
            rules.append(
                (
                    "deny",
                    "run_select n'accepte que des lectures ; une écriture passe par "
                    "preview_write puis execute_write",
                    "SQL_FORBIDDEN",
                )
            )
        elif classification.kind == FORBIDDEN:
            rules.append(("deny", f"requête interdite : {classification.reason}", "SQL_FORBIDDEN"))
        elif classification.kind == WRITE:
            operations = ", ".join(classification.operations)
            rules.append(("confirm", f"la requête modifie des données ({operations})", None))

    # 3. Annotations
    if hints.destructive is True:
        rules.append(("confirm", "outil déclaré destructif", None))
    if not tool.trusted and hints.read_only is not True:
        rules.append(("confirm", "outil d'un serveur tiers non déclaré en lecture seule", None))
    if tool.trusted and hints.read_only is False and hints.destructive is False:
        rules.append(("allow", "outil de confiance qui ajoute sans rien détruire", None))
    if hints.read_only is True:
        rules.append(("allow", "outil en lecture seule", None))

    # 4. Fail-safe: nothing says the tool is harmless
    if not any(action == "allow" for action, _, _ in rules):
        rules.append(("confirm", "aucune garantie que l'outil soit sans effet", None))

    strictest = max(_STRICTNESS[action] for action, _, _ in rules)
    chosen = [
        (action, reason, code) for action, reason, code in rules if _STRICTNESS[action] == strictest
    ]
    action = chosen[0][0]
    code = next((code for _, _, code in chosen if code), None)
    return PolicyDecision(action, [reason for _, reason, _ in chosen], code)


def requires_confirmation(tool: ToolInfo, config: HitlConfig) -> bool:
    """What the interface shows next to a tool: the decision without arguments."""
    return evaluate(tool, {}, config).action == "confirm"


def _classify_sql(
    arguments: dict[str, Any], classify: SqlClassifier | None
) -> Classification | None:
    sql = arguments.get("sql")
    if classify is None or not isinstance(sql, str) or not sql.strip():
        return None
    return classify(sql)


class DatabaseClassifier:
    """Classifies SQL on the business database, opened read-only on first use.

    Returns None when the database is missing or the statement does not even compile:
    the policy then relies on annotations, and the server's own guard reports the error
    to the model so that it can fix its query.
    """

    def __init__(self, db_path: str | Path, timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.timeout_ms = timeout_ms
        self._guard: SqlGuard | None = None

    def __call__(self, sql: str) -> Classification | None:
        if self._guard is None:
            if not self.db_path.is_file():
                return None
            self._guard = SqlGuard(self.db_path, self.timeout_ms)
        try:
            return self._guard.classify(sql)
        except sqlite3.Error as exc:
            logger.debug("SQL not classified (%s)", exc)
            return None

    def close(self) -> None:
        if self._guard is not None:
            self._guard.close()
            self._guard = None
