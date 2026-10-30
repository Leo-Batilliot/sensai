# Contrat — Pannes, codes d'erreur et messages (CORE-11)

| | |
|---|---|
| **Propriétaire** | P1 |
| **Fait foi pour les codes** | [`events.md`](events.md) §5 (listes fermées) ; erreurs du protocole WebSocket : [`websocket.md`](websocket.md) §7 |
| **Ce document** | Ce que voit l'utilisateur pour chaque panne connue, le message exact, et le test qui le prouve |

## 1. Principes

1. **Aucune panne connue ne fait planter** la CLI ou le serveur web : elle devient un message, et la session continue quand c'est possible.
2. **Chaque message est en français et dit quoi faire** (« lancez `ollama serve` », « choisissez un modèle qui gère les outils »…).
3. **Deux destinataires** :
   - l'utilisateur lit les `error` (fin de tour ou hors tour), les avertissements de démarrage et la timeline ;
   - le modèle lit les échecs d'outils (`tool_result.error`) : leur message lui dit s'il doit corriger, réessayer ou expliquer.
4. **Les journaux ne contiennent jamais de données métier** : ni SQL, ni lignes, ni réponses. Seulement les noms d'outils, les identifiants et les durées.
5. **Un nouveau code** se déclare d'abord dans `events.md` §5. `tests/unit/test_errors.py::test_codes_are_in_the_closed_list_of_the_contract` vérifie que chaque erreur du moteur y figure.

## 2. Les pannes connues

