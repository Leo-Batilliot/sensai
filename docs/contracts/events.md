# Contrat — Événements du moteur

| | |
|---|---|
| **Tâche** | ALL-02 (atelier contrats), implémenté par CORE-05 (`src/sensai/core/events.py`) |
| **Propriétaire** | P1 — Core & Agent |
| **Consommateurs** | P4 (front, WebSocket, `MockEngine`), P3 (juge, mémoire, harness), P2 (aperçu d'impact HITL) |
| **Statut** | Brouillon — à valider par P2, P3 et P4 (point P-18 de `websocket.md`) |
| **Version** | **v1.1** (21/10) : cinq types (§3.13 à §3.17), ordre et invariants (§4.1, §4.3), quatre codes (§5.1), repris de [`v1.1-extensions.md`](v1.1-extensions.md) §2 (ALL-15). Ajouts uniquement : rien de la v1 ne change de sens |
| **Fait foi** | Ce document. Les schémas JSON (`schemas/events/*.json`) seront générés à partir de lui dans CORE-05 |
| **Spécification** | PROJECT.md §7.1 (événements), §5.3 et §5.4 (déroulé d'un tour) |
| **Contrats liés** | [`websocket.md`](websocket.md) (P4), [`memory.md`](memory.md) (P3), `judge.md` (P3), `tools.md` (P1, P2) |

---

## 1. Principes

1. **Le moteur ne fait qu'émettre des événements.** `Engine.run_turn()` et `Engine.run_ui_action()` sont des générateurs asynchrones d'événements. La CLI et le serveur web les consomment de la même façon, sans logique métier (PROJECT §5.1).
2. **Le WebSocket les transmet tels quels**, sans enveloppe (`websocket.md` §5.1). Ce document est donc aussi la définition de ce que reçoit le front.
3. **Format** : un objet JSON par événement, UTF-8, clés en `snake_case`.
4. **Tous les champs sont toujours présents.** Un champ sans valeur vaut `null` (ou `[]` pour une liste), il n'est jamais omis. Les types TypeScript du front et les schémas JSON restent ainsi simples, sans champ facultatif.
5. **Identifiants opaques** (`session_id`, `turn_id`, `call_id`, `action_id`) : des chaînes que personne ne découpe pour en tirer un sens. `session_id` est généré par le client web (UUID v4, `websocket.md` §2.1) ou par la CLI ; `turn_id`, `call_id` et `action_id` par le moteur (UUID4). Les exemples utilisent `t-1`, `k-2`… pour la lisibilité.
6. **Textes destinés à l'utilisateur en français** : `summary`, `message`, `label`, `reason` des éléments protégés.
7. **Un type inconnu est ignoré** (v1.1). Un consommateur qui reçoit un événement dont il ne connaît pas le type le laisse de côté, sans erreur : c'est ce qui permet d'ajouter des types sans casser les clients existants.

## 2. Champs communs

| Champ | Type | Contenu |
|---|---|---|
| `type` | chaîne | Type de l'événement (§3) |
| `session_id` | chaîne | Session (une conversation, un historique) |
| `turn_id` | chaîne ou `null` | Tour auquel l'événement appartient. `null` **uniquement** pour un `error` émis hors tour |
| `ts` | nombre | Horodatage d'émission, secondes depuis l'epoch (flottant) |

## 3. Catalogue

| Type | Champs spécifiques | Émis quand |
|---|---|---|
| `turn_started` | `origin`, `user_message` | Début de tour |
| `context_injected` | `refs` | Mémoire fournie au modèle pour ce tour (s'il y en a) |
| `context_compacted` | `turns`, `tokens_before`, `tokens_after`, `summary` | Les plus vieux tours viennent d'être résumés (v1.1, M2) |
| `examples_injected` | `examples` | Exemples question → SQL ajoutés au prompt du tour (v1.1, A7) |
| `token` | `delta` | Fragment de la réponse |
| `thinking` | `delta` | Fragment de raisonnement (modèles « thinking ») |
| `tool_call` | `call_id`, `server`, `tool`, `arguments` | Le modèle demande un outil |
| `sql_generated` | `call_id`, `sql`, `operation` | Un appel d'outil porte du SQL classé par la garde |
| `confirmation_required` | `action_id`, `call_id`, `tool`, `sql`, `impact`, `expires_in_s` | Pause HITL |
| `confirmation_resolved` | `action_id`, `decision`, `reason`, `comment` | Décision reçue, délai dépassé ou pause interrompue |
| `question_asked` | `question_id`, `call_id`, `question`, `reason`, `options`, `allow_free_text`, `expires_in_s` | Le modèle a appelé `ask_user` : le tour est en pause (v1.1, X4) |
| `question_answered` | `question_id`, `status`, `option_id`, `text` | Réponse reçue, délai dépassé ou pause interrompue (v1.1, X4) |
| `tool_result` | `call_id`, `ok`, `summary`, `data`, `error`, `duration_ms` | Outil terminé (ou refusé) |
| `memory_updated` | `op`, `kind`, `ref`, `summary` | Écriture effective en mémoire |
| `document_updated` | `doc_id`, `revision`, `section_id`, `op`, `summary` | Une section de la note du comité a été modifiée (v1.1, M4) |
| `message_done` | `content`, `interrupted`, `usage`, `duration_ms` | Fin de la réponse |
| `judge_started` | — | Début de la vérification |
| `judge_report` | `badge`, `scores`, `numeric_check`, `report`, `duration_ms` | Vérification terminée |
| `error` | `code`, `message`, `recoverable` | Erreur |

### 3.1 `turn_started`

| Champ | Type | Contenu |
|---|---|---|
| `origin` | `"user"` ou `"ui"` | `"user"` : message de l'utilisateur (`run_turn`). `"ui"` : action lancée depuis l'interface (`run_ui_action`, `websocket.md` §4.2) |
| `user_message` | chaîne ou `null` | Le message, ou `null` si et seulement si `origin` vaut `"ui"` |

```json
{"type": "turn_started", "session_id": "s-1", "turn_id": "t-1", "ts": 1760433000.0,
 "origin": "user", "user_message": "Quels sont nos 5 clients les plus rentables ce trimestre ?"}
```

### 3.2 `context_injected`

Émis juste après `turn_started`, **seulement si** MEM-04 a sélectionné des entrées. Il dit ce qui a été **fourni** au modèle, pas ce qu'il a appliqué : le libellé d'interface est « Contexte mémoire fourni ». Le harness s'en sert pour l'ablation avec ou sans mémoire.

| Champ | Type | Contenu |
|---|---|---|
| `refs` | liste non vide de `{kind, ref, label}` | `kind` et `ref` comme dans `memory.md` §1 ; `label` : le terme pour le glossaire, le nom pour une entité, le début du contenu pour un fait |

```json
{"type": "context_injected", "session_id": "s-1", "turn_id": "t-1", "ts": 1760433000.1,
 "refs": [{"kind": "glossary", "ref": "client rentable", "label": "client rentable"}]}
```

### 3.3 `token` et `thinking`

| Champ | Type | Contenu |
|---|---|---|
| `delta` | chaîne non vide | Fragment de texte, à concaténer dans l'ordre d'arrivée |

`thinking` porte le raisonnement des modèles qui l'exposent : l'interface l'affiche replié, et il n'entre jamais dans `message_done.content`.

### 3.4 `tool_call`

| Champ | Type | Contenu |
|---|---|---|
| `call_id` | chaîne | Identifiant de l'appel, repris par `sql_generated`, `confirmation_required` et `tool_result` |
| `server` | chaîne ou `null` | Serveur MCP (`"sql_toolbox"`, `"time"`…), `null` pour un outil natif (mémoire) |
| `tool` | chaîne | Nom de l'outil tel que le modèle le voit : **toujours préfixé par le serveur** (`sql_toolbox__run_select`), sans préfixe pour les outils natifs (`memory_delete`). Caractères `[A-Za-z0-9_-]`, 64 au maximum |
| `arguments` | objet | Arguments tels que proposés par le modèle (ou par l'interface pour `origin: "ui"`). S'ils ne respectent pas l'`inputSchema`, le `tool_result` qui suit est en échec avec `INVALID_ARGUMENTS` (T5) |

### 3.5 `sql_generated`

Émis entre `tool_call` et `tool_result` pour tout appel qui porte du SQL (`run_select`, `preview_write`, `execute_write`).

| Champ | Type | Contenu |
|---|---|---|
| `call_id` | chaîne | Appel concerné |
| `sql` | chaîne | Requête exacte qui sera exécutée |
| `operation` | `"SELECT"`, `"INSERT"`, `"UPDATE"`, `"DELETE"` ou `"OTHER"` | Classification de la **garde SQL** (SEC-01), jamais du modèle |

### 3.6 `confirmation_required`

La politique HITL (P2) demande une confirmation. Le moteur attend la décision du `ConfirmationBroker` de l'adaptateur (CLI ou web).

| Champ | Type | Contenu |
|---|---|---|
| `action_id` | chaîne | Identifiant de la demande, repris par `confirm` (WebSocket) et `confirmation_resolved` |
| `call_id` | chaîne | Appel d'outil mis en pause : relie la modale à l'étape de la timeline |
| `tool` | chaîne | Nom de l'outil (comme `tool_call.tool`) |
| `sql` | chaîne ou `null` | SQL exact pour un outil SQL, `null` sinon |
| `impact` | objet | Aperçu calculé en transaction annulée (`tools.md`, P2), voir ci-dessous |
| `expires_in_s` | nombre | Délai avant refus automatique (`[hitl] confirmation_timeout_s`). Le front décompte à partir de la réception |

`impact = {operation, rows_affected, per_table, sample, blocked}` :

| Champ | Type | Contenu |
|---|---|---|
| `operation` | comme `sql_generated.operation` | `"DELETE"` aussi pour `memory_delete` |
| `rows_affected` | entier | Lignes visées par l'instruction |
| `per_table` | objet `{table: entier}` | Différences signées de `COUNT(*)`, cascades incluses (négatif = lignes supprimées). Nulles pour un `UPDATE` : afficher alors `rows_affected` comme « N lignes modifiées » |
| `sample` | liste d'objets | Jusqu'à 5 lignes visées |
| `blocked` | liste de `{reason, count}` | Contraintes qui empêchent l'instruction. Avec `ON DELETE RESTRICT`, SQLite rejette **toute** l'instruction : `rows_affected` vaut alors 0 et `per_table` `{}`. `count` est un entier ou `null` (`null` en v1, exact avec HITL-03) |

```json
{"type": "confirmation_required", "session_id": "s-1", "turn_id": "t-4", "ts": 1760433102.1,
 "action_id": "a-3", "call_id": "k-7", "tool": "sql_toolbox__execute_write",
 "sql": "DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM invoices)",
 "impact": {"operation": "DELETE", "rows_affected": 27,
            "per_table": {"clients": -27, "contacts": -41},
            "sample": [{"id": 12, "name": "Atelier Morel"}],
            "blocked": []},
 "expires_in_s": 120}
```

### 3.7 `confirmation_resolved`

| Champ | Type | Contenu |
|---|---|---|
| `action_id` | chaîne | Demande résolue |
| `decision` | `"approve"`, `"reject"` ou `"timeout"` | Décision appliquée |
| `reason` | `"user"`, `"cancelled"`, `"disconnected"` ou `"timeout"` | **Obligatoire**. Origine de la décision (voir les combinaisons ci-dessous) |
| `comment` | chaîne ou `null` | Commentaire saisi avec la décision (`confirm.comment`), journalisé |

| `decision` | `reason` possibles |
|---|---|
| `approve` | `user` |
| `reject` | `user` (bouton Refuser), `cancelled` (`cancel` ou `reset`), `disconnected` (déconnexion du WebSocket) |
| `timeout` | `timeout` |

Après un `reject` ou un `timeout`, l'outil n'est pas exécuté : le `tool_result` qui suit est en échec avec `ACTION_REJECTED`, et le modèle doit en tenir compte sans retenter la même action (PROJECT §6.5).

### 3.8 `tool_result`

| Champ | Type | Contenu |
|---|---|---|
| `call_id` | chaîne | Appel terminé |
| `ok` | booléen | `true` si l'outil a réussi |
| `summary` | chaîne | Résumé court en français pour la timeline (« 5 lignes », « Fait n°17 supprimé ») |
| `data` | objet ou `null` | Résultat structuré (`structuredContent` MCP, ou `ToolResult.data` d'un outil natif). Pour `run_select` : `{columns, rows, row_count, truncated}` (`tools.md`, P2) |
| `error` | `{code, message}` ou `null` | `null` si `ok`, sinon le code (§5.2) et un message en français |
| `duration_ms` | entier | Durée de l'appel, 0 pour un appel refusé avant exécution |

`run_select` : `rows` est une liste de listes dans l'ordre de `columns` ; `row_count` est le nombre de lignes **présentes dans `rows`** (pas le total) ; `truncated: true` signifie qu'il y avait plus de `row_limit` lignes. Valeurs : nombre, chaîne ou `null`.

```json
{"type": "tool_result", "session_id": "s-1", "turn_id": "t-1", "ts": 1760433001.6,
 "call_id": "k-2", "ok": true, "summary": "5 lignes",
 "data": {"columns": ["name", "marge_eur", "delai_moyen_jours"],
          "rows": [["Brasserie Lemaire", 48712.4, 47.3]], "row_count": 1, "truncated": false},
 "error": null, "duration_ms": 380}
```

### 3.9 `memory_updated`

Émis après chaque écriture effective en mémoire (`op` ≠ `unchanged`, `memory.md` §3.2), par l'agent ou depuis l'interface.

| Champ | Type | Contenu |
|---|---|---|
| `op` | `"create"`, `"update"` ou `"delete"` | Opération |
| `kind` | `"glossary"`, `"entity"`, `"relation"` ou `"fact"` | Type d'entrée |
| `ref` | chaîne | Identifiant de l'entrée (le terme pour le glossaire, l'id en texte sinon). **Remplace `id`** de PROJECT §7.1 |
| `summary` | chaîne | Résumé en français pour le toast du panneau mémoire |

Une suppression en cascade (entité → relations et faits) émet un seul `memory_updated` pour l'entrée demandée ; le panneau recharge `GET /api/memory`.

### 3.10 `message_done`

| Champ | Type | Contenu |
|---|---|---|
| `content` | chaîne | Texte complet de la réponse : **toujours égal à la concaténation des `token`** du tour |
| `interrupted` | booléen | `true` après un `cancel` : `content` est alors le texte partiel, et `usage` vaut `null` |
| `usage` | objet ou `null` | `{prompt_tokens, completion_tokens, ctx_used, ctx_max}`, d'après le dernier fragment du **dernier appel au modèle** du tour (`prompt_eval_count`, `eval_count`). `ctx_used = prompt_tokens + completion_tokens` (occupation du contexte) ; `ctx_max = num_ctx` de la session. `null` quand Ollama n'a pas fourni ces compteurs : tour `origin: "ui"` (aucun appel au modèle) ou tour interrompu (seul le dernier fragment les porte, et il n'arrive jamais après un `cancel`). La jauge garde alors sa dernière valeur |
| `duration_ms` | entier | Durée totale du tour |

Un tour interrompu **n'entre pas dans l'historique** de conversation, et le juge ne tourne pas.

### 3.11 `judge_started` et `judge_report`

`judge_started` n'a pas de champ spécifique. Il est **toujours** suivi d'un `judge_report` pour le même `turn_id`.

`judge_report` est une **enveloppe calculée par le code** autour du rapport produit par le modèle. Le badge et les scores ne sont jamais demandés au modèle, et `judge_report.v1.json` (`additionalProperties: false`) ne décrit que ce que le modèle produit. Ce découpage tranche le point J-1 de `judge.md`.

| Champ | Type | Contenu |
|---|---|---|
| `badge` | `"verified"`, `"needs_review"`, `"contradicted"` ou `"unavailable"` | Calculé selon `judge.md` §2 (première règle qui s'applique). Libellés : Vérifiée, À vérifier, Contredite, Vérification indisponible |
| `scores` | `{faithfulness}` | `faithfulness` : nombre entre 0 et 1, ou `null` sans rapport ou sans affirmation (`judge.md` §2) |
| `numeric_check` | `{status, unmatched}` | Contrôle numérique déterministe (EVAL-04). `status` : `"ok"`, `"mismatch"` ou `"inconclusive"` (résultat tronqué) ; `unmatched` : nombres de la réponse, copiés mot pour mot, absents des données |
| `report` | objet ou `null` | Rapport conforme à `schemas/judge_report.v1.json`, ou `null` si le rapport est invalide après réparation, si le juge a dépassé son délai ou s'il est injoignable |
| `duration_ms` | entier | Durée de la vérification (affichée comme dernière étape de la timeline) |

- `report: null` n'implique pas `badge: "unavailable"` : le contrôle numérique ne dépend pas du modèle, et un nombre absent des données donne `"contradicted"` même sans rapport (`judge.md` §2, règle 1).
- Le front surligne `report.claims[].text` (affirmations `contradicted`) et `numeric_check.unmatched`, en cherchant le texte tel quel dans la réponse. Les nombres de `unmatched` sont surlignés comme contredits quand `status` vaut `"mismatch"`, et comme « à vérifier » quand il vaut `"inconclusive"` : ils peuvent se trouver dans les lignes coupées (`judge.md` §2).

```json
{"type": "judge_report", "session_id": "s-1", "turn_id": "t-3", "ts": 1760433090.4,
 "badge": "contradicted",
 "scores": {"faithfulness": 0.5},
 "numeric_check": {"status": "ok", "unmatched": []},
 "report": {
   "claims": [
     {"text": "Brasserie Lemaire arrive en tête avec 52 300 € de marge",
      "verdict": "contradicted", "evidence": "marge_eur = 48712.4 sur la ligne Brasserie Lemaire",
      "expected_value": "48 712,40 €"},
     {"text": "un délai moyen de paiement de 47,3 jours",
      "verdict": "supported", "evidence": "delai_moyen_jours = 47.3", "expected_value": null}],
   "relevance": 4,
   "sql_answers_question": true,
   "key_figures": [{"label": "Marge Brasserie Lemaire", "value": 48712.4, "unit": "€", "source_column": "marge_eur"}],
   "assumptions": ["Délai moyen calculé sur tout l'historique de paiement"],
   "followups": ["Comparer au trimestre précédent ?"]},
 "duration_ms": 2100}
```

Le juge ne tourne pas (ni `judge_started` ni `judge_report`) : sans `run_select` réussi dans le tour, pour un tour interrompu, pour un tour `origin: "ui"`, ou quand `judge_enabled` vaut `false` (`judge.md` §2).

### 3.12 `error`

| Champ | Type | Contenu |
|---|---|---|
| `code` | chaîne | Code de la liste fermée (§5.1) |
| `message` | chaîne | Message en français, qui dit quoi faire (« Ollama injoignable sur http://localhost:11434 — lancez `ollama serve`. ») |
| `recoverable` | booléen | `true` : la session reste utilisable, l'utilisateur peut réessayer. `false` : la session doit être rechargée |

- Un `error` qui porte un `turn_id` **termine toujours ce tour** (aucun `message_done` après), qu'il soit récupérable ou non. L'historique reste celui d'avant le tour.
- Un `error` hors tour a `turn_id: null` (erreurs de protocole du WebSocket, avertissement au démarrage).
- Une erreur d'outil n'est **pas** un `error` : c'est un `tool_result` en échec, renvoyé au modèle pour qu'il corrige (§5.2).

### 3.13 `context_compacted` (v1.1)

Émis quand le budget de tokens (`[context]`, M2) oblige à remplacer les plus vieux tours par un résumé glissant, avant le premier appel au modèle du tour (§4.1).

| Champ | Type | Contenu |
|---|---|---|
| `turns` | entier ≥ 1 | Tours remplacés par le résumé à ce tour-ci |
| `tokens_before` / `tokens_after` | entiers ≥ 0 | Estimation du prompt avant et après compression ; `tokens_after < tokens_before` |
| `summary` | chaîne (200 caractères au plus) | Début du résumé, pour la timeline |

```json
{"type": "context_compacted", "session_id": "s-1", "turn_id": "t-19", "ts": 1760434100.0,
 "turns": 6, "tokens_before": 6120, "tokens_after": 2480,
 "summary": "Client rentable = marge brute > 25 % ; top 5 du T4 : Brasserie Lemaire (18,4 k€)…"}
```

- Aussi émis quand les tours sont retirés **sans** résumé : stratégie `truncate`, résumé en échec (repli, jamais d'`error`), ou tours récents trop longs à eux seuls. `summary` dit alors pourquoi : « Troncature : 4 échange(s) retiré(s) sans résumé. », « Résumé impossible : … », « Échanges récents trop longs : … ».
- Les estimations sont en tokens (caractères / 3,5, recalées sur le `prompt_eval_count` d'Ollama) et comptent le prompt système, l'historique, la question et la définition des outils.

### 3.14 `examples_injected` (v1.1)

Émis quand des exemples question → SQL de la banque (`[fewshot]`, A7) ont été ajoutés au prompt du tour. Il est distinct de `context_injected`, qui reste réservé à la mémoire et à son ablation (point V-3).

| Champ | Type | Contenu |
|---|---|---|
| `examples` | liste non vide de `{id, question, score}` | Exemples choisis, du plus proche au moins proche. `id` : identifiant dans la banque (`"ex-07"`) ; `question` : la question de l'exemple ; `score` : score de sélection (nombre) |

### 3.15 `question_asked` (v1.1)

Le modèle a appelé l'outil natif `ask_user` (X4) : le tour est en pause jusqu'à la réponse, au délai ou à une interruption. Le moteur attend le `QuestionBroker` de l'adaptateur (`tools.md`, P2), qui est distinct du `ConfirmationBroker` (point V-1).

| Champ | Type | Contenu |
|---|---|---|
| `question_id` | chaîne | Identifiant de la question, repris par `answer` (WebSocket) et `question_answered` |
| `call_id` | chaîne | Appel `ask_user` mis en pause |
| `question` | chaîne (300 caractères au plus) | La question posée à l'utilisateur |
| `reason` | chaîne (200 caractères au plus) | Pourquoi l'agent demande (« La période change le résultat ») |
| `options` | liste de `{id, label}`, 0 à 5 | Choix proposés ; `[]` si seule la réponse libre est possible. `id` : `[a-z0-9_-]`, 32 caractères au plus ; `label` : 80 caractères au plus |
| `allow_free_text` | booléen | Champ libre affiché ou non. Vaut forcément `true` quand `options` est vide |
| `expires_in_s` | nombre | `[agent] question_timeout_s`. Le front décompte à partir de la réception |

```json
{"type": "question_asked", "session_id": "s-1", "turn_id": "t-1", "ts": 1760433003.2,
 "question_id": "q-1", "call_id": "k-2",
 "question": "Le délai moyen de paiement doit-il porter sur ce trimestre ou sur tout l'historique ?",
 "reason": "Les deux périodes donnent des délais très différents.",
 "options": [{"id": "trimestre", "label": "Ce trimestre"}, {"id": "historique", "label": "Tout l'historique"}],
 "allow_free_text": true, "expires_in_s": 120}
```

### 3.16 `question_answered` (v1.1)

| Champ | Type | Contenu |
|---|---|---|
| `question_id` | chaîne | Question résolue |
| `status` | `"answered"`, `"timeout"` ou `"cancelled"` | `cancelled` : `cancel`, `reset`, déconnexion, ou exécution sans humain (tâche planifiée, harness) |
| `option_id` | chaîne ou `null` | Option choisie, `null` sinon |
| `text` | chaîne (500 caractères au plus) ou `null` | Réponse libre, `null` sinon |

- Avec `"answered"`, `option_id` et `text` ne sont jamais `null` tous les deux.
- Avec `"timeout"` ou `"cancelled"`, ils valent `null` tous les deux. Le `tool_result` qui suit dit au modèle de faire une hypothèse explicite et de l'annoncer.

### 3.17 `document_updated` (v1.1)

Émis juste après le `tool_result` réussi de `document_patch`, `document_delete_section` ou `document_restore` (M4, `memory.md`, P3), que la modification vienne de l'agent ou de l'interface, comme `memory_updated`.

| Champ | Type | Contenu |
|---|---|---|
| `doc_id` | chaîne | Document modifié (`"note-comite"`) |
| `revision` | entier ≥ 1 | Révision du document **après** la modification |
| `section_id` | chaîne ou `null` | Section modifiée (ou supprimée). `null` si et seulement si `op` vaut `"restore"`, qui rétablit tout le document |
| `op` | `"replace"`, `"append"`, `"insert_after"`, `"delete"` ou `"restore"` | Opération appliquée |
| `summary` | chaîne | Résumé en français pour le panneau Note (« Section « Rentabilité » mise à jour (révision 4) ») |

## 4. Séquence d'un tour

### 4.1 Ordre

1. `turn_started` est **toujours le premier** événement d'un tour.
2. Viennent juste après, dans cet ordre et chacun au plus une fois, ceux qui sont émis : `context_injected`, `context_compacted`, `examples_injected` (v1.1). Ils précèdent donc tout `thinking`, `token` ou `tool_call`.
3. Ensuite, dans un ordre quelconque et éventuellement répété : `thinking`, `token`, appels d'outils (ci-dessous), `memory_updated`, `document_updated`.
4. Pour un même `call_id` : `tool_call` → `sql_generated` (si SQL) → `confirmation_required` → `confirmation_resolved` (si HITL) → `tool_result`. Pour un appel à `ask_user` (v1.1) : `tool_call` → `question_asked` → `question_answered` → `tool_result`, sans `sql_generated` ni `confirmation_required`.
5. Le tour se termine par **`message_done`**, ou par un **`error`** portant son `turn_id`. Rien d'autre n'est émis pour ce tour ensuite, sauf le juge.
6. `judge_started` puis `judge_report` arrivent après `message_done`, éventuellement après le `turn_started` du tour suivant (le juge n'est pas bloquant). Les consommateurs rattachent chaque événement à son tour par `turn_id`, jamais par l'ordre d'arrivée.

### 4.2 Tour lancé depuis l'interface (`origin: "ui"`)

`turn_started {origin: "ui", user_message: null}` → `tool_call` (`server: null`) → éventuellement `confirmation_required` / `confirmation_resolved` → `tool_result` → `memory_updated` (si écriture) → `message_done {content: "", interrupted: false, usage: null}`. Ni `token`, ni juge. Rien n'est ajouté à l'historique de conversation.

### 4.3 Invariants vérifiés par les tests de contrat

Les fixtures (`tests/fixtures/events/*.jsonl`, WEB-01) et le moteur (CORE-05) sont testés contre ces règles :

- les champs communs et spécifiques sont tous présents, avec les types du §3 ;
- un seul `turn_started` et une seule fin (`message_done` ou `error`) par `turn_id` ;
- concaténation des `token` du tour = `message_done.content` ;
- `user_message` est `null` si et seulement si `origin` vaut `"ui"` ; `usage` est `null` si et seulement si `origin` vaut `"ui"` ou `interrupted` vaut `true` ;
- chaque `call_id` d'un `tool_result` a un `tool_call` avant lui ; chaque `confirmation_resolved` a son `confirmation_required` ;
- les couples `decision` / `reason` respectent le tableau du §3.7 ;
- `judge_report` suit toujours un `judge_started` du même tour, et aucun des deux n'apparaît pour un tour interrompu ou `origin: "ui"`.

Ajoutés en v1.1 (`tests/contract_rules.py`, `tests/unit/contracts/test_turn_rules_v1_1.py`) :

- `context_injected`, `context_compacted` et `examples_injected` suivent l'ordre du §4.1, point 2 ; `tokens_after < tokens_before` ;
- chaque `question_asked` porte le `call_id` d'un appel à `ask_user` ; chaque `question_answered` a son `question_asked` (même `question_id`), et le `tool_result` de l'appel vient après lui ; aucune question ne reste sans réponse à la fin du tour ;
- au plus `[agent] max_questions_per_turn` `question_asked` par tour ;
- les couples `status` / `option_id` / `text` respectent le §3.16 ;
- `document_updated` suit le `tool_result` réussi d'un outil `document_*` qui écrit ; `section_id` est `null` si et seulement si `op` vaut `"restore"`.

## 5. Codes

### 5.1 `error.code` (liste fermée)

| Code | Quand | `recoverable` | Hors tour possible |
|---|---|:---:|:---:|
| `OLLAMA_UNAVAILABLE` | Connexion à Ollama impossible | true | oui |
| `OLLAMA_TIMEOUT` | Pas de réponse d'Ollama dans `timeout_s` | true | — |
| `MODEL_NOT_FOUND` | Modèle absent d'Ollama (le message propose `ollama pull`) | true | oui |
| `MODEL_NO_TOOLS` | Le modèle ne gère pas les outils (`/api/show`, `capabilities`), ou Ollama refuse les outils d'un tour (HTTP 400, CORE-11) | true | oui |
| `STREAM_INTERRUPTED` | Flux Ollama coupé ou JSON invalide en cours de réponse (ce n'est pas un `cancel`) | true | — |
| `OLLAMA_ERROR` | Autre erreur signalée par Ollama : statut HTTP d'erreur, ou ligne `error` au milieu du flux | true | oui |
| `MAX_ITERATIONS` | `max_tool_iterations` atteint sans réponse finale (CORE-06) | true | — |
| `INTERNAL` | Erreur inattendue du moteur | false | oui |
| `INVALID_MESSAGE` | Message WebSocket invalide (`websocket.md` §7.1) | true | oui |
| `BUSY` | Un tour est déjà en cours (`user_message`, `ui_action`) | true | oui |
| `UNKNOWN_ACTION` | `confirm` sur une action inconnue, résolue ou expirée | true | oui |
| `INVALID_CONFIG` | `set_config` refusé | true | oui |
| `UI_ACTION_FORBIDDEN` | `ui_action` hors liste blanche | true | oui |
| `PERSONA_NOT_FOUND` | `set_persona` ou `/persona` sur un profil inconnu (v1.1) | true | oui |
| `PROMPT_VERSION_NOT_FOUND` | `prompt_version` absente du registre, au démarrage ou par `set_config` (v1.1) | true | oui |
| `UNKNOWN_QUESTION` | `answer` sur une question inconnue, déjà résolue ou expirée (v1.1) | true | oui |
| `SCHEDULE_NOT_FOUND` | `run_schedule` sur une tâche inconnue ou désactivée (v1.1) | true | oui |

`INVALID_MESSAGE`, `BUSY`, `UNKNOWN_ACTION`, `INVALID_CONFIG` et `UI_ACTION_FORBIDDEN` sont émis par la couche web (`websocket.md` §7.1), toujours hors tour (`turn_id: null`). `BUSY` et `UI_ACTION_FORBIDDEN` sont aussi renvoyés par le moteur (`run_ui_action`), qui applique les mêmes règles. Les quatre codes de la v1.1 sont toujours émis hors tour, par la couche web (`answer`, `set_persona`, `run_schedule`, `set_config`), par la CLI (`/persona`) ou au démarrage.

### 5.2 `tool_result.error.code`

Codes posés par le moteur ou par la couche MCP (en majuscules) :

| Code | Quand |
|---|---|
| `UNKNOWN_TOOL` | Le modèle demande un outil qui n'existe pas ; le message ne cite que les outils du profil actif (`tools.md` §4, étape 1) |
| `INVALID_ARGUMENTS` | Arguments non conformes à l'`inputSchema` (T5) ; le modèle a droit à une réparation |
| `ACTION_REJECTED` | Confirmation refusée, expirée ou interrompue (§3.7) |
| `DUPLICATE_CALL` | Même outil avec les mêmes arguments que l'appel précédent : l'appel n'est pas exécuté, le modèle en est informé et le tour continue (CORE-06) |
| `TOOL_DENIED` | Outil interdit par `[hitl] deny`, ou hors du profil actif (v1.1, A5) |
| `QUESTION_LIMIT` | `ask_user` au-delà de `[agent] max_questions_per_turn` : le modèle doit faire une hypothèse (v1.1, X4) |
| `MCP_SERVER_UNAVAILABLE` | Serveur MCP arrêté ou planté (après une tentative de redémarrage) |
| `TOOL_TIMEOUT` | L'outil n'a pas répondu à temps |
| `TOOL_FAILED` | Erreur d'exécution signalée par l'outil (`isError: true`) sans code plus précis |
| `SQL_ERROR` | Erreur SQLite ; renvoyée au modèle pour auto-correction (`max_sql_retries`) |
| `SQL_FORBIDDEN` | La garde SQL refuse la requête (écriture passée à `run_select`, `ATTACH`, `PRAGMA`…) |

Les codes propres à un outil sont transmis tels quels, avec leur casse (`memory.md` §3.4 : `not_found`, `entity_not_found`, `ambiguous_entity`, `invalid_patch`, `conflict`).

Les messages de chaque code, et la panne qui le produit, sont dans [`errors.md`](errors.md) (CORE-11).

## 6. Côté Python (CORE-05)

- `src/sensai/core/events.py` : une dataclass figée par type d'événement, champs communs en tête, méthode `to_dict()` qui produit exactement le JSON de ce document (aucun champ omis).
- `Engine.run_turn(user_message)` et `Engine.run_ui_action(tool, arguments)` : `AsyncIterator[Event]`.
- `Engine.cancel()` : drapeau vérifié entre deux fragments ; le générateur termine proprement par `message_done {interrupted: true}`. **Jamais `task.cancel()`** sur la tâche qui consomme le générateur : il ne pourrait plus émettre `message_done`.
- Le `ConfirmationBroker` web est résolu par la tâche de réception du WebSocket, jamais par le code qui consomme le générateur (sinon interblocage).

## 7. Écarts avec PROJECT §7.1

| PROJECT §7.1 | Ce contrat | Raison |
|---|---|---|
| `turn_started {user_message}` | + `origin`, `user_message` nullable | Tours lancés depuis l'interface (P-5) |
| — | `context_injected` | Mémoire fournie au modèle, affichage et ablation (P-2) |
| `tool_result {ok, summary, data?, duration_ms}` | `data` et `error` toujours présents | Champs toujours présents (§1) ; code d'erreur exploitable |
| `confirmation_required` sans `call_id` | + `call_id` | Relier la modale à l'étape de la timeline |
| `confirmation_resolved {decision, reason?}` | `decision` + `timeout` ; `reason` obligatoire, liste fermée ; + `comment` | P-12, ALL-02 question 6 |
| `memory_updated {id}` | `ref` (chaîne) | Même nom que les outils et le WebSocket (P-14) |
| `message_done {content, usage, duration_ms}` | + `interrupted` ; `usage` nullable | P-4, P-10 |
| `judge_report` = schéma du rapport | Enveloppe `{badge, scores, numeric_check, report, duration_ms}` | Le badge est calculé par le code (P-13, J-1) |
| `error` toujours dans un tour | `turn_id` nullable | Erreurs hors tour (P-5) |
| Outils préfixés « en cas de collision » | Toujours préfixés | Noms stables quand on ajoute un serveur (P-17) |

## 8. Points ouverts

| # | Sujet | Avec |
|---|---|---|
| E-1 | Forme exacte de `numeric_check` (`status`, `unmatched`) : à confirmer avec la sortie d'EVAL-04 | P3 |
| E-2 | Valeurs de `badge` (`verified`, `needs_review`, `contradicted`, `unavailable`) à reporter dans `judge.md` | P3 |
| E-3 | Codes de `tool_result.error` (§5.2) à reprendre dans `tools.md` | P1, P2 |

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P1 — propriétaire | | |
| P2 — HITL, MCP | | |
| P3 — mémoire, juge | | |
| P4 — WebSocket, interface | | |
