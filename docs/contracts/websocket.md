# Contrat — Protocole WebSocket (v1)

| | |
|---|---|
| **Statut** | 🟡 Brouillon (réponses de P1, P2 et P3 intégrées) — gel prévu le **mar 06/10 à 12 h** |
| **Propriétaire** | P4 (Web & Produit) |
| **Consommateurs** | P1 (moteur, événements), P2 (outils MCP, HITL), P3 (mémoire, juge) |
| **Tâche** | WEB-01 (PLANNING §7, sprint 1) |
| **Dépend de** | `events.md` (P1), `tools.md` (P1, P2), `judge.md` + `schemas/judge_report.v1.json` (P3, EVAL-01), `memory.md` (P3, MEM-01) |

Ce document définit tout ce qui circule entre le front web et le serveur FastAPI : la connexion, les messages du client, les messages du serveur, l'ordre dans lequel ils arrivent et les erreurs. Il reprend PROJECT.md §7.2 et le complète avec ce qu'exigent les maquettes (WEB-03) et le catalogue X1.

**Règle de modification** (PLANNING §4.1) : après le gel, toute modification passe par une PR dédiée sur `docs/contracts/`, approuvée par le propriétaire et par chaque consommateur concerné. Pas de modification entre le mercredi 18 h et le lundi 9 h.

---

## 1. Principes

1. **Le web n'a aucune logique métier.** Le serveur relaie les événements du moteur tels quels et transmet les actions de l'utilisateur au moteur. Toute décision (HITL, juge, mémoire) reste dans le moteur.
2. **Un seul canal.** Le streaming, la confirmation HITL, l'annulation et les actions de l'interface sur la mémoire passent par le même WebSocket (ADR-002).
3. **Les écritures passent uniquement par le WebSocket.** Les routes REST (§9) sont en lecture seule. Toute modification déclenchée depuis l'interface passe donc par le moteur, et par conséquent par la politique HITL.
4. **Le front se reconstruit à partir des événements.** Après une reconnexion, le serveur renvoie l'historique de la session sous forme d'événements (§5.2) : le front utilise le même code pour l'affichage en direct et pour la reprise.

---

## 2. Connexion et session

### 2.1 URL

```text
ws://<host>:<port>/ws/{session_id}
```

- `session_id` est **généré par le client** : un UUID v4 (motif accepté `^[A-Za-z0-9-]{8,64}$`).
- Le front le garde dans `sessionStorage`. Un rechargement de la page reprend la même session, alors qu'un nouvel onglet en ouvre une nouvelle.
- Un `session_id` invalide entraîne la fermeture avec le code `4400` (§7.2).

### 2.2 Ouverture

```text
client                                  serveur
  | ---- ouverture WS /ws/{id} ---------> |
  | <--- session_ready -------------------|   toujours en premier (avec history si resumed = true)
  | ---- user_message, … ---------------> |
```

Le client **n'envoie rien avant d'avoir reçu `session_ready`**.

### 2.3 Une connexion active par session

Si une seconde connexion s'ouvre avec le même `session_id`, la première est fermée avec le code `4409`. Le front qui reçoit `4409` **ne se reconnecte pas** et affiche « Session ouverte dans un autre onglet ».

### 2.4 Durée de vie et reconnexion

- Une session (un `Engine` et son historique) vit **en mémoire du serveur**. Elle est conservée **1 h** après la dernière déconnexion, puis libérée.
- Un redémarrage du serveur perd les sessions (la mémoire long terme `memory.db` est conservée). Le client reçoit alors `session_ready` avec `resumed: false` et vide son affichage.
- **Reconnexion côté client** : automatique, avec un délai croissant de 0,5 s, 1 s, 2 s, puis 5 s maximum. Pendant ce temps, la barre d'état affiche « reconnexion en cours ».
- **Déconnexion pendant un tour** (CORE-11) : le tour est annulé (`engine.cancel()`, comme un `cancel`), et toute confirmation en attente est **refusée** (`decision: "reject"`, `reason: "disconnected"`). Une action destructive n'est donc jamais laissée en suspens sans personne devant l'écran. À la reconnexion, `session_ready.history` contient la fin du tour annulé.
- Le maintien de la connexion repose sur les pings WebSocket d'uvicorn (`ws_ping_interval` par défaut). Il n'y a pas de message applicatif de type « heartbeat ».

---

## 3. Format des messages

- Trames **texte**, JSON UTF-8, **un objet par trame**.
- Chaque message a un champ `type` (chaîne).
- **Client → serveur** : le champ `id` (chaîne, ≤ 64 caractères) est facultatif. Quand il est présent, le serveur le renvoie dans `reply_to` sur l'erreur ou l'accusé de réception correspondant. Cela permet au front de savoir à quelle action se rapporte une réponse.
- **Champs inconnus** : ignorés, des deux côtés, pour la compatibilité ascendante.
- **Type inconnu ou champ obligatoire manquant** : erreur `INVALID_MESSAGE` (§7.1). La connexion reste ouverte.
- Dates et durées : `ts` en secondes epoch (float), durées en millisecondes (`*_ms`) ou en secondes (`*_s`), comme dans `events.md`.
- Les identifiants (`session_id`, `turn_id`, `call_id`, `action_id`) sont **opaques** : le front ne les découpe jamais pour en tirer un sens.

---

## 4. Messages client → serveur

| Type | Champs | Rôle | Accepté pendant un tour ? |
|---|---|---|---|
| `user_message` | `content` | Démarre un tour | ❌ `BUSY` |
| `ui_action` | `tool`, `arguments` | Démarre un tour lancé par l'interface (actions sur la mémoire) | ❌ `BUSY` |
| `confirm` | `action_id`, `decision`, `comment?` | Répond à une demande HITL | ✅ (c'est même le seul cas utile) |
| `cancel` | — | Interrompt le tour en cours | ✅ |
| `reset` | — | Vide l'historique de la session | ✅ (annule d'abord le tour) |
| `set_model` | `model` | Change le modèle de l'agent | ✅ effectif au tour suivant |
| `set_config` | `patch` | Modifie les réglages de la session | ✅ effectif au tour suivant |

