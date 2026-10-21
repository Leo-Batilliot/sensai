# Conformité MCP — client SENSAI et serveur `sql_toolbox`

Ce document vérifie, point par point, que notre client MCP (`src/sensai/mcp/`) et notre serveur `sql_toolbox` (`src/sensai/mcp_servers/sql_toolbox/`) respectent le Model Context Protocol : JSON-RPC 2.0 sur stdio, révisions `2024-11-05` à `2025-11-25`. Chaque point est relié au test qui le prouve. Les tests marqués `external` demandent `uvx` (`make test-all`).

## Vérification avec l'outil officiel : MCP Inspector

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m sensai.mcp_servers.sql_toolbox --db data/business.db
```

| Capture | Ce qu'elle montre |
|---|---|
| [`server_connected.png`](../keynote/mcp-inspector/server_connected.png) | Connexion et poignée de main `initialize` réussies avec l'outil officiel |
| [`tools.png`](../keynote/mcp-inspector/tools.png) | `tools/list` : les 5 outils, leurs descriptions et leurs annotations |
| [`list_tables.png`](../keynote/mcp-inspector/list_tables.png) | Appel de `list_tables` : les 9 tables, en texte et en `structuredContent` |
| [`run_select.png`](../keynote/mcp-inspector/run_select.png) | `run_select` avec un `DELETE` : la garde SQL refuse, résultat `isError: true` et code `SQL_FORBIDDEN`, et l'échange complet dans le journal du protocole (`initialize`, `notifications/initialized`, `tools/list`, `tools/call`) |

## Checklist

### Cycle de vie et négociation de version
| # | Exigence | État | Preuve |
|---|---|:---:|---|
| 1 | Le serveur garde la version demandée s'il la connaît, et répond avec la sienne sinon | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_a_supported_version_is_kept`, `::test_an_unknown_version_gets_ours` |
| 2 | Le client accepte toute révision de sa liste (`2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`) et refuse proprement une version inconnue | ✅ | `tests/integration/test_mcp_client.py::test_older_protocol_revisions_still_work`, `::test_unsupported_version_is_refused`, `tests/unit/mcp/test_client.py::test_unsupported_protocol_version_closes_the_server` |
| 3 | Séquence `initialize` → `notifications/initialized` → requêtes | ✅ | `tests/unit/mcp/test_client.py::test_start_runs_the_handshake` |
| 4 | `initialize` déclare les capacités réelles (`tools`, liste fixe) ; `ping` répond | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_an_unknown_version_gets_ours`, `tests/unit/mcp_servers/test_server.py::test_ping_and_unknown_method` |
| 5 | Le client répond au `ping` du serveur et refuse les requêtes qu'il ne gère pas (`-32601`) | ✅ | `tests/unit/mcp/test_client.py::test_server_requests_get_an_answer`, `tests/integration/test_mcp_client.py::test_server_ping_is_answered` |

### Outils
| # | Exigence | État | Preuve |
|---|---|:---:|---|
| 6 | `tools/list` accepte un `cursor` ; le client suit `nextCursor` sur plusieurs pages | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_tools_list_accepts_a_cursor_and_has_one_page`, `tests/integration/test_mcp_client.py::test_handshake_and_paginated_tools` |
| 7 | Chaque outil déclare un `inputSchema` et un `outputSchema` valides (JSON Schema 2020-12, racine `object`) et ses quatre annotations | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_every_tool_declares_an_object_output_schema`, `tests/unit/mcp_servers/test_server.py::test_tools_are_declared_with_explicit_annotations` |
| 8 | Une erreur d'exécution est un résultat `isError: true`, pas une erreur JSON-RPC | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_a_tool_failure_is_a_result_not_a_protocol_error`, `tests/unit/mcp/test_client.py::test_tool_error_is_a_result_not_an_exception` |
| 9 | Avec un `outputSchema`, chaque `structuredContent` y est conforme, erreurs comprises | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_structured_content_conforms_to_the_output_schema` (succès et échec des 5 outils) |
| 10 | Le contenu texte reprend le résultat, pour les clients qui ignorent `structuredContent` | ✅ | `tests/unit/mcp_servers/test_server.py::test_results_carry_text_and_structured_content` |
| 11 | Outil inconnu ou arguments invalides → `-32602` | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_unknown_tool_is_invalid_params`, `tests/unit/mcp_servers/test_server.py::test_unknown_tool_and_invalid_params` |
| 12 | Un `_meta` dans les paramètres (`progressToken` d'Inspector) est accepté | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_meta_in_params_is_accepted` |

### JSON-RPC
| # | Exigence | État | Preuve |
|---|---|:---:|---|
| 13 | JSON illisible → `-32700` | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_protocol_errors` |
| 14 | Requête invalide → `-32600` : lot (tableau, retiré en `2025-06-18`), `jsonrpc` absent ou différent de `"2.0"`, `id` ni texte ni nombre, `method` qui n'est pas un texte | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_protocol_errors` |
| 15 | Méthode inconnue → `-32601` | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_protocol_errors` |
| 16 | Les notifications, connues ou non, ne reçoivent aucune réponse | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_notifications_and_answers_get_no_reply` |
| 17 | Une erreur interne coûte une réponse (`-32603`), jamais le serveur | ✅ | `tests/unit/mcp_servers/test_conformity.py::test_an_internal_bug_costs_one_answer_not_the_server` |

### Transport stdio
| # | Exigence | État | Preuve |
|---|---|:---:|---|
| 18 | Un message par ligne, sans saut de ligne à l'intérieur ; stdout réservé au protocole, journaux sur stderr | ✅ | `tests/unit/mcp/test_jsonrpc.py::test_encode_is_one_line_even_with_newlines_in_values`, `tests/unit/mcp_servers/test_server.py::test_serve_answers_line_by_line` |
| 19 | Le client tolère une ligne non JSON-RPC sur stdout et lit stderr en continu | ✅ | `tests/integration/test_mcp_client.py::test_banner_on_stdout_is_ignored` |
| 20 | Délai dépassé → `notifications/cancelled` ; arrêt sans processus orphelin | ✅ | `tests/unit/mcp/test_client.py::test_timeout_sends_a_cancellation`, `tests/integration/test_mcp_client.py::test_close_leaves_no_process` |
| 21 | Interopérabilité avec un serveur que nous n'avons pas écrit | ✅ | `tests/integration/test_mcp_client.py::test_mcp_server_time` (`external`) |
