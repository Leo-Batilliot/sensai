# Contrat — Protocole WebSocket (v1)

| | |
|---|---|
| **Statut** | 🟡 Brouillon (lun 05/10) — gel prévu le **mar 06/10 à 12 h** |
| **Propriétaire** | P4 (Web & Produit) |
| **Consommateurs** | P1 (moteur, événements), P3 (mémoire) |
| **Tâche** | WEB-01 (PLANNING §7, sprint 1) |
| **Dépend de** | `events.md` (P1), `tools.md` (P1, P2), `judge_report.v1.json` (P3), schéma mémoire (P3, MEM-01) |

Ce document définit tout ce qui circule entre le front web et le serveur FastAPI : la connexion, les messages du client, les messages du serveur, l'ordre dans lequel ils arrivent et les erreurs. Il reprend PROJECT.md §7.2 et le complète avec ce qu'exigent les maquettes (WEB-03) et le catalogue X1.

---

## 1. Principes

1. **Le web n'a aucune logique métier.** Le serveur relaie les événements du moteur tels quels et transmet les actions de l'utilisateur au moteur. Toute décision (HITL, juge, mémoire) reste dans le moteur.
2. **Un seul canal.** Le streaming, la confirmation HITL, l'annulation et les actions sur la mémoire passent par le même WebSocket (ADR-002).
3. **Les écritures passent uniquement par le WebSocket.** Les routes REST (§9) sont en lecture seule. Ainsi, toute modification déclenchée depuis l'interface passe par la session et donc par la politique HITL.
4. **Le front se reconstruit à partir des événements.** Après une reconnexion, le serveur renvoie l'historique de la session sous forme d'événements (§2.4) : le front utilise le même code pour l'affichage en direct et pour la reprise.

---

## 2. Connexion et session

### 2.1 URL

```text
ws://<host>:<port>/ws/{session_id}
```

- `session_id` est **généré par le client** : un UUID v4 (36 caractères, motif `^[A-Za-z0-9-]{8,64}$`).
- Le front le garde dans `sessionStorage`. Un rechargement de la page reprend la même session, alors qu'un nouvel onglet en ouvre une nouvelle.
- Un `session_id` invalide entraîne la fermeture avec le code `4400` (§7.2).

### 2.2 Ouverture

```text
client                                  serveur
  | ---- ouverture WS /ws/{id} ---------> |
  | <--- session_ready -------------------|   toujours en premier
  | <--- session_replay ------------------|   seulement si resumed = true
  | ---- user_message, … ---------------> |
```

Le client **n'envoie rien avant d'avoir reçu `session_ready`**.

### 2.3 Une connexion active par session

Si une seconde connexion s'ouvre avec le même `session_id`, la première est fermée avec le code `4409`. Le front qui reçoit `4409` **ne se reconnecte pas** et affiche « Session ouverte dans un autre onglet ».

### 2.4 Durée de vie et reconnexion

- Une session (un `Engine` et son historique) vit **en mémoire du serveur**. Elle est conservée **1 h** après la dernière déconnexion, puis libérée.
- Un redémarrage du serveur perd les sessions (la mémoire long terme `memory.db` est conservée). Le client reçoit alors `session_ready` avec `resumed: false` et vide son affichage.
- **Reconnexion côté client** : automatique, avec un délai croissant de 0,5 s, 1 s, 2 s, puis 5 s maximum. Pendant ce temps, la barre d'état affiche « reconnexion en cours ».
- Un tour en cours **continue côté serveur** pendant une déconnexion. Les événements émis pendant la coupure sont rattrapés par `session_replay`.
- Le maintien de la connexion repose sur les pings WebSocket d'uvicorn (`ws_ping_interval` par défaut). Il n'y a pas de message applicatif de type « heartbeat ».

---

## 3. Format des messages

- Trames **texte**, JSON UTF-8, **un objet par trame**.
- Chaque message a un champ `type` (chaîne).
- **Client → serveur** : le champ `id` (chaîne, ≤ 64 caractères) est facultatif. Quand il est présent, le serveur le renvoie dans `reply_to` sur l'erreur ou l'accusé de réception correspondant. Cela permet au front de savoir à quelle action se rapporte une réponse.
- **Champs inconnus** : ignorés, des deux côtés, pour la compatibilité ascendante.
- **Type inconnu ou champ obligatoire manquant** : erreur `bad_message` (§7.1). La connexion reste ouverte.
- Dates et durées : `ts` en secondes epoch (float), durées en millisecondes (`*_ms`) ou en secondes (`*_s`), comme dans `events.md`.