### 4.1 `user_message`

```json
{"type": "user_message", "id": "c-12", "content": "Quels sont nos 5 clients les plus rentables ce trimestre ?"}
```

- `content` : obligatoire, **non vide après `strip()`**, 4 000 caractères au maximum.
- Sinon : `INVALID_MESSAGE`, avec un `message` qui précise la cause (vide ou trop long).
- Refusé si un tour est en cours (y compris en attente de confirmation) : `BUSY`.
- **Accepté** pendant que le juge du tour précédent tourne encore (le juge n'est pas bloquant, §6.2).

### 4.2 `ui_action`

Permet à l'interface de déclencher un outil sans passer par le modèle : modifier ou supprimer une entrée depuis le panneau Mémoire (US-M3-03).

```json
{"type": "ui_action", "id": "c-31", "tool": "memory_delete", "arguments": {"kind": "fact", "ref": "17"}}
```

- **Liste blanche** : seuls `memory_update` et `memory_delete` sont appelables. **Jamais `execute_write`** ni aucun autre outil. Un autre nom donne l'erreur `UI_ACTION_FORBIDDEN`. Le moteur applique la même liste blanche (défense en profondeur).
- `arguments` sont **exactement ceux des outils mémoire** (`memory.md`, P3) :

| Outil | Arguments | Confirmation |
|---|---|---|
| `memory_update` | `{kind, ref, patch}` | Non |
| `memory_delete` | `{kind, ref}` | **Oui** (l'outil déclare `destructiveHint: true`) |

- `kind` : `"glossary"`, `"entity"`, `"relation"` ou `"fact"`.
- `ref` : **toujours une chaîne**. C'est le terme pour le glossaire, et l'`id` en texte pour les autres.
- `patch` : les champs modifiables de chaque table sont listés dans `memory.md` §2.
- Côté serveur, le message appelle `Engine.run_ui_action(tool, arguments)`. C'est un générateur asynchrone, comme `run_turn` : **le serveur le traite exactement comme un tour**.
- L'action passe par la **politique HITL et le broker de la session**. L'aperçu d'une suppression indique les **cascades** : supprimer une entité supprime aussi ses relations et ses faits (`impact.per_table`).
- L'audit enregistre `actor = "user"` et le `session_id`.
- **Rien n'est ajouté à l'historique de conversation** : la mémoire est de toute façon réinjectée à chaque tour.
- Les erreurs d'exécution arrivent comme dans n'importe quel tour : un `tool_result` avec `ok: false`. Un `ref` inconnu donne `ok: false` avec le code `not_found`.

```text
client                                   serveur
  | ---- ui_action (memory_delete) ------> |
  | <--- turn_started {origin: "ui"} ----- |
  | <--- tool_call ----------------------- |
  | <--- confirmation_required ----------- |
  | ---- confirm (approve | reject) -----> |
  | <--- confirmation_resolved ----------- |
  | <--- tool_result --------------------- |
  | <--- memory_updated (op = delete) ---- |   seulement si approve
  | <--- message_done -------------------- |   fin du tour : content "", usage null (P-10)
```

### 4.3 `confirm`

```json
{"type": "confirm", "action_id": "a-3", "decision": "reject", "comment": "Pas avant la clôture."}
```

- `decision` : `"approve"` ou `"reject"`.
- `comment` : facultatif, 500 caractères au maximum, journalisé avec la décision.
- `action_id` inconnu, déjà résolu ou expiré : erreur `UNKNOWN_ACTION`. Le `confirmation_resolved` correspondant a déjà été envoyé, le front ferme la modale.

### 4.4 `cancel`

```json
{"type": "cancel"}
```

- **Pendant la génération** : le tour se termine par **`message_done` avec `interrupted: true` et `content` = le texte partiel**. Le front l'affiche marqué « interrompue » (PROJECT §6.1).
  - Le tour interrompu **n'entre pas dans l'historique** de conversation.
  - **Le juge ne tourne pas** : pas de `judge_started` ni de `judge_report` pour ce tour.
- **En attente de confirmation** : la confirmation est refusée (`confirmation_resolved` avec `decision: "reject"` et `reason: "cancelled"`), puis le tour se termine de la même façon (`message_done`, `interrupted: true`).
- **Sans tour en cours** : ignoré silencieusement (le message est idempotent).
- Une **coupure côté Ollama** n'est pas une annulation : c'est l'erreur moteur `STREAM_INTERRUPTED` (`events.md`).

> **Implémentation côté serveur** : `cancel` appelle **`engine.cancel()`**, un drapeau que `run_turn` vérifie entre deux fragments. **Jamais `task.cancel()`** : un générateur interrompu de force ne peut plus émettre `message_done`.

### 4.5 `reset`

```json
{"type": "reset", "id": "c-20"}
```

- Si un tour est en cours, il est d'abord annulé comme avec `cancel`.
- L'historique de conversation de la session est vidé. **La mémoire long terme (`memory.db`) n'est pas touchée.**
- Réponse : `session_reset` (§5.2).
- Le front ignore ensuite tout événement portant un `turn_id` antérieur au reset (par exemple un `judge_report` tardif).

### 4.6 `set_model`

```json
{"type": "set_model", "id": "c-21", "model": "llama3.1:8b"}
```

- Seul moyen de changer le modèle de l'agent : `set_config` ne le permet pas.
- Le modèle doit exister dans Ollama (`/api/tags`). Sinon : erreur `MODEL_NOT_FOUND`, le modèle courant reste inchangé.
- Le changement prend effet **au tour suivant**, et l'historique est conservé (US-X1-02).
- Réponse : `model_changed` (§5.2), avec `supports_tools`. Le front affiche un avertissement si le modèle ne gère pas les outils.

### 4.7 `set_config`

```json
{"type": "set_config", "id": "c-22", "patch": {"temperature": 0.2, "judge_enabled": false}}
```

C'est la réponse à l'exigence « configuration » du catalogue X1. Règles (validées par P1) :

- un réglage modifié depuis l'interface **ne vaut que pour la session** et **à partir du tour suivant** ;
- il n'est **jamais écrit dans `config.toml`**.

| Clé (dans `patch`) | Clé `config.toml` | Type | Bornes |
|---|---|---|---|
| `temperature` | `agent.temperature` | number | 0 – 1 |
| `num_ctx` | `ollama.num_ctx` | integer | 2 048 – 32 768 (recharge le modèle : premier tour plus lent) |
| `judge_enabled` | `eval.judge_enabled` | boolean | — |
| `judge_model` | `ollama.judge_model` | string ou `null` | modèle présent dans Ollama ; `null` = même modèle que l'agent |

**Jamais modifiable depuis l'interface** : `[hitl]` (délai de confirmation, `deny`, `always_confirm`), `[database]`, `[mcp]`, `ollama.host` et `[web]`. Sinon, l'interface pourrait désactiver la sécurité.

- Clé hors du tableau ou valeur hors bornes : erreur `INVALID_CONFIG`, **et aucune modification n'est appliquée** (tout ou rien).
- Réponse : `config_updated` (§5.2).
- **Revenir aux valeurs par défaut** (bouton « Réinitialiser » du panneau Réglages) : il n'y a pas de message dédié. Le front lit `defaults` dans `GET /api/config` (§9) et l'envoie tel quel dans un `set_config`.
- Les réglages en lecture seule affichés dans le panneau (hôte Ollama, `keep_alive`, délais…) viennent de `GET /api/config` (§9).

---

## 5. Messages serveur → client

### 5.1 Événements du moteur

Le serveur envoie **exactement les événements de `events.md`** (PROJECT §7.1), sérialisés tels quels, sans enveloppe :

`turn_started`, `context_injected`, `token`, `thinking`, `tool_call`, `tool_result`, `sql_generated`, `confirmation_required`, `confirmation_resolved`, `memory_updated`, `message_done`, `judge_started`, `judge_report`, `error`.

Leur définition fait foi dans `events.md` (propriétaire P1). Ce contrat n'en redéfinit aucun. Pour mémoire, voici les points décidés avec P1 qui concernent directement le front :

| Événement | Ce que le front doit savoir |
|---|---|
| `turn_started` | `origin` : `"user"` ou `"ui"`. Pour un tour lancé par `ui_action`, `user_message` vaut `null` et le front n'affiche pas de bulle utilisateur. |
| `context_injected` | `refs: [{kind, ref, label}]`, émis juste après `turn_started`, **seulement s'il y a des entrées**. `kind` et `ref` comme pour les outils mémoire (§4.2). `label` : le terme pour le glossaire, le nom pour une entité, le début du contenu pour un fait. Les entrées sont choisies par MEM-04 (au plus 5 définitions et 5 faits). Il dit ce qui a été **fourni** au modèle, pas ce qu'il a appliqué : le libellé de l'interface est **« Contexte mémoire fourni »**, jamais « appliqué ». |
| `tool_result` (`run_select`) | `data` = le `structuredContent` de l'outil : `{columns, rows, row_count, truncated}`. `rows` est une liste de listes, dans l'ordre de `columns`. `row_count` est le nombre de lignes **présentes dans `rows`**, pas le total. `truncated: true` signifie qu'il y avait d'autres lignes (le serveur en lit `row_limit + 1` et n'en renvoie que `row_limit`). Les valeurs sont des scalaires JSON (nombre, chaîne, `null`). Le front affiche « 200 lignes affichées · résultat tronqué », jamais un total. |
| `sql_generated` | `operation` : `"SELECT"`, `"INSERT"`, `"UPDATE"`, `"DELETE"` ou `"OTHER"`. Valeur produite par la garde SQL (SEC-01), jamais par le modèle. |
| `confirmation_required` | `impact = {operation, rows_affected, per_table, sample, blocked}` (`tools.md`, P2) :<br>• `per_table` : différences signées de `COUNT(*)` par table, cascades incluses (négatif = lignes supprimées). Pour un `UPDATE`, elles valent 0 : le front affiche alors `rows_affected` sous la forme « N lignes modifiées » au lieu de lire `per_table`.<br>• `blocked: [{reason, count}]` : avec `ON DELETE RESTRICT`, SQLite **rejette toute l'instruction** dès qu'une ligne est protégée. Dans ce cas, `rows_affected` vaut 0, `per_table` vaut `{}`, et `blocked` dit pourquoi. `count` vaut `null` en v1 (MCP-05) ; le comptage exact arrive avec l'aperçu riche (HITL-03).<br>• Une requête qui exclut elle-même les lignes protégées (acte 4 : `NOT IN (SELECT client_id FROM invoices)`) passe : `blocked` est alors vide. |
| `memory_updated` | Identifie l'entrée par `kind` et `ref` (une chaîne), comme les outils mémoire (P-14). |
| `judge_report` | **Enveloppe** calculée par le code (P-13, `events.md` §3.11) : `badge`, `scores`, `numeric_check`, `report`, `duration_ms`.<br>• `badge` : `"verified"`, `"needs_review"`, `"contradicted"` ou `"unavailable"`, affichés « Vérifiée », « À vérifier », « Contredite », « Vérification indisponible ». Le badge et les scores sont **calculés par le code**, jamais par le modèle.<br>• `report` : le rapport produit par le modèle, conforme à `judge_report.v1.json` (P3), ou `null` (rapport invalide après réparation, ou juge sans réponse). `judge_started` est donc **toujours** suivi d'un `judge_report`. Sans rapport, le contrôle numérique peut quand même donner `"contradicted"`.<br>• `numeric_check.unmatched` : nombres de la réponse absents des données, à surligner comme les affirmations contredites.<br>• Dans `report` : `key_figures[]` (0 à 5 : `label`, `value`, `unit`, `source_column`) → cartes KPI ; `assumptions[]` (3 au maximum, `[]` si aucune) → hypothèses ; `followups[]` (3 au maximum) → boutons de relance ; `claims[].text` est un **extrait copié mot pour mot** de la réponse, que le front cherche pour le surligner (s'il est introuvable, l'affirmation reste listée sous le badge sans surlignage).<br>• Si `truncated: true`, un nombre absent des lignes reçues est classé « à vérifier », pas « contredit ». |
| `confirmation_resolved` | `decision` : `"approve"`, `"reject"` ou `"timeout"` (ALL-02, question 6). `reason` **obligatoire**, liste fermée (P-12) : `"user"` (clic sur un bouton), `"cancelled"` (`cancel` ou `reset`), `"disconnected"` (déconnexion), `"timeout"` (délai dépassé, avec `decision: "timeout"`). |
| `message_done` | `interrupted: true` après un `cancel`, avec `content` = le texte partiel. Pour un tour d'origine `ui` : `content: ""` et `usage: null` (P-10) ; **la jauge de contexte garde alors sa dernière valeur**. |
| `error` | `turn_id` vaut `null` pour une erreur hors tour. `code` fait partie de la liste fermée d'`events.md`. |

### 5.2 Messages propres au WebSocket

Ces messages ne sont pas émis par le moteur, mais par la couche web. Ils portent les champs communs `type`, `session_id` et `ts`, **sans `turn_id`**.

#### `session_ready`

Envoyé une fois, juste après l'ouverture.

```json
{"type": "session_ready", "session_id": "3f2b…", "ts": 1760433000.0,
 "protocol_version": 1,
 "server_version": "0.2.0",
 "resumed": false,
 "history": [],
 "model": "qwen3:8b",
 "supports_tools": true,
 "ctx_max": 8192,
 "config": {"temperature": 0.1, "num_ctx": 8192, "judge_enabled": true, "judge_model": null},
 "servers": [
   {"name": "sql_toolbox", "status": "up"},
   {"name": "time", "status": "up"},
   {"name": "filesystem", "status": "down", "error": "La commande `npx` est introuvable : installez Node.js."}
 ],
 "tools": [
   {"name": "sql_toolbox__run_select", "server": "sql_toolbox",
    "description": "Exécute une requête de lecture",
    "read_only": true, "destructive": false, "requires_confirmation": false},
   {"name": "sql_toolbox__execute_write", "server": "sql_toolbox",
    "description": "Exécute une écriture en transaction",
    "read_only": false, "destructive": true, "requires_confirmation": true},
   {"name": "memory_delete", "server": null,
    "description": "Supprime une entrée de la mémoire",
    "read_only": false, "destructive": true, "requires_confirmation": true}
 ]}
```

- `history` : la liste des **événements de la session depuis le dernier `reset`**, dans l'ordre d'émission, y compris ceux des tours d'origine `ui`. Elle est vide quand `resumed: false`. Le front la rejoue dans le même reducer que les événements en direct (principe 4).
  - Pour alléger le message, **les `token` et `thinking` sont omis**, puisque `message_done.content` porte le texte complet.
  - Aucun tour n'est en cours à la reconnexion, car une déconnexion annule le tour (§2.4). La liste ne contient donc jamais de confirmation en attente.
- `config` contient les **valeurs de la session**, uniquement pour les clés du tableau §4.7. Les valeurs de `config.toml` et les réglages en lecture seule sont dans `GET /api/config` (§9).
- `servers[].status` : `"up"` ou `"down"`. Les chips de la barre d'état en découlent (maquette BarreEtat, annotation 2). `servers[].error` est un message en français fourni par la couche MCP, affichable tel quel.
- `tools[].server` vaut `null` pour les outils natifs (mémoire).
- `tools[].name` est le nom tel que le modèle le voit (`tools.md`, MCP-04) :
  - **toujours préfixé par le serveur** (`sql_toolbox__run_select`), pour que les noms restent stables quand on ajoute un serveur ;
  - les outils natifs (mémoire) restent sans préfixe ;
  - caractères `[A-Za-z0-9_-]`, 64 au maximum. Les caractères interdits d'un outil tiers sont remplacés par `_`.
- `tools[].read_only` et `tools[].destructive` : les annotations MCP **telles que déclarées** par le serveur, `null` si elles ne le sont pas (elles sont facultatives, et le client n'invente jamais de `false`).
- `tools[].requires_confirmation` : la décision de la **politique HITL** (annotations, configuration `[hitl]`, refus par défaut). **C'est ce champ que le front affiche** (icône cadenas), jamais les annotations brutes.
- Le front vérifie `protocol_version`. Si elle diffère de la version attendue, il affiche « Version de l'interface incompatible, rechargez la page ».

#### `session_reset`

```json
{"type": "session_reset", "session_id": "3f2b…", "ts": 1760433200.0, "reply_to": "c-20"}
```

- Le front vide le fil de conversation et la timeline, et affiche l'état vide.
- **La jauge de contexte affiche « — » jusqu'au prochain `message_done`.** Le serveur ne peut pas donner l'usage du seul prompt système : Ollama ne compte les tokens qu'en traitant une requête (`prompt_eval_count`), et un appel au modèle juste pour ça coûterait plusieurs secondes.
- Le modèle et la configuration de session sont conservés : le front garde ses valeurs.

#### `model_changed`

```json
{"type": "model_changed", "session_id": "3f2b…", "ts": 1760433210.0, "reply_to": "c-21",
 "model": "llama3.1:8b", "supports_tools": true, "ctx_max": 8192}
```

#### `config_updated`

```json
{"type": "config_updated", "session_id": "3f2b…", "ts": 1760433220.0, "reply_to": "c-22",
 "config": {"temperature": 0.2, "num_ctx": 8192, "judge_enabled": false, "judge_model": null},
 "changed": ["temperature", "judge_enabled"]}
```

---

## 6. Règles de séquence

### 6.1 États de la session

```text
       user_message | ui_action                   confirmation_required
  IDLE ─────────────────────────▶ TURN_RUNNING ─────────────────────────▶ AWAITING_CONFIRMATION
   ▲                                │        ▲                                    │
   │      message_done | error      │        └──────── confirmation_resolved ─────┘
   └────────────────────────────────┘
```

| Message reçu | IDLE | TURN_RUNNING | AWAITING_CONFIRMATION |
|---|---|---|---|
| `user_message` | démarre un tour (`origin: "user"`) | `BUSY` | `BUSY` |
| `ui_action` | démarre un tour (`origin: "ui"`) | `BUSY` | `BUSY` |
| `confirm` | `UNKNOWN_ACTION` | `UNKNOWN_ACTION` | résout l'action |
| `cancel` | ignoré | arrête le tour | refuse puis arrête |
| `reset` | vide | annule puis vide | refuse, annule puis vide |
| `set_model` / `set_config` | appliqué | appliqué au tour suivant | appliqué au tour suivant |

Le juge du tour précédent ne compte pas comme un tour en cours.

### 6.2 Ordre des événements dans un tour

1. `turn_started` est **toujours le premier** événement d'un tour, qu'il vienne de l'utilisateur ou de l'interface.
2. `context_injected`, s'il est émis, vient **juste après** `turn_started`.
3. Viennent ensuite, dans un ordre quelconque et éventuellement répété : `thinking`, `tool_call`, `sql_generated`, `tool_result`, `confirmation_required`, `confirmation_resolved`, `memory_updated`, `token`.
4. Pour un même `call_id`, `tool_call` précède `sql_generated`, qui précède `tool_result`.
5. Le tour se termine par **`message_done` (éventuellement `interrupted: true`), ou par un `error` qui porte son `turn_id`**, récupérable ou non (`events.md` §3.12). Après cela, la session repasse en IDLE. `recoverable` dit seulement si la session reste utilisable.
6. `judge_started` puis `judge_report` arrivent **après** `message_done`, éventuellement **après le `turn_started` du tour suivant**. Ils ne sont jamais émis pour un tour interrompu, pour un tour d'origine `ui`, ou quand `judge_enabled` vaut `false`.

**Règle pour le front** : chaque événement est rattaché à son tour par `turn_id`, jamais par son ordre d'arrivée.

### 6.3 Délai de confirmation

- Le décompte est géré **par le moteur**. Le front affiche un compte à rebours de `expires_in_s` secondes **à partir de l'heure de réception** de l'événement (pas de `ts`), mais ne décide rien.
- À l'expiration, le moteur émet `confirmation_resolved` avec `decision: "timeout"` et `reason: "timeout"` (US-A3-03). Le front affiche « Délai dépassé, action annulée ».
- Une déconnexion refuse immédiatement la confirmation (`decision: "reject"`, `reason: "disconnected"`, §2.4).

---

## 7. Erreurs

### 7.1 Erreurs de protocole (émises par la couche web)

Elles utilisent la forme de l'événement `error` d'`events.md` (`code`, `message`, `recoverable`), avec `turn_id: null` et `reply_to` si le message du client avait un `id`. La connexion reste ouverte. Les codes appartiennent à la **liste fermée d'`events.md`** (ALL-02, question 5) ; ceux marqués ➕ y ont été ajoutés pour ce contrat (P-11). Toutes ces erreurs ont `recoverable: true` et `turn_id: null`.

| Code | Quand | `recoverable` |
|---|---|---|
| `INVALID_MESSAGE` | JSON invalide, `type` inconnu, champ obligatoire manquant ou mal typé, `user_message` vide ou de plus de 4 000 caractères | true |
| `BUSY` ➕ | `user_message` ou `ui_action` refusé parce qu'un tour est en cours (§6.1). Même code que le moteur. | true |
| `MODEL_NOT_FOUND` | `set_model`, ou `set_config` sur `judge_model`, avec un modèle absent d'Ollama | true |
| `UNKNOWN_ACTION` ➕ | `confirm` sur une action inconnue, déjà résolue ou expirée | true |
| `INVALID_CONFIG` ➕ | `set_config` : clé hors du tableau §4.7 ou valeur hors bornes | true |
| `UI_ACTION_FORBIDDEN` ➕ | `ui_action` sur un outil hors liste blanche | true |

```json
{"type": "error", "session_id": "3f2b…", "turn_id": null, "ts": 1760433300.0,
 "reply_to": "c-12", "code": "BUSY",
 "message": "Une réponse est déjà en cours. Attendez la fin ou interrompez-la.", "recoverable": true}
```

Les erreurs du moteur (Ollama injoignable, modèle absent, serveur MCP arrêté, `STREAM_INTERRUPTED`…) sont définies dans `events.md`. Le front ne fait que les afficher : bandeau jaune si `recoverable: true`, message d'erreur dans le fil sinon.

### 7.2 Codes de fermeture

| Code | Sens | Le client se reconnecte ? |
|---|---|---|
| `1000` | Fermeture normale | non |
| `1001` | Arrêt du serveur | oui |
| `1011` | Erreur interne du serveur | oui |
| `4400` | `session_id` invalide | non : il génère un nouvel identifiant |
| `4409` | Session reprise par une autre connexion | non |

---

## 8. Exemples

### 8.1 Tour nominal (acte 1, abrégé)

```jsonl
{"type":"user_message","id":"c-1","content":"Quels sont nos 5 clients les plus rentables ce trimestre ?"}
{"type":"turn_started","session_id":"s-1","turn_id":"t-1","ts":1760433000.0,"origin":"user","user_message":"Quels sont nos 5 clients les plus rentables ce trimestre ?"}
{"type":"context_injected","session_id":"s-1","turn_id":"t-1","ts":1760433000.1,"refs":[{"kind":"glossary","ref":"client rentable","label":"client rentable"}]}
{"type":"tool_call","session_id":"s-1","turn_id":"t-1","ts":1760433000.4,"call_id":"k-1","server":"time","tool":"time__get_current_time","arguments":{"timezone":"Europe/Paris"}}
{"type":"tool_result","session_id":"s-1","turn_id":"t-1","ts":1760433000.5,"call_id":"k-1","ok":true,"summary":"2026-10-14","data":null,"error":null,"duration_ms":120}
{"type":"tool_call","session_id":"s-1","turn_id":"t-1","ts":1760433001.2,"call_id":"k-2","server":"sql_toolbox","tool":"sql_toolbox__run_select","arguments":{"sql":"WITH margin AS (…) SELECT …","purpose":"top 5 marge"}}
{"type":"sql_generated","session_id":"s-1","turn_id":"t-1","ts":1760433001.2,"call_id":"k-2","sql":"WITH margin AS (…) SELECT …","operation":"SELECT"}
{"type":"tool_result","session_id":"s-1","turn_id":"t-1","ts":1760433001.6,"call_id":"k-2","ok":true,"summary":"5 lignes","data":{"columns":["name","marge_eur","delai_moyen_jours"],"rows":[["Brasserie Lemaire",48712.4,47.3],["Imprimerie Vasseur",41208.15,31.0],["Cabinet Roussel",37950.0,28.4],["Garage Delmas",29880.6,null],["Atelier Morel",26011.0,52.9]],"row_count":5,"truncated":false},"error":null,"duration_ms":380}
{"type":"token","session_id":"s-1","turn_id":"t-1","ts":1760433002.0,"delta":"Voici les 5 clients les plus rentables depuis le 1er octobre."}
{"type":"message_done","session_id":"s-1","turn_id":"t-1","ts":1760433004.1,"content":"Voici les 5 clients les plus rentables depuis le 1er octobre.","interrupted":false,"usage":{"prompt_tokens":2900,"completion_tokens":512,"ctx_used":3412,"ctx_max":8192},"duration_ms":4100}
{"type":"judge_started","session_id":"s-1","turn_id":"t-1","ts":1760433004.1}
{"type":"judge_report","session_id":"s-1","turn_id":"t-1","ts":1760433006.2,"badge":"verified","scores":{"faithfulness":1.0},"numeric_check":{"status":"ok","unmatched":[]},"report":{"…":"voir judge_report.v1.json"},"duration_ms":2100}
```

### 8.2 Action destructive (acte 4) : refus

Le SQL exclut lui-même les clients qui ont des factures : la requête passe, et `blocked` est vide (§5.1).

```jsonl
{"type":"user_message","id":"c-5","content":"Supprime les clients inactifs depuis plus de 18 mois."}
{"type":"turn_started","session_id":"s-1","turn_id":"t-4","ts":1760433100.0,"origin":"user","user_message":"Supprime les clients inactifs depuis plus de 18 mois."}
{"type":"tool_call","session_id":"s-1","turn_id":"t-4","ts":1760433101.8,"call_id":"k-7","server":"sql_toolbox","tool":"sql_toolbox__execute_write","arguments":{"sql":"DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM invoices)"}}
{"type":"sql_generated","session_id":"s-1","turn_id":"t-4","ts":1760433101.8,"call_id":"k-7","sql":"DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM invoices)","operation":"DELETE"}
{"type":"confirmation_required","session_id":"s-1","turn_id":"t-4","ts":1760433102.1,"action_id":"a-3","call_id":"k-7","tool":"sql_toolbox__execute_write","sql":"DELETE FROM clients WHERE status = 'inactive' AND id NOT IN (SELECT client_id FROM invoices)","impact":{"operation":"DELETE","rows_affected":27,"per_table":{"clients":-27,"contacts":-41},"sample":[{"id":12,"name":"Atelier Morel"}],"blocked":[]},"expires_in_s":120}
{"type":"confirm","action_id":"a-3","decision":"reject"}
{"type":"confirmation_resolved","session_id":"s-1","turn_id":"t-4","ts":1760433110.4,"action_id":"a-3","decision":"reject","reason":"user","comment":null}
{"type":"tool_result","session_id":"s-1","turn_id":"t-4","ts":1760433110.4,"call_id":"k-7","ok":false,"summary":"Action refusée","data":null,"error":{"code":"ACTION_REJECTED","message":"Suppression refusée par l'utilisateur."},"duration_ms":0}
{"type":"token","session_id":"s-1","turn_id":"t-4","ts":1760433111.0,"delta":"D'accord, je n'ai rien supprimé."}
{"type":"message_done","session_id":"s-1","turn_id":"t-4","ts":1760433112.0,"content":"D'accord, je n'ai rien supprimé.","interrupted":false,"usage":{"prompt_tokens":3100,"completion_tokens":80,"ctx_used":3180,"ctx_max":8192},"duration_ms":12000}
```

### 8.3 Suppression d'une entrée mémoire depuis le panneau (`ui_action`)

Le tour se termine par `message_done` avec `content` vide et `usage` à `null`, sans juge (P-10). Le front n'affiche pas de bulle pour ce tour.

```jsonl
{"type":"ui_action","id":"c-31","tool":"memory_delete","arguments":{"kind":"fact","ref":"17"}}
{"type":"turn_started","session_id":"s-1","turn_id":"t-5","ts":1760433150.0,"origin":"ui","user_message":null}
{"type":"tool_call","session_id":"s-1","turn_id":"t-5","ts":1760433150.0,"call_id":"k-9","server":null,"tool":"memory_delete","arguments":{"kind":"fact","ref":"17"}}
{"type":"confirmation_required","session_id":"s-1","turn_id":"t-5","ts":1760433150.1,"action_id":"a-4","call_id":"k-9","tool":"memory_delete","sql":null,"impact":{"operation":"DELETE","rows_affected":1,"per_table":{"facts":-1},"sample":[{"kind":"fact","ref":"17","label":"Claire veut les montants en k€"}],"blocked":[]},"expires_in_s":120}
{"type":"confirm","action_id":"a-4","decision":"approve"}
{"type":"confirmation_resolved","session_id":"s-1","turn_id":"t-5","ts":1760433153.2,"action_id":"a-4","decision":"approve","reason":"user","comment":null}
{"type":"tool_result","session_id":"s-1","turn_id":"t-5","ts":1760433153.3,"call_id":"k-9","ok":true,"summary":"Fait n°17 supprimé","data":null,"error":null,"duration_ms":12}
{"type":"memory_updated","session_id":"s-1","turn_id":"t-5","ts":1760433153.3,"op":"delete","kind":"fact","ref":"17","summary":"Claire veut les montants en k€"}
{"type":"message_done","session_id":"s-1","turn_id":"t-5","ts":1760433153.3,"content":"","interrupted":false,"usage":null,"duration_ms":3300}
```

### 8.4 Reconnexion après rechargement de la page

```jsonl
{"type":"session_ready","session_id":"s-1","ts":1760433200.0,"protocol_version":1,"resumed":true,"history":[{"type":"turn_started","turn_id":"t-1","…":"…"},{"type":"message_done","turn_id":"t-1","…":"…"},{"type":"judge_report","turn_id":"t-1","…":"…"}],"model":"qwen3:8b","…":"…"}
```

---

## 9. Annexe — Routes REST (lecture seule)

Ces routes servent à charger des listes au démarrage du front. Toutes renvoient du JSON. **Aucune ne modifie quoi que ce soit** (principe 3).

| Route | Réponse | Utilisée par |
|---|---|---|
| `GET /api/health` | `{"status": "ok", "ollama": "up" \| "down", "version": "0.2.0"}` | Barre d'état, tests |
| `GET /api/models` | `{"default": "qwen3:8b", "models": [{"name", "size_bytes", "supports_tools"}]}` | Sélecteur de modèle, choix du modèle du juge |
| `GET /api/tools` | `{"servers": [...], "tools": [...]}`, même forme que `session_ready` | Barre d'état, `/tools` |
| `GET /api/memory` | `{"glossary": [...], "entities": [...], "relations": [...], "facts": [...]}`, colonnes de `memory.db` | Panneau mémoire |
| `GET /api/schema` | `{"tables": [{"name", "columns": [{"name", "type"}]}]}` | Affichage du schéma (facultatif) |
| `GET /api/config` | Valeurs par défaut, bornes et réglages en lecture seule (voir ci-dessous) | Panneau Réglages |

- `supports_tools` vient de `POST /api/show` (champ `capabilities`) d'Ollama.
- `GET /api/schema` est alimenté par `sql_toolbox` (`list_tables`, `describe_table`) via le registry. **La couche web n'ouvre jamais `business.db` elle-même** : `sql_toolbox` reste le seul accès à la base.
- En cas d'erreur : statut HTTP 5xx et `{"error": {"code", "message"}}`, avec les codes d'`events.md`. Par exemple, `GET /api/models` répond 503 avec `OLLAMA_UNAVAILABLE` quand Ollama est arrêté.

**Réponse de `GET /api/config`**

```json
{"defaults": {"temperature": 0.1, "num_ctx": 8192, "judge_enabled": true, "judge_model": null},
 "bounds": {"temperature": [0, 1], "num_ctx": [2048, 32768]},
 "read_only": {
   "ollama_host": "http://localhost:11434",
   "keep_alive": "30m",
   "max_tool_iterations": 6,
   "max_sql_retries": 2,
   "row_limit": 200,
   "query_timeout_ms": 5000,
   "confirmation_timeout_s": 120,
   "numeric_tolerance": 0.01}}
```

- Les valeurs sont celles de `config.toml` au démarrage du serveur. **`set_config` ne les modifie pas** : elles servent de référence pour « Réinitialiser » (§4.7).
- `defaults` contient exactement les clés modifiables du §4.7, et `bounds` les bornes numériques. Le front s'en sert pour construire ses contrôles, sans valeurs en dur.
- **Exclus** : les chemins de fichiers (`business_path`, `memory_path`), les commandes des serveurs MCP, les listes `deny` et `always_confirm`, et la section `[web]`.

---

## 10. Décisions et points ouverts

### 10.1 Décidé avec P1, P2 et P3 (05/10 et 06/10)

| # | Décision | Avec | Reporté dans |
|---|---|---|---|
| P-1 | `assumptions: string[]` (3 au maximum, obligatoire, `[]` si aucune) ajouté à `judge_report.v1`. Badge à quatre états, dont « Vérification indisponible ». `claims[].text` copié mot pour mot pour le surlignage. | P3 | §5.1 — `judge.md` (PR EVAL-01) |
| P-2 | Événement `context_injected {refs: [{kind, ref, label}]}`, émis après `turn_started` seulement s'il y a des entrées choisies par MEM-04. Libellé de l'interface : « Contexte mémoire fourni ». Sert aussi au harness (ablation avec / sans mémoire). | P1, P3 | §5.1, §6.2 — `events.md` |
| P-3 | `tool_result.data` de `run_select` = `{columns, rows, row_count, truncated}` (c'est le `structuredContent` de l'outil). `row_count` = lignes présentes, pas le total. Avec `truncated`, le juge classe un nombre absent « à vérifier ». | P1, P2, P3 | §5.1 — `tools.md` |
| P-4 | `message_done.interrupted`, avec `content` = texte partiel ; tour hors historique, pas de juge. Annulation par drapeau `engine.cancel()`. | P1, P3 | §4.4, §6.2 — `events.md` |
| P-5 | `turn_id` nullable pour les erreurs hors tour. Pas de préfixe `ui-` (identifiants opaques) : un tour lancé par l'interface commence par `turn_started {origin: "ui", user_message: null}`. | P1 | §3, §5.1 — `events.md` |
| P-6 | `Engine.run_ui_action(tool, arguments)`, générateur traité comme un tour, liste blanche `memory_update {kind, ref, patch}` / `memory_delete {kind, ref}`, HITL (cascades dans l'aperçu) et audit `actor = "user"`, refus `BUSY`, hors historique. Message client `ui_action`. Pas de code WS `memory_not_found` : un `ref` inconnu donne un `tool_result` en échec (`not_found`). | P1, P3 | §4.2 — `tools.md`, `memory.md` |
| P-7 | Réglages de session modifiables : `temperature`, `num_ctx`, `judge_enabled`, `judge_model`. Copie de la config de session (`model_copy`), jamais écrite dans `config.toml`. Le modèle de l'agent passe uniquement par `set_model`. Jamais `[hitl]`, `[database]`, `[mcp]`, `ollama.host`, `[web]`, ni `eval.numeric_tolerance`. | P1, P3 | §4.7 |
| P-8 | `kind` : `glossary`, `entity`, `relation`, `fact`. `ref` toujours une chaîne (le terme pour le glossaire, l'id en texte sinon). | P3 | §4.2, §5.1 — `memory.md` |
| P-15 | `tools[].read_only` et `tools[].destructive` nullables (annotations telles que déclarées) ; ajout de `tools[].requires_confirmation`, calculé par la politique HITL, seul champ affiché par le front. | P2 | §5.2 — `tools.md` |
| P-16 | `impact.blocked[].count` nullable (`null` en v1, exact avec HITL-03) ; une instruction qui touche une ligne protégée est rejetée en entier. `per_table` = différences de `COUNT(*)`, nulles pour un `UPDATE`. | P2 | §5.1, §8.2 — `tools.md` |
| P-10 | Un tour d'origine `ui` se termine par `message_done` avec `content: ""`, `interrupted: false`, `usage: null`, sans juge. La règle « un tour finit par `message_done` ou `error` » reste vraie. | P1 | §4.2, §5.1, §8.3 — `events.md` |
| P-11 | `BUSY`, `UNKNOWN_ACTION`, `INVALID_CONFIG`, `UI_ACTION_FORBIDDEN` ajoutés à la liste fermée des codes, tous `recoverable: true`, `turn_id: null`. | P1 | §7.1 — `events.md` |
| P-12 | `confirmation_resolved.reason` **obligatoire**, liste fermée : `user`, `cancelled`, `disconnected`, `timeout`. | P1, P2 | §5.1, §6.3 — `events.md` |
| P-13 | `judge_started` est toujours suivi d'un `judge_report`. Le badge est dans l'**enveloppe** de l'événement (`badge`, `report` nullable), pas dans `judge_report.v1.json`, qui ne décrit que ce que le modèle produit (`additionalProperties: false`). | P1 | §5.1 — `events.md`, `judge.md` (J-1) |
| P-14 | `memory_updated` identifie l'entrée par `ref` (une chaîne), pas `id`. | P1, P3 | §5.1 — `events.md` |
| P-17 | Noms d'outils toujours préfixés par le serveur, outils natifs sans préfixe, `[A-Za-z0-9_-]{1,64}`. `sql_generated.operation` ∈ `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `OTHER` (garde SQL). `/api/schema` passe par `sql_toolbox`. | P2 | §5.1, §5.2, §9 — `tools.md` |

### 10.2 Aligné sur TASKS.md (fiches ALL-02, WEB-01, WEB-04, WEB-07, CORE-11)

| Sujet | Alignement |
|---|---|
| Reconnexion (ALL-02, question 8) | L'historique est dans `session_ready.history`, pas dans un message séparé |
| Déconnexion (CORE-11, WEB-08) | Tour annulé, confirmation en attente refusée |
| `confirmation_resolved.decision` (ALL-02, question 6) | `approve`, `reject` ou `timeout` |
| Codes d'erreur (ALL-02, question 5) | Liste fermée d'`events.md` : `INVALID_MESSAGE`, `MODEL_NOT_FOUND`, `OLLAMA_UNAVAILABLE` |
| Compte à rebours HITL (WEB-07) | Calculé depuis l'heure de réception, pas depuis `ts` |
| Noms des outils (ALL-02, question 3) | Toujours préfixés (P-17) |
| Fixtures (WEB-01) | Noms de fichiers de la fiche (§11) |

### 10.3 Encore ouverts

| # | Question | Avec | Proposition |
|---|---|---|---|
| P-9 | « Basic agent switching » (catalogue X1) : le changement de modèle suffit-il ? | Suivi | Question à poser au suivi ; `set_model` en attendant |
| P-13 bis | Valeurs de `badge` et forme de `numeric_check` dans l'enveloppe de `judge_report` (`events.md` E-1, E-2 ; `judge.md` J-1) | P3 | Celles d'`events.md` §3.11 |
| P-18 | `events.md` rédigé en brouillon (avec toutes les décisions P-2 à P-17) : à relire et valider par son propriétaire | P1 | Brouillon proposé par P4 le 06/10 |

---

## 11. Suite (WEB-01, mar 06/10)

- Schémas JSON des messages dans `docs/contracts/schemas/` : `ws_client.v1.json` (messages du client) et `ws_server.v1.json` (messages propres au WS).
- Fixtures dans `tests/fixtures/events/`, validées par P1 : `nominal.jsonl`, `hitl_approve.jsonl` et `hitl_reject.jsonl` (les deux branches de l'acte 4), `judge_contradiction.jsonl`, `errors.jsonl`. Elles alimentent le `MockEngine` (WEB-04). Dans chacune, la concaténation des `token` doit être égale à `message_done.content`.
- Tests de contrat dans `tests/unit/contracts/` : chaque ligne des fixtures est validée contre les schémas.

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P4 — propriétaire | | |
| P1 — moteur | | |
| P2 — outils MCP, HITL | | 06/10 (réserves 1 et 2 intégrées : P-15, P-16) |
| P3 — mémoire, juge | | |
