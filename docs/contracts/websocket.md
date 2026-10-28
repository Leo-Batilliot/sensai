# Contrat — Protocole WebSocket (v1, ajouts v1.1)

| | |
|---|---|
| **Statut** | 🟢 Validé le **mar 06/10** par P1, P2, P3 et P4 — gelé à la fusion sur `main`. Toute modification passe désormais par une PR dédiée (règle ci-dessous) |
| **Propriétaire** | P4 (Web & Produit) |
| **Consommateurs** | P1 (moteur, événements), P2 (outils MCP, HITL), P3 (mémoire, juge) |
| **Tâche** | WEB-01 (PLANNING §7, sprint 1) |
| **Ajouts v1.1** | Mer 21/10 (décision D-06, [`v1.1-extensions.md`](v1.1-extensions.md) §4, gelé à 18 h) : `answer`, `set_persona`, `share`, `unshare`, `run_schedule`, cinq messages serveur, nouvelles routes REST. **Ajouts uniquement** : rien de la v1 ne change de sens, `protocol_version` reste à `1` |
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
| `answer` (v1.1) | `question_id`, `option_id?`, `text?` | Répond à une question de l'agent (X4) | ✅ (c'est même le seul cas utile) |
| `set_persona` (v1.1) | `persona` | Change de profil (A5) | ✅ effectif au tour suivant |
| `share` (v1.1) | — | Crée un lien de partage en lecture seule (X3) | ✅ (sans le tour en cours) |
| `unshare` (v1.1) | `token` | Révoque un lien de partage (X3) | ✅ |
| `run_schedule` (v1.1) | `schedule_id` | Lance une tâche planifiée tout de suite (X5) | ✅ (la tâche tourne à part) |

Un serveur qui ne prend pas encore en charge un type de la v1.1 répond `INVALID_MESSAGE` (« pas encore pris en charge »), sans fermer la connexion.

### 4.1 `user_message`

```json
{"type": "user_message", "id": "c-12", "content": "Quels sont nos 5 clients les plus rentables ce trimestre ?"}
```