---

## 4. Messages client → serveur

| Type | Champs | Rôle | Accepté pendant un tour ? |
|---|---|---|---|
| `user_message` | `content` | Démarre un tour | ❌ `turn_in_progress` |
| `confirm` | `action_id`, `decision`, `comment?` | Répond à une demande HITL | ✅ (c'est même le seul cas utile) |
| `cancel` | — | Interrompt le tour en cours | ✅ |
| `reset` | — | Vide l'historique de la session | ✅ (annule d'abord le tour) |
| `set_model` | `model` | Change de modèle | ✅ effectif au tour suivant |
| `set_config` | `patch` | Modifie les réglages de la session | ✅ effectif au tour suivant |
| `memory_update` | `kind`, `ref`, `patch` | Modifie une entrée de la mémoire | ❌ `turn_in_progress` |
| `memory_delete` | `kind`, `ref` | Supprime une entrée de la mémoire (passe par le HITL) | ❌ `turn_in_progress` |

Les sous-sections suivantes détaillent chaque message.

### 4.1 `user_message`

```json
{"type": "user_message", "id": "c-12", "content": "Quels sont nos 5 clients les plus rentables ce trimestre ?"}
```

- `content` : obligatoire, **non vide après `strip()`**, 4 000 caractères au maximum.
- Sinon : `empty_message` ou `message_too_long`.
- Refusé si un tour est en cours (y compris en attente de confirmation) : `turn_in_progress`.
- **Accepté** pendant que le juge du tour précédent tourne encore (le juge n'est pas bloquant, §6.3).

### 4.2 `confirm`

```json
{"type": "confirm", "action_id": "a-3", "decision": "reject", "comment": "Pas avant la clôture."}
```

- `decision` : `"approve"` ou `"reject"`.
- `comment` : facultatif, 500 caractères au maximum, journalisé avec la décision.
- `action_id` inconnu, déjà résolu ou expiré : erreur `unknown_action`. Le `confirmation_resolved` correspondant a déjà été envoyé, le front ferme la modale.

### 4.3 `cancel`

```json
{"type": "cancel"}
```

- **Pendant la génération** : le moteur arrête le flux. Le tour se termine par `message_done` marqué comme interrompu (point ouvert P-4). La réponse partielle n'est pas ajoutée telle quelle à l'historique (PROJECT §6.1).
- **En attente de confirmation** : la confirmation est **refusée** (`confirmation_resolved` avec `decision: "reject"` et `reason: "cancelled"`), puis le tour s'arrête.
- **Sans tour en cours** : ignoré silencieusement (le message est idempotent).

### 4.4 `reset`

```json
{"type": "reset", "id": "c-20"}
```

- Si un tour est en cours, il est d'abord annulé comme avec `cancel`.
- L'historique de conversation de la session est vidé. **La mémoire long terme (`memory.db`) n'est pas touchée.**
- Réponse : `session_reset` (§5.2).
- Le front ignore ensuite tout événement portant un `turn_id` antérieur au reset (par exemple un `judge_report` tardif).

### 4.5 `set_model`

```json
{"type": "set_model", "id": "c-21", "model": "llama3.1:8b"}
```

- Le modèle doit exister dans Ollama (`/api/tags`). Sinon : erreur `unknown_model`, le modèle courant reste inchangé.
- Le changement prend effet **au tour suivant**, et l'historique est conservé (US-X1-02).
- Réponse : `model_changed` (§5.2), avec `supports_tools`. Le front affiche un avertissement si le modèle ne gère pas les outils.

### 4.6 `set_config`

```json
{"type": "set_config", "id": "c-22", "patch": {"temperature": 0.2, "judge_enabled": false}}
```

C'est la réponse à l'exigence « configuration » du catalogue X1. Les réglages sont **propres à la session** : ils ne sont jamais écrits dans `config.toml`.

| Clé | Type | Bornes | Modifiable |
|---|---|---|---|
| `temperature` | number | 0 – 1 | ✅ |
| `num_ctx` | integer | 2 048 – 32 768 | ✅ (provoque un rechargement du modèle par Ollama) |
| `judge_enabled` | boolean | — | ✅ |
| `max_tool_iterations` | integer | — | ❌ lecture seule |
| `confirmation_timeout_s` | integer | — | ❌ lecture seule (sécurité) |
| `row_limit` | integer | — | ❌ lecture seule |

- Clé inconnue, en lecture seule ou hors bornes : erreur `invalid_config`, **et aucune modification n'est appliquée** (tout ou rien).
- Réponse : `config_updated` (§5.2).
- La liste des clés modifiables est à valider avec P1 (point ouvert P-7).

### 4.7 `memory_update`

```json
{"type": "memory_update", "id": "c-30", "kind": "glossary", "ref": "client rentable",
 "patch": {"definition": "Marge brute > 30 % sur la période", "sql_hint": "marge / ca > 0.30"}}
```

- `kind` : `"glossary"`, `"entity"`, `"relation"` ou `"fact"` (à aligner sur MEM-01, point ouvert P-8).
- `ref` : identifiant de l'entrée, **toujours une chaîne**. C'est le `term` pour le glossaire, et l'`id` converti en chaîne pour les autres tables.
- `patch` : les champs modifiables de la table (schéma `memory.db`, PROJECT §6.4).
- L'action est exécutée par le moteur avec `actor = 'user'` dans `memory_audit`. Le moteur émet ensuite `memory_updated` (op `update`).
- `ref` inconnu : erreur `memory_not_found`.

### 4.8 `memory_delete`

```json
{"type": "memory_delete", "id": "c-31", "kind": "fact", "ref": "17"}
```

La suppression est destructive : **elle passe par le HITL** comme l'outil `memory_delete` de l'agent (US-M3-03).

```text
client                                   serveur
  | ---- memory_delete ------------------> |
  | <--- confirmation_required ----------- |   tool = "memory_delete"
  | ---- confirm (approve | reject) -----> |
  | <--- confirmation_resolved ----------- |
  | <--- memory_updated (op = delete) ---- |   seulement si approve
```

Ces actions déclenchées depuis l'interface n'ont pas de `turn_started` ni de `message_done`. Leurs événements portent un `turn_id` préfixé par `ui-` (point ouvert P-5).

---

## 5. Messages serveur → client

### 5.1 Événements du moteur

Le serveur envoie **exactement les événements de `events.md`** (PROJECT §7.1), sérialisés tels quels, sans enveloppe :

`turn_started`, `token`, `thinking`, `tool_call`, `tool_result`, `sql_generated`, `confirmation_required`, `confirmation_resolved`, `memory_updated`, `message_done`, `judge_started`, `judge_report`, `error`.

Leur définition fait foi dans `events.md` (propriétaire P1). Ce contrat n'en redéfinit aucun.

### 5.2 Messages propres au WebSocket

Ces messages ne sont pas émis par le moteur, mais par la couche web. Ils portent les champs communs `type`, `session_id` et `ts`, **sans `turn_id`**.

#### `session_ready`

Envoyé une fois, juste après l'ouverture.

```json
{"type": "session_ready", "session_id": "3f2b…", "ts": 1760433000.0,
 "protocol_version": 1,
 "server_version": "0.2.0",
 "resumed": false,
 "model": "qwen3:8b",
 "supports_tools": true,
 "ctx_max": 8192,
 "config": {"temperature": 0.1, "num_ctx": 8192, "judge_enabled": true,
            "max_tool_iterations": 6, "confirmation_timeout_s": 120, "row_limit": 200},
 "servers": [
   {"name": "sql_toolbox", "status": "up"},
   {"name": "time", "status": "up"},
   {"name": "filesystem", "status": "down", "error": "npx introuvable"}
 ],
 "tools": [
   {"name": "sql_toolbox__run_select", "server": "sql_toolbox",
    "description": "Exécute une requête de lecture", "read_only": true, "destructive": false},
   {"name": "memory_delete", "server": null,
    "description": "Supprime une entrée de la mémoire", "read_only": false, "destructive": true}
 ]}
```

- `servers[].status` : `"up"` ou `"down"`. Les chips de la barre d'état en découlent (maquette BarreEtat, annotation 2).
- `tools[].server` vaut `null` pour les outils natifs (mémoire).
- `tools[].name` est le nom tel que le modèle le voit, préfixé en cas de collision (PROJECT §6.3).
- Le front vérifie `protocol_version`. Si elle diffère de la version attendue, il affiche « Version de l'interface incompatible, rechargez la page ».

#### `session_replay`

Envoyé juste après `session_ready` quand `resumed: true`.

```json
{"type": "session_replay", "session_id": "3f2b…", "ts": 1760433100.0,
 "events": [ {"type": "turn_started", "…": "…"}, {"type": "message_done", "…": "…"} ]}
```

- Il contient **tous les événements de la session depuis le dernier `reset`**, dans l'ordre d'émission.
- Pour alléger le message, **les `token` et `thinking` des tours terminés sont omis**, puisque `message_done.content` porte le texte complet. Pour un tour encore en cours, ils sont tous inclus.
- **Confirmation en attente** : si le replay contient un `confirmation_required` sans `confirmation_resolved`, et que `ts + expires_in_s` est encore dans le futur, le front rouvre la modale avec le temps restant. Le serveur et le navigateur tournent sur la même machine, donc l'horloge est partagée.

#### `session_reset`

```json
{"type": "session_reset", "session_id": "3f2b…", "ts": 1760433200.0, "reply_to": "c-20"}
```

#### `model_changed`

```json
{"type": "model_changed", "session_id": "3f2b…", "ts": 1760433210.0, "reply_to": "c-21",
 "model": "llama3.1:8b", "supports_tools": true, "ctx_max": 8192}
```

#### `config_updated`

```json
{"type": "config_updated", "session_id": "3f2b…", "ts": 1760433220.0, "reply_to": "c-22",
 "config": {"temperature": 0.2, "num_ctx": 8192, "judge_enabled": false,
            "max_tool_iterations": 6, "confirmation_timeout_s": 120, "row_limit": 200},
 "changed": ["temperature", "judge_enabled"]}
```

---

## 6. Règles de séquence

### 6.1 États de la session

```text
          user_message                       confirmation_required
  IDLE ─────────────────▶ TURN_RUNNING ─────────────────────────▶ AWAITING_CONFIRMATION
   ▲                        │        ▲                                    │
   │  message_done | error  │        └──────── confirmation_resolved ─────┘
   └────────────────────────┘
```

| Message reçu | IDLE | TURN_RUNNING | AWAITING_CONFIRMATION |
|---|---|---|---|
| `user_message` | démarre un tour | `turn_in_progress` | `turn_in_progress` |
| `confirm` | `unknown_action` | `unknown_action` | résout l'action |
| `cancel` | ignoré | arrête le tour | refuse puis arrête |
| `reset` | vide | annule puis vide | refuse, annule puis vide |
| `set_model` / `set_config` | appliqué | appliqué au tour suivant | appliqué au tour suivant |
| `memory_update` / `memory_delete` | exécuté | `turn_in_progress` | `turn_in_progress` |

Le juge du tour précédent ne compte pas comme un tour en cours.

### 6.2 Ordre des événements dans un tour

1. `turn_started` est **toujours le premier** événement d'un tour.
2. Viennent ensuite, dans un ordre quelconque et éventuellement répété : `thinking`, `tool_call`, `sql_generated`, `tool_result`, `confirmation_required`, `confirmation_resolved`, `memory_updated`, `token`.
3. Pour un même `call_id`, `tool_call` précède `sql_generated`, qui précède `tool_result`.
4. Le tour se termine par **`message_done`, ou par `error` avec `recoverable: false`**. Après cela, la session repasse en IDLE.
5. `judge_started` puis `judge_report` arrivent **après** `message_done`, éventuellement **après le `turn_started` du tour suivant**.

**Règle pour le front** : chaque événement est rattaché à son tour par `turn_id`, jamais par son ordre d'arrivée.

### 6.3 Délai de confirmation

- Le décompte est géré **par le moteur**. Le front affiche un compte à rebours jusqu'à `ts + expires_in_s`, mais ne décide rien.
- À l'expiration, le moteur émet `confirmation_resolved` avec `decision: "reject"` et `reason: "timeout"` (US-A3-03).
- Une déconnexion n'arrête pas le décompte.

---

## 7. Erreurs

### 7.1 Erreurs de protocole (émises par la couche web)

Elles utilisent la forme de l'événement `error` d'`events.md` (`code`, `message`, `recoverable`), avec `turn_id: null` et `reply_to` si le message du client avait un `id`. La connexion reste ouverte.

| Code | Quand | `recoverable` |
|---|---|---|
| `bad_message` | JSON invalide, `type` inconnu, champ obligatoire manquant ou mal typé | true |
| `empty_message` | `user_message` vide après `strip()` | true |
| `message_too_long` | `content` > 4 000 caractères | true |
| `turn_in_progress` | Message refusé parce qu'un tour est en cours (§6.1) | true |
| `unknown_action` | `confirm` sur une action inconnue, déjà résolue ou expirée | true |
| `unknown_model` | `set_model` sur un modèle absent d'Ollama | true |
| `invalid_config` | `set_config` : clé inconnue, en lecture seule ou hors bornes | true |
| `memory_not_found` | `memory_update` ou `memory_delete` sur un `ref` inexistant | true |

```json
{"type": "error", "session_id": "3f2b…", "turn_id": null, "ts": 1760433300.0,
 "reply_to": "c-12", "code": "turn_in_progress",
 "message": "Une réponse est déjà en cours. Attendez la fin ou interrompez-la.", "recoverable": true}
```

Les erreurs du moteur (Ollama injoignable, modèle absent, serveur MCP arrêté…) sont définies dans `events.md`. Le front ne fait que les afficher : bandeau jaune si `recoverable: true`, message d'erreur dans le fil sinon.

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
{"type":"turn_started","session_id":"s-1","turn_id":"t-1","ts":1760433000.0,"user_message":"Quels sont nos 5 clients les plus rentables ce trimestre ?"}
{"type":"tool_call","session_id":"s-1","turn_id":"t-1","ts":1760433000.4,"call_id":"k-1","server":"time","tool":"time__get_current_time","arguments":{"timezone":"Europe/Paris"}}
{"type":"tool_result","session_id":"s-1","turn_id":"t-1","ts":1760433000.5,"call_id":"k-1","ok":true,"summary":"2026-10-14","duration_ms":120}
{"type":"tool_call","session_id":"s-1","turn_id":"t-1","ts":1760433001.2,"call_id":"k-2","server":"sql_toolbox","tool":"sql_toolbox__run_select","arguments":{"sql":"WITH margin AS (…) SELECT …","purpose":"top 5 marge"}}
{"type":"sql_generated","session_id":"s-1","turn_id":"t-1","ts":1760433001.2,"call_id":"k-2","sql":"WITH margin AS (…) SELECT …","operation":"SELECT"}
{"type":"tool_result","session_id":"s-1","turn_id":"t-1","ts":1760433001.6,"call_id":"k-2","ok":true,"summary":"5 lignes","data":{"columns":["name","marge_eur","delai_moyen_jours"],"rows":[["Brasserie Lemaire",48712.4,47.3]],"row_count":5,"truncated":false},"duration_ms":380}
{"type":"token","session_id":"s-1","turn_id":"t-1","ts":1760433002.0,"delta":"Voici les 5 clients"}
{"type":"message_done","session_id":"s-1","turn_id":"t-1","ts":1760433004.1,"content":"Voici les 5 clients …","usage":{"prompt_tokens":2900,"completion_tokens":512,"ctx_used":3412,"ctx_max":8192},"duration_ms":4100}
{"type":"judge_started","session_id":"s-1","turn_id":"t-1","ts":1760433004.1}
{"type":"judge_report","session_id":"s-1","turn_id":"t-1","ts":1760433006.2,"…":"voir judge_report.v1.json"}
```

La forme de `tool_result.data` dans cet exemple est une **proposition** (point ouvert P-3).

### 8.2 Action destructive (acte 4) : refus

```jsonl
{"type":"user_message","id":"c-5","content":"Supprime les clients inactifs depuis plus de 18 mois."}
{"type":"turn_started","session_id":"s-1","turn_id":"t-4","ts":1760433100.0,"user_message":"Supprime les clients inactifs depuis plus de 18 mois."}
{"type":"confirmation_required","session_id":"s-1","turn_id":"t-4","ts":1760433102.1,"action_id":"a-3","tool":"sql_toolbox__execute_write","sql":"DELETE FROM clients WHERE …","impact":{"operation":"DELETE","rows_affected":27,"per_table":{"clients":-27,"contacts":-41},"sample":[{"id":12,"name":"Atelier Morel"}],"blocked":[{"reason":"factures à conserver","count":14}]},"expires_in_s":120}
{"type":"confirm","action_id":"a-3","decision":"reject"}
{"type":"confirmation_resolved","session_id":"s-1","turn_id":"t-4","ts":1760433110.4,"action_id":"a-3","decision":"reject","reason":"user"}
{"type":"message_done","session_id":"s-1","turn_id":"t-4","ts":1760433112.0,"content":"D'accord, je n'ai rien supprimé …","usage":{"prompt_tokens":3100,"completion_tokens":80,"ctx_used":3180,"ctx_max":8192},"duration_ms":12000}
```

### 8.3 Reconnexion après rechargement de la page

```jsonl
{"type":"session_ready","session_id":"s-1","ts":1760433200.0,"protocol_version":1,"resumed":true,"model":"qwen3:8b","…":"…"}
{"type":"session_replay","session_id":"s-1","ts":1760433200.0,"events":[{"type":"turn_started","turn_id":"t-1","…":"…"},{"type":"message_done","turn_id":"t-1","…":"…"},{"type":"judge_report","turn_id":"t-1","…":"…"}]}
```

---

## 9. Annexe — Routes REST (lecture seule)

Ces routes servent à charger des listes au démarrage du front. Toutes renvoient du JSON. **Aucune ne modifie quoi que ce soit** (principe 3).

| Route | Réponse | Utilisée par |
|---|---|---|
| `GET /api/health` | `{"status": "ok", "ollama": "up" \| "down", "version": "0.2.0"}` | Barre d'état, tests |
| `GET /api/models` | `{"default": "qwen3:8b", "models": [{"name", "size_bytes", "supports_tools"}]}` | Sélecteur de modèle |
| `GET /api/tools` | `{"servers": [...], "tools": [...]}`, même forme que `session_ready` | Barre d'état, `/tools` |
| `GET /api/memory` | `{"glossary": [...], "entities": [...], "relations": [...], "facts": [...]}`, colonnes de `memory.db` | Panneau mémoire |
| `GET /api/schema` | `{"tables": [{"name", "columns": [{"name", "type"}]}]}` | Affichage du schéma (facultatif) |
| `GET /api/config` | Configuration effective **sans chemins ni secrets** | Panneau de réglages |

- `supports_tools` vient de `POST /api/show` (champ `capabilities`) d'Ollama.
- En cas d'erreur : statut HTTP 5xx et `{"error": {"code", "message"}}`. Par exemple, `GET /api/models` répond 503 avec `ollama_unreachable` quand Ollama est arrêté.

---

## 10. Points ouverts à trancher avant le gel (mar 06/10, 12 h)

| # | Question | Contrat concerné | Avec | Proposition |
|---|---|---|---|---|
| P-1 | La fiche d'indicateurs affiche des **hypothèses**, mais `judge_report.v1` n'a pas de champ pour elles | `judge_report.v1.json` | P3 | Ajouter `assumptions: string[]` (3 au maximum) |
| P-2 | Le bloc SQL affiche la **mémoire appliquée** (acte 2), mais aucun événement ne dit quelles entrées ont été injectées | `events.md` | P1, P3 | Nouvel événement `context_injected {refs: [{kind, ref, label}]}` émis après `turn_started` |
| P-3 | Forme de `tool_result.data` pour `run_select` (tableau de résultats) | `events.md`, `tools.md` | P1, P2 | `{columns: string[], rows: any[][], row_count: int, truncated: bool}` |
| P-4 | Comment marquer une réponse interrompue par `cancel` ? | `events.md` | P1 | `message_done.interrupted: bool` (false par défaut) |
| P-5 | `turn_id` des erreurs hors tour et des actions mémoire lancées depuis l'interface | `events.md` | P1 | `turn_id` nullable pour `error` ; préfixe `ui-` pour les actions de l'interface |
| P-6 | Le moteur doit exposer une façon d'exécuter `memory_update` / `memory_delete` à la demande de l'interface, **en passant par la politique HITL** | `tools.md` (interface `Engine`) | P1, P3 | Méthode du type `Engine.run_ui_action(tool, arguments)` qui émet les mêmes événements qu'un appel d'outil |
| P-7 | Liste des réglages modifiables par `set_config` | `config.example.toml` | P1 | Tableau du §4.6 |
| P-8 | Valeurs de `kind` pour la mémoire | Schéma mémoire (MEM-01) | P3 | `glossary`, `entity`, `relation`, `fact` |
| P-9 | « Basic agent switching » (catalogue X1) : le changement de modèle suffit-il ? | — | Suivi | Question à poser au suivi ; `set_model` en attendant |

---

## 11. Suite (WEB-01, mar 06/10)

- Schémas JSON des messages dans `docs/contracts/schemas/` : `ws_client.v1.json` (messages du client) et `ws_server.v1.json` (messages propres au WS).
- Quatre fixtures dans `tests/fixtures/events/`, validées par P1 : `nominal.jsonl`, `hitl.jsonl`, `contradiction.jsonl`, `error.jsonl`. Elles alimentent le `MockEngine` (WEB-04).
- Tests de contrat dans `tests/unit/contracts/` : chaque ligne des fixtures est validée contre les schémas.

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P4 — propriétaire | | |
| P1 — moteur | | |
| P3 — mémoire | | |