| # | Panne | Détection | Ce que voit l'utilisateur | Code | Testé par |
|---|---|---|---|---|---|
| 1 | **Ollama arrêté au démarrage** | `httpx.ConnectError` | CLI : « Ollama injoignable sur http://localhost:11434 — lancez `ollama serve`. », la session reste ouverte et le message suivant réessaie. Web : `GET /api/health` → `"ollama": "down"`, liste des modèles vide | `OLLAMA_UNAVAILABLE` | `tests/unit/cli/test_app.py::test_startup_with_ollama_down_keeps_the_session_open`, `tests/unit/web/test_real_engine.py::test_ollama_down_gives_no_model_instead_of_an_error` |
| 2 | **Ollama arrêté en cours de session** | idem, pendant un tour | `error` récupérable en fin de tour ; l'historique est intact et la séance repart quand Ollama revient | `OLLAMA_UNAVAILABLE` | `tests/unit/core/test_engine.py::test_ollama_down_ends_the_turn_with_a_recoverable_error`, `::test_ollama_down_mid_session_keeps_the_history`, `tests/unit/cli/test_app.py::test_session_survives_ollama_being_down` |
| 3 | **Modèle absent** | HTTP 404 d'Ollama | « Le modèle « X » n'est pas installé. Lancez : `ollama pull X`. » ; au démarrage de la CLI, liste des modèles installés ; `/model X` garde le modèle courant | `MODEL_NOT_FOUND` | `tests/unit/test_ollama_client.py::test_missing_model_raises_model_not_found_everywhere`, `tests/unit/cli/test_app.py::test_startup_with_a_missing_model_lists_the_installed_ones`, `::test_model_command_with_an_unknown_model_keeps_the_current_one` |
| 4 | **Modèle sans outils** | `/api/show` (`capabilities`) au démarrage et à `/model` ; HTTP 400 « *does not support tools* » pendant un tour | Avertissement : « Le modèle X ne gère pas les outils : les questions sur les données ne fonctionneront pas avec lui. » Pendant un tour : « Le modèle « X » ne gère pas les outils : il ne peut pas interroger les données. Choisissez un modèle qui les gère, par exemple qwen3:8b. » | `MODEL_NO_TOOLS` | `tests/unit/cli/test_app.py::test_startup_warns_when_the_model_has_no_tools`, `tests/unit/test_ollama_client.py::test_a_model_without_tools_raises_model_no_tools`, `tests/unit/core/test_engine.py::test_a_model_without_tools_gets_a_clear_message` |
| 5 | **Serveur MCP absent au démarrage** | commande introuvable, pas de réponse à `initialize` | Le serveur est marqué indisponible avec sa raison, les autres fonctionnent. CLI : « Serveur MCP time : indisponible (la commande `uvx` est introuvable…) » au démarrage et dans `/tools` ; web : `session_ready.servers` | — (ses outils disparaissent de la liste) | `tests/unit/core/test_app_factory.py::test_a_server_that_cannot_start_does_not_stop_the_others`, `tests/unit/cli/test_app.py::test_a_server_down_at_startup_is_visible_in_tools` |
| 6 | **Serveur MCP qui plante pendant un appel** | fin du processus, sortie fermée | Un redémarrage automatique, une fois par session (`McpManager`). Sinon, le modèle lit « Serveur MCP « X » indisponible : … » et l'explique ; la timeline affiche l'échec | `MCP_SERVER_UNAVAILABLE` (outil) | `tests/integration/test_mcp_manager.py::test_a_crashed_server_is_restarted_once`, `tests/unit/cli/test_app.py::test_tool_steps_are_shown_as_a_timeline` |
| 7 | **Requête SQL trop longue** | `[database] query_timeout_ms` (5 s), interruption par SQLite | Le modèle lit « Requête interrompue : plus de 5000 ms. Ajoutez des filtres ou simplifiez-la. » (en écriture : « … Rien n'a été modifié. ») | `TOOL_TIMEOUT` (outil) | `tests/unit/mcp_servers/test_guard.py::test_long_queries_are_interrupted`, `tests/unit/mcp_servers/test_server.py::test_run_select_errors`, `tests/unit/mcp_servers/test_preview.py::test_long_writes_are_interrupted` |
| 8 | **Échec du juge** | exception, JSON invalide après réparation | Badge « Vérification indisponible » ; le contrôle numérique (sans modèle) est quand même donné | — (`judge_report.badge: "unavailable"`) | `tests/unit/core/test_engine_judge.py::test_a_failing_judge_still_reports` |
| 9 | **Délai de confirmation dépassé** | `[hitl] confirmation_timeout_s` | Refus, rien n'est exécuté ; le modèle lit « Pas de confirmation dans le délai : action annulée. Ne la retente pas ; explique-le dans ta réponse. » ; journal `WARNING` avec l'outil et l'action, sans le SQL | `ACTION_REJECTED` (outil) | `tests/unit/core/test_engine_hitl.py::test_no_answer_in_time_is_a_refusal`, `tests/unit/cli/test_confirm.py::test_timeout_is_reported` |
| 10 | **Configuration invalide** | validation pydantic, TOML illisible, fichier absent | Erreur au démarrage qui cite le fichier et la clé (« Configuration invalide (config.toml) : - ollama.modle : … »), code de sortie 1. Même règle pour un profil de `personas/` (fichier et champ cités) | — (démarrage) | `tests/unit/test_config.py::test_unknown_key_is_rejected`, `::test_invalid_env_value_is_reported`, `::test_invalid_toml_names_the_file`, `tests/unit/cli/test_app.py::test_invalid_config_stops_with_exit_code_1`, `tests/unit/core/test_personas.py::test_invalid_file_is_reported` |
| 11 | **Déconnexion WebSocket pendant un tour** | fermeture du socket | Confirmation en attente refusée (`reason: "disconnected"`), question en attente annulée, tour arrêté ; à la reconnexion, l'historique dit comment le tour s'est terminé | `ACTION_REJECTED` (outil) | `tests/unit/web/test_ws.py::test_disconnection_refuses_a_pending_confirmation`, `tests/unit/web/test_real_engine.py::test_disconnection_during_a_confirmation_refuses_it`, `::test_disconnection_during_a_question_cancels_it` |

## 3. Messages des erreurs de fin de tour et hors tour (`error`)

Levées par le client Ollama ou le moteur (`sensai.core.errors`), affichées telles quelles par la CLI et le web.

| Code | Message | `recoverable` |
|---|---|:---:|
| `OLLAMA_UNAVAILABLE` | « Ollama injoignable sur {hôte} — lancez `ollama serve`. » | true |
| `OLLAMA_TIMEOUT` | « Ollama n'a pas répondu en {n} s. Réessayez : l'historique est conservé. » | true |
| `MODEL_NOT_FOUND` | « Le modèle « {m} » n'est pas installé. Lancez : `ollama pull {m}`. » | true |
| `MODEL_NO_TOOLS` | « Le modèle « {m} » ne gère pas les outils : il ne peut pas interroger les données. Choisissez un modèle qui les gère, par exemple qwen3:8b. » | true |
| `STREAM_INTERRUPTED` | « La réponse a été interrompue ({raison}). » ; la CLI ajoute « Elle n'est pas conservée dans l'historique. » Raison du plafond `[ollama] num_predict` : « plus de {n} tokens générés sans conclure ; reformulez ou précisez la question » | true |
| `OLLAMA_ERROR` | « Erreur Ollama (HTTP {statut}) : {détail} », détail transmis tel quel par Ollama | true |
| `MAX_ITERATIONS` | « Je n'ai pas trouvé de réponse en {n} étapes d'outils. Reformulez ou précisez la question. » | true |
| `INTERNAL` | « Erreur inattendue du moteur (le détail est dans les journaux). Recommencez la conversation avec /reset. » | false |
| `BUSY` | « Une réponse est déjà en cours. Attendez la fin ou interrompez-la. » | true |
| `PERSONA_NOT_FOUND` | « Le profil « {id} » n'existe pas. Profils disponibles : {liste}. » | true |

Les autres codes hors tour (`INVALID_MESSAGE`, `UNKNOWN_ACTION`, `INVALID_CONFIG`, `UI_ACTION_FORBIDDEN`, `UNKNOWN_QUESTION`, `PROMPT_VERSION_NOT_FOUND`, `SCHEDULE_NOT_FOUND`) sont ceux de la couche web : messages dans [`websocket.md`](websocket.md) §7.

## 4. Messages des échecs d'outils (`tool_result.error`), lus par le modèle

| Code | Message | Ce que le modèle doit faire |
|---|---|---|
| `UNKNOWN_TOOL` | « Outil inconnu : {nom}. Outils disponibles : {outils du profil}. » | Choisir un outil de la liste |
| `DUPLICATE_CALL` | « Appel identique au précédent, non exécuté : utilise le résultat déjà obtenu. » | Utiliser le résultat |
| `INVALID_ARGUMENTS` | « Arguments invalides : {erreurs}. Corrige-les et rappelle l'outil. », puis « Arguments toujours invalides : … Ne rappelle plus cet outil ; explique le problème dans ta réponse. » | Une réparation, puis expliquer |
| `TOOL_DENIED` | « Action refusée par la politique de sécurité : {raisons}. » (`[hitl] deny`, outil hors du profil) | Expliquer le refus |
| `SQL_FORBIDDEN` | « Requête refusée par la garde SQL : {raison}. Ce n'est pas une erreur à corriger : ne retente pas cette requête… » | Ne pas réessayer |
| `SQL_ERROR` | « La requête a échoué : {erreur}. Corrige-la et relance l'outil (encore N essais). », puis « Limite de N correction(s) atteinte… » (CORE-09) | Corriger, au plus `max_sql_retries` fois |
| `ACTION_REJECTED` | « Action refusée par l'utilisateur. » ou « Pas de confirmation dans le délai : action annulée. », suivi de « Ne la retente pas ; explique-le dans ta réponse. » | Expliquer, sans réessayer |
| `QUESTION_LIMIT` | « Une seule question par tour : n'en pose pas d'autre, fais une hypothèse explicite et dis-la dans ta réponse. » | Faire une hypothèse |
| `MCP_SERVER_UNAVAILABLE` | « Serveur MCP « {nom} » indisponible : {raison} » | Expliquer, ou se passer de l'outil |
| `TOOL_TIMEOUT` | Requête SQL : « Requête interrompue : plus de {n} ms. Ajoutez des filtres ou simplifiez-la. » ; autre outil : délai MCP dépassé | Simplifier la requête |
| `TOOL_FAILED` | « L'outil {nom} a échoué : {détail} » | Expliquer |

## 5. Pannes qui ne produisent pas d'erreur

| Panne | Comportement |
|---|---|
| Résumé de l'historique en échec (M2) | Repli sur la troncature, journal `WARNING`, `context_compacted` dit « Résumé impossible : … » |
| Question sans réponse dans le délai (X4) | Le modèle fait une hypothèse explicite ; journal `INFO` |
| Base de démonstration absente au démarrage de la CLI | « Base de démonstration absente (data/business.db) : lancez `sensai seed`. Chat sans outils, sans mémoire et sans vérification. » ; `sensai web` refuse de démarrer et propose `--mock` |
| Aperçu d'impact indisponible | La confirmation est demandée sans chiffres (« Aperçu de l'impact indisponible ») |