- `content` : obligatoire, **non vide après `strip()`**, 4 000 caractères au maximum.
- Sinon : `INVALID_MESSAGE`, avec un `message` qui précise la cause (vide ou trop long).
- Refusé si un tour est en cours (y compris en attente de confirmation) : `BUSY`.
- **Accepté** pendant que le juge du tour précédent tourne encore (le juge n'est pas bloquant, §6.2).

### 4.2 `ui_action`

Permet à l'interface de déclencher un outil sans passer par le modèle : modifier ou supprimer une entrée depuis le panneau Mémoire (US-M3-03), modifier la note du comité depuis le panneau Note (v1.1, M4, US-M4-01 et 02).

```json
{"type": "ui_action", "id": "c-31", "tool": "memory_delete", "arguments": {"kind": "fact", "ref": "17"}}
```

- **Liste blanche** : seuls `memory_update`, `memory_delete` et, en v1.1, `document_patch`, `document_delete_section` et `document_restore` sont appelables. **Jamais `execute_write`** ni aucun autre outil. Un autre nom donne l'erreur `UI_ACTION_FORBIDDEN`. Le moteur applique la même liste blanche (défense en profondeur).
- `arguments` sont **exactement ceux des outils mémoire** (`memory.md`, P3) :

| Outil | Arguments | Confirmation |
|---|---|---|
| `memory_update` | `{kind, ref, patch}` | Non |
| `memory_delete` | `{kind, ref}` | **Oui** (l'outil déclare `destructiveHint: true`) |
| `document_patch` (v1.1) | `{section_id, op, content, title?, base_revision}` | Non (réversible, versionné) |
| `document_delete_section` (v1.1) | `{section_id, base_revision}` | **Oui** (`destructiveHint: true`) |
| `document_restore` (v1.1) | `{revision}` | Non (crée une révision) |

- Outils `document_*` : arguments de `memory.md` §7.3, audit `actor = "user"`. `base_revision` est la révision que le panneau affichait : si l'agent a écrit entre-temps, le `tool_result` échoue (`REVISION_CONFLICT`) et le panneau relit la note. `document_updated` suit chaque écriture effective, comme pour l'agent. Tant que l'`Engine` n'a pas son `run_ui_action` (P-6), le serveur web construit ce tour lui-même (`web/engines/note.py`), sur le mock comme sur le vrai moteur ; les actions du panneau Mémoire attendent P-6 sur le vrai moteur.

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

- **Pendant la génération** : le tour se termine par **`message_done` avec `interrupted: true`, `content` = le texte partiel et `usage: null`** (Ollama ne donne les compteurs que dans son dernier fragment). Le front l'affiche marqué « interrompue » (PROJECT §6.1), et la jauge garde sa dernière valeur.
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

### 4.8 `answer` (v1.1, X4)

```json
{"type": "answer", "id": "c-14", "question_id": "q-1", "option_id": "trimestre", "text": null}
```

- Répond à l'événement `question_asked` (`events.md`, P1) : l'agent a appelé `ask_user` et le tour est en pause (état AWAITING_ANSWER, §6.1).
- **Exactement un** de `option_id` et `text` est non nul. `option_id` doit être une des `options` de la question. `text` : non vide après `strip()`, 500 caractères au plus, refusé si la question a `allow_free_text: false`. Sinon `INVALID_MESSAGE`, et **la question reste en attente** : l'utilisateur peut corriger.
- Question inconnue, déjà répondue, expirée ou annulée : `UNKNOWN_QUESTION`. Le `question_answered` correspondant a déjà été envoyé, et le front ferme la carte (même règle que `confirm`).
- Pas d'accusé de réception propre : la réponse est l'événement `question_answered` du moteur (`status: "answered"`).
- Le texte libre se tape dans la carte de la question, pas dans le champ du chat : `user_message` reste refusé (`BUSY`).

### 4.9 `set_persona` (v1.1, A5)

```json
{"type": "set_persona", "id": "c-15", "persona": "adv"}
```

- `persona` : un `id` de `GET /api/personas` (§9). Profil inconnu : `PERSONA_NOT_FOUND`, et le profil courant ne change pas.
- Effectif **au tour suivant**, comme `set_model` : un tour en cours se termine avec le profil d'avant. L'historique est conservé ; le moteur y insère un message `system` « Profil actif : … » (P1).
- Le profil ne change ni le modèle ni les réglages de session, et ne lève jamais une confirmation : il ne fait que retirer des outils.
- Réponse : `persona_changed` (§5.2). Demander le profil courant renvoie aussi `persona_changed` (le message est idempotent).

### 4.10 `share` et `unshare` (v1.1, X3)

```json
{"type": "share", "id": "c-16"}
```

```json
{"type": "unshare", "id": "c-17", "token": "kq3Vb0wLr8nT2xYc5HdJ1A"}
```

- `share` fige un **instantané** : les tours terminés (jusqu'au dernier `message_done`, ou `error` de fin de tour), les `judge_report` déjà reçus et la note du comité à sa révision courante. Un tour en cours n'y figure pas, et la suite de la conversation non plus. Sa forme est celle de `GET /api/shares/{token}` (§9).
- Rien d'autre n'est partagé : ni la mémoire long terme, ni les réglages, ni la liste des outils.
- Une session sans tour terminé peut être partagée : la page n'affiche alors que la note.
- Réponses : `share_created` et `share_revoked` (§5.2). `unshare` sur un jeton inconnu ou déjà révoqué : `SHARE_NOT_FOUND`.

### 4.11 `run_schedule` (v1.1, X5)

```json
{"type": "run_schedule", "id": "c-18", "schedule_id": "point-lundi"}
```

- Tâche inconnue ou désactivée (`enabled = false`) : `SCHEDULE_NOT_FOUND`. Tâche déjà en cours : `BUSY` (« Cette tâche tourne déjà »).
- La tâche tourne dans **son propre moteur** : brokers sans humain, profil de la tâche, aucun outil d'écriture ; le rapport est écrit par le planificateur. Elle n'occupe pas la session : `user_message` reste accepté.
- Réponse : `schedule_run_started` (§5.2), avec `reply_to` dans la copie envoyée à la session qui l'a demandée.
- Les événements du tour de la tâche ne sont pas envoyés aux sessions : seulement `schedule_run_started` et `schedule_run_finished`, hors historique. Le rapport reprend la question, la réponse, le SQL, les 20 premières lignes de chaque tableau, le badge, les chiffres clés et les hypothèses, comme l'export Markdown (§9). Un rapport du même jour remplace le précédent (`{date}` = AAAA-MM-JJ).
- Hors du web : `sensai schedule` liste les tâches, `sensai schedule run <id>` en lance une, exactement comme « Exécuter maintenant ».

---

## 5. Messages serveur → client

### 5.1 Événements du moteur

Le serveur envoie **exactement les événements de `events.md`** (PROJECT §7.1), sérialisés tels quels, sans enveloppe :

`turn_started`, `context_injected`, `token`, `thinking`, `tool_call`, `tool_result`, `sql_generated`, `confirmation_required`, `confirmation_resolved`, `memory_updated`, `message_done`, `judge_started`, `judge_report`, `error`.

**v1.1** : `context_compacted`, `examples_injected`, `question_asked`, `question_answered` et `document_updated` s'ajoutent à la liste (`events.md`, P1). Un front **ignore les types qu'il ne connaît pas** (`events.md`, règle 7) : il ne casse pas quand le moteur en émet un nouveau.

Leur définition fait foi dans `events.md` (propriétaire P1). Ce contrat n'en redéfinit aucun. Pour mémoire, voici les points décidés avec P1 qui concernent directement le front :

| Événement | Ce que le front doit savoir |
|---|---|
| `turn_started` | `origin` : `"user"` ou `"ui"`. Pour un tour lancé par `ui_action`, `user_message` vaut `null` et le front n'affiche pas de bulle utilisateur. |
| `context_injected` | `refs: [{kind, ref, label}]`, émis juste après `turn_started`, **seulement s'il y a des entrées**. `kind` et `ref` comme pour les outils mémoire (§4.2). `label` : le terme pour le glossaire, le nom pour une entité, le début du contenu pour un fait. Les entrées sont choisies par MEM-04 (au plus 5 définitions et 5 faits). Il dit ce qui a été **fourni** au modèle, pas ce qu'il a appliqué : le libellé de l'interface est **« Contexte mémoire fourni »**, jamais « appliqué ». |
| `tool_result` (`run_select`) | `data` = le `structuredContent` de l'outil : `{columns, rows, row_count, truncated}`. `rows` est une liste de listes, dans l'ordre de `columns`. `row_count` est le nombre de lignes **présentes dans `rows`**, pas le total. `truncated: true` signifie qu'il y avait d'autres lignes (le serveur en lit `row_limit + 1` et n'en renvoie que `row_limit`). Les valeurs sont des scalaires JSON (nombre, chaîne, `null`). Le front affiche « 200 lignes affichées · résultat tronqué », jamais un total. |
| `sql_generated` | `operation` : `"SELECT"`, `"INSERT"`, `"UPDATE"`, `"DELETE"` ou `"OTHER"`. Valeur produite par la garde SQL (SEC-01), jamais par le modèle. |
| `confirmation_required` | `impact = {operation, rows_affected, per_table, sample, blocked}` (`tools.md`, P2) :<br>• `per_table` : différences signées de `COUNT(*)` par table, cascades incluses (négatif = lignes supprimées). Pour un `UPDATE`, elles valent 0 : le front affiche alors `rows_affected` sous la forme « N lignes modifiées » au lieu de lire `per_table`.<br>• `blocked: [{reason, count}]` : avec `ON DELETE RESTRICT`, SQLite **rejette toute l'instruction** dès qu'une ligne est protégée. Dans ce cas, `rows_affected` vaut 0, `per_table` vaut `{}`, et `blocked` dit pourquoi. `count` vaut `null` en v1 (MCP-05) ; le comptage exact arrive avec l'aperçu riche (HITL-03).<br>• Une requête qui exclut elle-même les lignes protégées (acte 4 : `NOT IN (SELECT client_id FROM invoices)`) passe : `blocked` est alors vide. |
| `memory_updated` | Identifie l'entrée par `kind` et `ref` (une chaîne), comme les outils mémoire (P-14). |
| `judge_report` | **Enveloppe** calculée par le code (P-13, `events.md` §3.11) : `badge`, `scores`, `numeric_check`, `report`, `duration_ms`.<br>• `badge` : `"verified"`, `"needs_review"`, `"contradicted"` ou `"unavailable"`, affichés « Vérifiée », « À vérifier », « Contredite », « Vérification indisponible ». Le badge et les scores sont **calculés par le code**, jamais par le modèle.<br>• `report` : le rapport produit par le modèle, conforme à `judge_report.v1.json` (P3), ou `null` (rapport invalide après réparation, ou juge sans réponse). `judge_started` est donc **toujours** suivi d'un `judge_report`. Sans rapport, le contrôle numérique peut quand même donner `"contradicted"`.<br>• `numeric_check.unmatched` : nombres de la réponse absents des données, surlignés comme contredits si `status` vaut `"mismatch"`, comme « à vérifier » s'il vaut `"inconclusive"` (résultat tronqué).<br>• Dans `report` : `key_figures[]` (0 à 5 : `label`, `value`, `unit`, `source_column`) → cartes KPI ; `assumptions[]` (3 au maximum, `[]` si aucune) → hypothèses ; `followups[]` (3 au maximum) → boutons de relance ; `claims[].text` est un **extrait copié mot pour mot** de la réponse, que le front cherche pour le surligner (s'il est introuvable, l'affirmation reste listée sous le badge sans surlignage).<br>• Si `truncated: true`, un nombre absent des lignes reçues est classé « à vérifier », pas « contredit ». |
| `confirmation_resolved` | `decision` : `"approve"`, `"reject"` ou `"timeout"` (ALL-02, question 6). `reason` **obligatoire**, liste fermée (P-12) : `"user"` (clic sur un bouton), `"cancelled"` (`cancel` ou `reset`), `"disconnected"` (déconnexion), `"timeout"` (délai dépassé, avec `decision: "timeout"`). |
| `message_done` | `interrupted: true` après un `cancel`, avec `content` = le texte partiel. `usage` vaut `null` pour un tour interrompu et pour un tour d'origine `ui` (`content: ""`, P-10) ; **la jauge de contexte garde alors sa dernière valeur**. |
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
 "persona": {"id": "analyste", "name": "Contrôle de gestion", "role": "Copilote de Claire, contrôleuse de gestion"},
 "personas": [
   {"id": "analyste", "name": "Contrôle de gestion", "role": "Copilote de Claire, contrôleuse de gestion"},
   {"id": "adv", "name": "ADV", "role": "Copilote de Karim, responsable ADV"},
   {"id": "admin", "name": "Admin", "role": "Copilote d'Inès, administratrice"}
 ],
 "document_id": "note-comite",
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
  - **v1.1** : `history` contient aussi les `persona_changed` (§5.2), à leur place entre les événements, pour que le séparateur « Profil : ADV (Karim) » survive à un rechargement. Ce sont les seuls messages sans `turn_id` de l'historique.
- `persona` (v1.1) : le profil actif de la session, `{id, name, role}`. `personas` : les profils disponibles, pour le sélecteur. `null` et `[]` pour un serveur sans profils.
- `document_id` (v1.1) : l'identifiant de la note du comité (`GET /api/documents/current`), `null` sans note.
- `config` contient les **valeurs de la session**, uniquement pour les clés du tableau §4.7. Les valeurs de `config.toml` et les réglages en lecture seule sont dans `GET /api/config` (§9).
- `servers[].status` : `"up"` ou `"down"`. Les chips de la barre d'état en découlent (maquette BarreEtat, annotation 2). `servers[].error` est un message en français fourni par la couche MCP, affichable tel quel.
- `tools` : les outils **du profil actif** (v1.1), c'est-à-dire ce que le modèle voit.
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

#### `persona_changed` (v1.1)

```json
{"type": "persona_changed", "session_id": "3f2b…", "ts": 1760433400.0, "reply_to": "c-15",
 "persona": {"id": "adv", "name": "ADV", "role": "Copilote de Karim, responsable ADV"},
 "tools": [
   {"name": "sql_toolbox__execute_write", "server": "sql_toolbox",
    "description": "Exécute une écriture en transaction",
    "read_only": false, "destructive": true, "requires_confirmation": true}
 ]}
```

- `tools` : même forme que `session_ready.tools`, filtrée par le nouveau profil. Le cadenas (`requires_confirmation`) ne change jamais avec le profil.
- Le front affiche le profil dans la barre d'état et un séparateur « Profil : ADV (Karim) » dans le fil, après le tour en cours s'il y en a un.

#### `share_created` et `share_revoked` (v1.1)

```json
{"type": "share_created", "session_id": "3f2b…", "ts": 1760433500.0, "reply_to": "c-16",
 "token": "kq3Vb0wLr8nT2xYc5HdJ1A", "url": "http://127.0.0.1:8000/share/kq3Vb0wLr8nT2xYc5HdJ1A",
 "created_at": 1760433500.0}
```

```json
{"type": "share_revoked", "session_id": "3f2b…", "ts": 1760433600.0, "reply_to": "c-17",
 "token": "kq3Vb0wLr8nT2xYc5HdJ1A"}
```

- `token` : 16 octets aléatoires en base64 URL (`secrets.token_urlsafe(16)`, 22 caractères). `url` est absolue, construite avec l'adresse par laquelle le navigateur joint le serveur (en-tête `Host`) ; sur un serveur ouvert au réseau (`[web] host = "0.0.0.0"`), une adresse locale (`127.0.0.1`, `localhost`) est remplacée par l'adresse de la machine sur le réseau. L'instantané est écrit dans `[web] share_dir` (par défaut `data/shares`).
- Le serveur écoute sur `127.0.0.1` par défaut : le lien n'est joignable que depuis la machine, sauf si `[web] host` est changé.

#### `schedule_run_started` et `schedule_run_finished` (v1.1)

Envoyés à **toutes les sessions ouvertes**, pas seulement à celle qui a lancé la tâche.

```json
{"type": "schedule_run_started", "session_id": "3f2b…", "ts": 1760433700.0, "reply_to": "c-18",
 "schedule_id": "point-lundi", "run_id": "r-12", "trigger": "manual"}
```

```json
{"type": "schedule_run_finished", "session_id": "3f2b…", "ts": 1760433760.0,
 "schedule_id": "point-lundi", "run_id": "r-12", "status": "ok", "badge": "verified",
 "report_path": "reports/planifie/point-lundi-2026-10-26.md", "error": null}
```

- `trigger` : `"schedule"` (heure atteinte) ou `"manual"` (`run_schedule`).
- `status` : `"ok"` ou `"error"`. `badge` : celui du juge (§5.1), ou `null` (erreur, juge désactivé). `report_path` est relatif au dossier de lancement, `null` en cas d'erreur. `error` vaut `{code, message}` ou `null`.
- Une session ouverte après une exécution ne reçoit pas ces messages : elle lit `GET /api/schedules` (§9).

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

**v1.1 : AWAITING_ANSWER.** On y entre sur `question_asked` et on en sort sur `question_answered` (X4). Comme AWAITING_CONFIRMATION, c'est un tour en cours :

| Message reçu | AWAITING_ANSWER |
|---|---|
| `user_message`, `ui_action` | `BUSY` |
| `answer` | résout la question |
| `confirm` | `UNKNOWN_ACTION` |
| `cancel` | `question_answered` (`status: "cancelled"`), puis `message_done` (`interrupted: true`) |
| `reset` | comme `cancel`, puis vide |
| `set_model`, `set_config`, `set_persona` | appliqué au tour suivant |
| `share`, `unshare`, `run_schedule` | traités tout de suite (`share` : sans le tour en cours) |

`set_persona`, `share`, `unshare` et `run_schedule` sont acceptés dans tous les états.

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

### 6.4 Délai d'une question (v1.1)

- Même règle : le moteur compte (`[agent] question_timeout_s`), le front affiche `expires_in_s` à partir de l'heure de réception et ne décide rien.
- À l'expiration, le moteur émet `question_answered` avec `status: "timeout"` et **le tour continue** : le modèle fait une hypothèse explicite et la dit.
- Une déconnexion annule la question (`status: "cancelled"`), puis le tour, comme une confirmation (§2.4). `session_ready.history` ne contient donc jamais de question en attente.

---

## 7. Erreurs

### 7.1 Erreurs de protocole (émises par la couche web)

Elles utilisent la forme de l'événement `error` d'`events.md` (`code`, `message`, `recoverable`), avec `turn_id: null` et `reply_to` si le message du client avait un `id`. La connexion reste ouverte. Les codes appartiennent à la **liste fermée d'`events.md`** (ALL-02, question 5) ; ceux marqués ➕ y ont été ajoutés pour ce contrat (P-11). Toutes ces erreurs ont `recoverable: true` et `turn_id: null`. Les codes marqués « ➕ v1.1 » sont ajoutés à la liste d'`events.md` par P1 (`v1.1-extensions.md` §2.4).

| Code | Quand | `recoverable` |
|---|---|---|
| `INVALID_MESSAGE` | JSON invalide, `type` inconnu, champ obligatoire manquant ou mal typé, `user_message` vide ou de plus de 4 000 caractères | true |
| `BUSY` ➕ | `user_message` ou `ui_action` refusé parce qu'un tour est en cours (§6.1). Même code que le moteur. | true |
| `MODEL_NOT_FOUND` | `set_model`, ou `set_config` sur `judge_model`, avec un modèle absent d'Ollama | true |
| `UNKNOWN_ACTION` ➕ | `confirm` sur une action inconnue, déjà résolue ou expirée | true |
| `INVALID_CONFIG` ➕ | `set_config` : clé hors du tableau §4.7 ou valeur hors bornes | true |
| `UI_ACTION_FORBIDDEN` ➕ | `ui_action` sur un outil hors liste blanche | true |
| `UNKNOWN_QUESTION` ➕ v1.1 | `answer` sur une question inconnue, déjà résolue ou expirée | true |
| `PERSONA_NOT_FOUND` ➕ v1.1 | `set_persona` sur un profil inconnu | true |
| `SHARE_NOT_FOUND` ➕ v1.1 | `unshare` sur un jeton inconnu ou déjà révoqué (et 404 de `GET /api/shares/{token}`) | true |
| `SCHEDULE_NOT_FOUND` ➕ v1.1 | `run_schedule` sur une tâche inconnue ou désactivée | true |

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
| `GET /api/personas` (v1.1) | `{"current": "analyste", "personas": [{id, name, role, tools}]}` | Sélecteur de profil (A5) |
| `GET /api/prompts` (v1.1) | `{"current": "v2", "versions": [{name, version, created, parent, sha256, notes}]}` | Réglages (A4) |
| `GET /api/documents/current` (v1.1) | `{id, title, revision, sections: [{section_id, position, title, content, updated_at}]}` | Panneau Note (M4) |
| `GET /api/documents/current/revisions` (v1.1) | `{revisions: [{revision, op, section_id, actor, at}]}` | Panneau Note (M4) |
| `GET /api/documents/current/export?format=md` (v1.1) | Le document en Markdown (`text/markdown`) | Export (M4, X3) |
| `GET /api/sessions/{session_id}/export?format=md\|json` (v1.1) | La conversation : Markdown lisible, ou JSON | Export (X3) |
| `GET /api/shares` (v1.1) | `{shares: [{token, url, created_at}], local_only}` | Liste des liens (X3) |
| `GET /api/shares/{token}` (v1.1) | L'instantané partagé (JSON) ; 404 si révoqué ou inconnu | Page partagée (X3) |
| `GET /share/{token}` (v1.1) | **Page en lecture seule, sans authentification**, sans WebSocket ni action | Lien partagé (X3) |
| `GET /api/schedules` (v1.1) | `{schedules: [{id, at, every_minutes, question, persona, output, enabled, next_run, last_runs}]}` | Tâches planifiées (X5) |
| `GET /api/schedules/runs/{run_id}/report` (v1.1) | Le rapport Markdown d'une exécution, affiché dans le navigateur (`text/plain`) ; 404 `SCHEDULE_NOT_FOUND` sans rapport | Lien « Rapport » (X5) |

- `supports_tools` vient de `POST /api/show` (champ `capabilities`) d'Ollama.
- `GET /api/schema` est alimenté par `sql_toolbox` (`list_tables`, `describe_table`) via le registry. **La couche web n'ouvre jamais `business.db` elle-même** : `sql_toolbox` reste le seul accès à la base.
- **v1.1** :
  - `GET /api/personas` : `current` est le profil de démarrage (`[agent] persona`) ; celui d'une session est dans `session_ready.persona`. `tools` : les noms d'outils autorisés par le profil, après application des motifs.
  - `GET /api/sessions/{session_id}/export` : le JSON vaut `{session_id, exported_at, model, persona, events}`, où `events` a la forme de `session_ready.history`. Session inconnue ou expirée : 404 ; autre `format` : 400. Le fichier est servi en téléchargement (`Content-Disposition: attachment`) ; le Markdown reprend les questions, réponses, requêtes SQL, 20 premières lignes de chaque tableau, badge, chiffres clés et hypothèses.
  - `GET /api/shares` : `local_only` vaut `true` quand le serveur n'accepte que cette machine (`[web] host` = `127.0.0.1`) ; le front prévient alors que le lien ne s'ouvre pas ailleurs.
  - `GET /api/shares/{token}` : `{token, created_at, model, persona, events, document}`, avec `events` comme `session_ready.history` et `document` comme `GET /api/documents/current` (ou `null`). L'instantané est **figé** dans `data/shares/<token>.json`.
  - `GET /share/{token}` est servie par le build du front (WEB-14) : la même application en mode lecture seule, qui lit `GET /api/shares/{token}`. Elle affiche « Toute personne qui a ce lien voit cette conversation ».
  - `GET /api/schedules` : une tâche a soit `at`, soit `every_minutes` (l'autre vaut `null`). `next_run` vaut `null` si elle est désactivée. `last_runs` : les 5 dernières exécutions, de la plus récente à la plus ancienne, `[{run_id, trigger, started_at, finished_at, status, badge, report_path, error}]`. Une exécution en cours a `status`, `finished_at` et `badge` à `null`.
  - `GET /api/schedules/runs/{run_id}/report` : le chemin lu est celui enregistré par le planificateur pour cette exécution, jamais un chemin venu de la requête.
  - Les dates (`created_at`, `exported_at`, `next_run`, `started_at`, `finished_at`) sont en secondes epoch, comme `ts`.
- En cas d'erreur : statut HTTP 5xx (4xx pour une ressource absente ou un paramètre invalide) et `{"error": {"code", "message"}}`, avec les codes d'`events.md`. Par exemple, `GET /api/models` répond 503 avec `OLLAMA_UNAVAILABLE` quand Ollama est arrêté.

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

## 10. Décisions

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

### 10.3 Clos au gel (06/10)

**Aucun point ouvert.** Les trois derniers points sont clos ainsi :

| # | Sujet | Décision | Suite |
|---|---|---|---|
| P-9 | « Basic agent switching » (catalogue X1) | `set_model` est le changement d'agent de la v1 : il change le modèle au tour suivant sans perdre l'historique. | Question posée au suivi. Si le jury attend davantage (profils d'agent), ce sera une extension par PR de modification de contrat, sans casser l'existant. |
| P-13 bis | Valeurs de `badge` et forme de `numeric_check` | Celles d'`events.md` §3.11 : `badge` ∈ `verified`, `needs_review`, `contradicted`, `unavailable` ; `numeric_check = {status, unmatched}`. | P3 les reporte dans `judge.md` (J-1). |
| P-18 | `events.md`, auquel ce contrat renvoie | Rédigé avec toutes les décisions P-2 à P-17, complété et mergé sur `main` par P1 (ALL-02). Ce contrat est aligné sur sa version : `usage` nul après un `cancel`, `OLLAMA_ERROR`, `DUPLICATE_CALL`, surlignage selon `numeric_check.status`. | Un écart futur se corrige par une PR de modification de ce contrat. |

### 10.4 Ajouts v1.1 (atelier ALL-15, mer 21/10)

Décisions de [`v1.1-extensions.md`](v1.1-extensions.md) §6 qui touchent ce contrat.

V-7 et V-10 ont été proposés par P4 dans la relecture de la section 4 : ils sont à confirmer avant le gel de 18 h.

| # | Décision | Avec | Reporté dans |
|---|---|---|---|
| V-1 | `ask_user` passe par un `QuestionBroker` séparé, résolu par le message `answer` ; la HITL ne bouge pas | P1, P2 | §4.8, §6.1, §6.4 |
| V-4 | Une tâche planifiée tourne sans humain et sans outil d'écriture ; le planificateur écrit le rapport | P2 | §4.11 |
| V-6 | Pas d'expiration des liens de partage en v1.1 (révocation manuelle), cité en limite | — | §4.10 |
| V-7 | `persona_changed` entre dans `session_ready.history`, à sa place | P1 | §5.2 |
| V-10 | `SHARE_NOT_FOUND` pour un jeton inconnu ou révoqué | P1 | §7.1 |
| — | Les nouvelles clés de `set_config` (`prompt_version`, `fewshot_enabled`) et les outils `document_*` d'`ui_action` sont reportés ici par la PR de la fonctionnalité qui les lit (A4, A7, M4). `document_*` : reportés par WEB-17 | P1, P3 | §4.2, §4.7 |

---

## 11. Fichiers liés (WEB-01)

- **Schémas JSON** dans `docs/contracts/schemas/` :
  - [`ws_client.v1.json`](schemas/ws_client.v1.json) : messages du client (§4), structure seulement. Le serveur répond `INVALID_MESSAGE` quand il échoue ; liste blanche, bornes et modèle sont vérifiés ensuite.
  - [`ws_server.v1.json`](schemas/ws_server.v1.json) : messages propres au WebSocket (§5.2).
  - Les événements du moteur ont leurs propres schémas : [`schemas/events/`](schemas/events/), un par événement (P1, CORE-05).
- **Fixtures** dans [`tests/fixtures/events/`](../../tests/fixtures/events/README.md) : `nominal`, `memory`, `judge_contradiction`, `hitl_approve`, `hitl_reject`, `errors`. Elles alimentent le `MockEngine` (WEB-04) ; leur README décrit comment les rejouer.
- **Tests de contrat** : [`tests/unit/contracts/test_websocket_contract.py`](../../tests/unit/contracts/test_websocket_contract.py) vérifie chaque fixture et chaque exemple de ce document contre les schémas officiels (un événement à la fois), puis les invariants d'un tour entier (`events.md` §4.3 : ordre, `token` = `message_done.content`…). Les règles partagées sont dans [`tests/contract_rules.py`](../../tests/contract_rules.py). Un exemple faux ici fait échouer la CI.

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P4 — propriétaire | Arthur| 06/10 |
| P1 — moteur | Gabin | 06/10 (réponses du 05/10 sur P-2 à P-7, du 06/10 sur P-9 à P-14) |
| P2 — outils MCP, HITL | Thomas | 06/10 (réserves 1 et 2 intégrées : P-15, P-16) |
| P3 — mémoire, juge | Léo | 06/10 (réponses du 05/10 sur P-1 à P-8 ; valeurs de `badge` à reporter dans `judge.md`) |
