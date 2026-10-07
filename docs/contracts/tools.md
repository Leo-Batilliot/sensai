# Contrat — Outils, registry et confirmation (v1)

| | |
|---|---|
| **Tâche** | ALL-02 (atelier contrats). Implémenté par CORE-06 (boucle d'outils), MCP-04 (registry), HITL-01 et HITL-02 (politique, broker CLI), MEM-03 (outils mémoire) |
| **Propriétaire** | P1 — Core & Agent. La section §7 (`sql_toolbox`) appartient à P2 |
| **Consommateurs** | P2 (MCP, registry, HITL), P3 (outils mémoire), P4 (liste des outils, modale HITL) |
| **Statut** | Brouillon — à valider par P2, P3 et P4 |
| **Fait foi** | Ce document pour les interfaces Python et les codes d'erreur d'outil. Les événements restent définis par [`events.md`](events.md) |
| **Spécification** | PROJECT.md §6.3 (MCP), §6.5 (HITL), §7.5 (interfaces internes) |
| **Contrats liés** | [`events.md`](events.md) (P1), [`websocket.md`](websocket.md) (P4), [`memory.md`](memory.md) (P3, section « Outils mémoire » de ce contrat) |

Ce document fixe la frontière entre le moteur, qui décide quand appeler un outil et émet les événements, et les outils eux-mêmes : serveurs MCP, outils natifs de la mémoire, politique de confirmation.

---

## 1. Principes

1. **Le moteur ne connaît qu'un point d'entrée : le `ToolRegistry`.** Il ne parle jamais directement à un `McpClient`, au `McpManager` ou au `MemoryStore`.
2. **Outils MCP et outils natifs ont la même forme** (`ToolInfo`, `ToolResult`), pour que la validation des arguments (T5) et la politique HITL (A3) s'appliquent sans cas particulier (`memory.md` §1).
3. **Noms toujours préfixés par le serveur** (P-17) : `<serveur>__<outil>` (`sql_toolbox__run_select`, `time__get_current_time`). Les outils natifs n'ont pas de préfixe (`memory_delete`). Caractères `[A-Za-z0-9_-]`, 64 au maximum. Un nom de serveur ne contient jamais `__` (vérifié par la configuration, CORE-03).
4. **Une erreur d'outil n'est jamais une exception.** Le registry renvoie un `ToolResult` en échec, que le moteur transmet au modèle pour qu'il corrige. Ce n'est jamais un événement `error` (`events.md` §3.12). Seules les erreurs de programmation lèvent des exceptions.
5. **Les annotations sont des indices, pas des garanties** (spécification MCP). La politique HITL (§5) décide, et elle n'utilise jamais une information fournie par le modèle.
6. **Le moteur émet les événements, pas les outils.** Un outil renvoie un `ToolResult`, et le moteur en tire `tool_result` et, le cas échéant, `memory_updated`.

## 2. Vue d'ensemble

```text
                         Engine (P1, CORE-06)
                           │  boucle d'outils, événements
          ┌────────────────┼──────────────────────┬──────────────────────────┐
          ▼                ▼                      ▼                          ▼
   policy.evaluate   ToolRegistry (MCP-04)   Engine._confirm (CORE-05)   guard.classify
   (P2, HITL-01)     ├─ McpManager (P2)        └─ ConfirmationBroker      (P2, SEC-01)
                     │   └─ McpClient (MCP-02)      ├─ AutoRejectBroker (défaut)
                     │       └─ serveurs MCP         ├─ CLI (P2, HITL-02)
                     │          sql_toolbox, time…   └─ Web (P4, WEB-04)
                     └─ outils natifs (P3, MEM-03)
                         └─ MemoryStore (MEM-02)
```

| Module | Contenu | Responsable |
|---|---|---|
| `src/sensai/tools/base.py` | `ToolInfo`, `ToolResult`, `MemoryChange`, `ServerStatus`, protocoles `Tool` et `ToolRegistry` (§3) | P1 (CORE-06) |
| `src/sensai/tools/registry.py` | Le `ToolRegistry` réel : outils MCP et natifs | P2 (MCP-04), avec P1 |
| `src/sensai/tools/policy.py` | `evaluate()` (§5) | P2 (HITL-01) |
| `src/sensai/core/confirm.py` | `PendingAction`, `Decision`, `ConfirmationBroker` (§4) | P1 (CORE-05, livré) |
| `src/sensai/mcp/` | Client MCP, `ToolAnnotations` (§3.1) | P2 (MCP-02, livré) |
| `src/sensai/tools/memory_tools.py` | Les 7 outils mémoire | P3 (MEM-03) |
| `tests/fakes.py` | `FakeToolRegistry` pour les tests du moteur | P1 (CORE-06) |

## 3. Interfaces Python

### 3.1 `ToolAnnotations`

Définie une seule fois, dans `sensai.mcp.client` (MCP-02, déjà livrée), et utilisée aussi par les outils natifs.

| Champ | Annotation MCP | Valeur |
|---|---|---|
| `title` | `title` | Chaîne ou `None` |
| `read_only` | `readOnlyHint` | `True`, `False` ou **`None` si absente** |
| `destructive` | `destructiveHint` | idem |
| `idempotent` | `idempotentHint` | idem |
| `open_world` | `openWorldHint` | idem |

`None` veut dire « non déclarée », jamais « faux ». Les valeurs par défaut de la spécification MCP (`readOnlyHint` faux, `destructiveHint` vrai pour un outil sans annotation) sont appliquées par la politique (§5), qui doit distinguer « déclaré » et « absent ». C'est aussi ce qu'affiche l'interface : `read_only` et `destructive` sont `null` quand ils ne sont pas déclarés (P-15).

### 3.2 `ToolInfo`

Ce que le registry sait d'un outil : ce que voient la politique, le moteur et l'interface.

```python
@dataclass(frozen=True)
class ToolInfo:
    name: str                     # as seen by the model: "sql_toolbox__run_select", "memory_delete"
    server: str | None            # "sql_toolbox", "time"... None for a native tool
    original_name: str            # name on its server: "run_select" (same as name if native)
    description: str
    input_schema: dict[str, Any]  # JSON Schema of the arguments, as declared
    annotations: ToolAnnotations
    trusted: bool                 # native tool or trusted server (§5, point T-1)
```

### 3.3 `ToolResult`

Ce que renvoie tout appel d'outil, réussi ou non.

```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    text: str                          # what the model reads (role "tool" message)
    summary: str                       # short French text for the timeline: "5 lignes"
    data: dict[str, Any] | None = None # structured result (MCP structuredContent)
    error: ToolError | None = None     # sensai.core.events.ToolError(code, message)
    duration_ms: int = 0
    memory_change: MemoryChange | None = None  # memory writes only (§3.4)
```

Règles :
- `ok` vaut `True` si et seulement si `error` vaut `None`.
- `text` n'est jamais vide. En cas d'échec, c'est le message d'erreur destiné au modèle, pour qu'il corrige.
- `text` n'est **jamais envoyé à l'interface**. L'événement `tool_result` reprend `ok`, `summary`, `data`, `error` et `duration_ms` tels quels (`events.md` §3.8).
- `data` de `run_select` : `{columns, rows, row_count, truncated}` (P-3, §7.2).
- `duration_ms` vaut 0 pour un appel refusé avant exécution.

**Conversion d'un résultat MCP** (`McpCallResult`, MCP-02) par le registry :

| `McpCallResult` | `ToolResult` |
|---|---|
| blocs `text` | `text` : blocs joints par un saut de ligne ; un bloc d'un autre type devient « [contenu non textuel ignoré] » |
| `structuredContent` | `data` |
| `isError: true` | `ok=False`. Le code est `structuredContent.code` s'il est présent (nos serveurs, §7.3), sinon `TOOL_FAILED` |
| `summary` | `N lignes` si `data.row_count` existe (« N lignes · tronqué » si `truncated`), sinon le début de `text` (80 caractères au plus) |

### 3.4 `MemoryChange`

Renvoyé par un outil mémoire qui a réellement écrit. Le moteur en fait un événement `memory_updated` (`events.md` §3.9) juste après `tool_result`.

```python
@dataclass(frozen=True)
class MemoryChange:
    op: Literal["create", "update", "delete"]
    kind: Literal["glossary", "entity", "relation", "fact"]
    ref: str          # always a string (memory.md §1), never "id" (P-14)
    summary: str
```

Une opération `unchanged` (`memory.md` §3.2) donne `memory_change=None` : pas d'événement.

### 3.5 `Tool` (outils natifs)

Un outil natif (la mémoire, MEM-03) implémente ce protocole et est donné au registry à sa construction. Les outils MCP ne l'implémentent pas : le registry les enveloppe.

```python
class Tool(Protocol):
    name: str                      # no prefix
    description: str               # French, short, says when to use it
    input_schema: dict[str, Any]
    annotations: ToolAnnotations

    async def call(self, arguments: dict[str, Any]) -> ToolResult: ...
```

### 3.6 `ToolRegistry`

```python
class ToolRegistry(Protocol):
    def ollama_tools(self) -> list[dict[str, Any]]: ...
    def get(self, name: str) -> ToolInfo | None: ...
    def tools(self) -> list[ToolInfo]: ...
    def servers(self) -> list[ServerStatus]: ...
    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> list[str]: ...
    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


@dataclass(frozen=True)
class ServerStatus:
    name: str
    status: Literal["up", "down"]
    error: str | None    # French message shown as is, None when up
```

| Méthode | Contrat |
|---|---|
| `ollama_tools()` | Les outils **disponibles** au format d'Ollama : `{"type": "function", "function": {"name", "description", "parameters"}}`. `parameters` est l'`inputSchema` sans sa clé `$schema`. Les outils d'un serveur `down` en sont absents. |
| `get(name)` | `None` si le nom est inconnu |
| `tools()` | Tous les outils connus, pour `/tools`, `session_ready.tools` et `GET /api/tools` (§6) |
| `servers()` | L'état de chaque serveur de `[mcp.servers]`, pour la barre d'état (`session_ready.servers`) |
| `validate_arguments(name, arguments)` | Les erreurs de validation contre l'`inputSchema` (`jsonschema`, Draft 2020-12), 3 au plus, `[]` si valides (CORE-07) |
| `call(name, arguments)` | Route l'appel vers le bon serveur, sous le nom d'origine (`original_name`). **Ne lève jamais d'exception pour une erreur d'outil** : serveur arrêté, délai dépassé ou erreur du serveur donnent un `ToolResult` en échec (§8) |

`call()` ne valide pas les arguments et n'applique pas la politique : c'est le moteur qui le fait avant (§4). Un registry ne s'utilise que depuis la boucle d'événements du moteur.

## 4. Un appel d'outil dans le moteur (CORE-06)

Pour chaque appel demandé par le modèle (`message.tool_calls`), dans cet ordre :

| # | Étape | Si ça échoue | Événements |
|---|---|---|---|
| 1 | `call_id = new_id()`, `info = registry.get(name)` | `info is None` → échec `UNKNOWN_TOOL` | `tool_call` (avec `server: null`), puis `tool_result` |
| 2 | Même outil et mêmes arguments que l'appel précédent ? | oui → échec `DUPLICATE_CALL`, sans exécution | `tool_call`, `tool_result` |
| 3 | `registry.validate_arguments(...)` | erreurs → échec `INVALID_ARGUMENTS`, le modèle a droit à une réparation | `tool_call`, `tool_result` |
| 4 | Émettre l'appel ; si les arguments contiennent `sql`, classer la requête (`guard.classify`) | — | `tool_call`, puis `sql_generated` |
| 5 | `policy.evaluate(info, arguments, config.hitl)` | `deny` → échec `TOOL_DENIED` (ou `SQL_FORBIDDEN` pour une écriture passée à `run_select`) | `tool_result` |
| 6 | Si `confirm` : calculer l'aperçu (`sql_toolbox__preview_write` pour du SQL, aperçu du `MemoryStore` pour `memory_delete`), construire un `PendingAction`, attendre `Engine._confirm()` | refus ou délai → échec `ACTION_REJECTED`, sans exécution | `confirmation_required`, `confirmation_resolved` |
| 7 | `result = await registry.call(name, arguments)` | `result.ok` faux → transmis tel quel au modèle | `tool_result`, puis `memory_updated` si `result.memory_change` |
| 8 | Ajouter `{"role": "tool", "tool_name": name, "content": result.text}` aux messages et rappeler le modèle | — | — |

- Un nom qui ne respecte pas `[A-Za-z0-9_-]{1,64}` (modèle qui invente) est émis comme `"unknown_tool"` dans `tool_call.tool`, et l'étape 1 échoue avec `UNKNOWN_TOOL`.
- Chaque `tool_call` reçoit exactement un `tool_result`, sauf si le tour se termine par un `error` (`events.md` §4.1).
- Après un refus, le message pour le modèle dit : « Action refusée par l'utilisateur. Ne la retente pas ; explique-le dans ta réponse. » (PROJECT §6.5).
- Le résultat d'un outil de données est précédé d'une ligne qui indique que ce sont des données, pas des instructions (protection contre l'injection, SEC-02).
- Les actions lancées depuis l'interface (`Engine.run_ui_action`, P-6) suivent les étapes 1 et 3 à 8, avec la liste blanche `memory_update` et `memory_delete`. Hors liste, l'échec est `UI_ACTION_FORBIDDEN` (`events.md` §5.1).

## 5. Politique de confirmation (HITL-01, P2)

```python
@dataclass(frozen=True)
class PolicyDecision:
    action: Literal["allow", "confirm", "deny"]
    reasons: list[str]          # French, logged, shown in the confirmation panel


def evaluate(tool: ToolInfo, arguments: dict[str, Any], config: HitlConfig) -> PolicyDecision: ...
```

- Déterministe, sans appel au modèle, la règle la plus stricte l'emporte. Les règles elles-mêmes sont celles de la fiche HITL-01 (P2) : `[hitl] deny` → `deny` ; écriture passée à `run_select` → `deny` ; `[hitl] always_confirm`, SQL classé écriture ou `destructiveHint` → `confirm` ; serveur non fiable sans `readOnlyHint: true` explicite → `confirm` ; outil fiable avec `readOnlyHint: false` et `destructiveHint: false` explicites → `allow` (outils mémoire additifs) ; `readOnlyHint: true` → `allow` ; tout autre cas → `confirm`.
- **Outil fiable** (`ToolInfo.trusted`) : outil natif, ou outil d'un serveur de la liste de confiance, `sql_toolbox` en v1. Point ouvert T-1.
- `requires_confirmation`, affiché par l'interface (P-15, §9), n'est pas un champ de `ToolInfo` : il est calculé par `evaluate(tool, {}, config).action == "confirm"`, la décision sans arguments. Un outil dont la décision dépend du SQL (`run_select`, `preview_write`) est donc affiché sans cadenas.

## 6. Confirmation : `PendingAction`, `Decision`, `ConfirmationBroker`

Livrés par CORE-05 dans `sensai.core.confirm`. Rappel des règles :

| Élément | Contrat |
|---|---|
| `PendingAction` | `action_id`, `call_id`, `tool`, `sql`, `impact`, `expires_in_s` : exactement les champs de `confirmation_required` (`events.md` §3.6). `impact = {operation, rows_affected, per_table, sample, blocked}` (§7.4) |
| `Decision` | `decision` (`approve`, `reject`, `timeout`), `reason` (`user`, `cancelled`, `disconnected`, `timeout`), `comment`. Les couples interdits par `events.md` §3.7 lèvent `ValueError` à la création |
| `ConfirmationBroker.request(action)` | Renvoie la décision d'un humain. **N'applique pas de délai** : c'est `Engine._confirm` qui borne l'attente à `expires_in_s` et fabrique `Decision("timeout", "timeout")` |
| Annulation | `cancel`, `reset` ou déconnexion : le broker résout lui-même les demandes en attente avec `reject` et la raison `cancelled` ou `disconnected` (voir `WebConfirmationBroker.reject_all`) |

| Broker | Où | Comportement |
|---|---|---|
| `AutoRejectBroker` | `sensai.core.confirm` | Défaut d'un `Engine` créé sans broker : refuse tout, immédiatement (`reason` `cancelled`, point T-3) |
| `CliConfirmationBroker` | `sensai.cli.confirm` (HITL-02) | Panneau `rich`, invite « Confirmer ? [o/N] », lecture du clavier dans un thread |
| `WebConfirmationBroker` | `sensai.web.websocket.confirm` (WEB-04, livré) | Une `Future` par `action_id`, résolue par le message WebSocket `confirm` |

**Interblocage** : pendant `request()`, le moteur est en pause au milieu de son générateur, et le code qui consomme ce générateur attend l'événement suivant. La décision doit donc venir d'**une autre tâche** : la boucle de réception du WebSocket, ou le thread qui lit le clavier. Résoudre le broker depuis le code qui consomme les événements bloque le programme.

## 7. Outils de `sql_toolbox` (propriétaire : P2)

> Brouillon pré-rempli par P1 à partir des fiches MCP-03, MCP-05, SEC-01 et des décisions P-3, P-16, P-17. **À valider et compléter par P2** : descriptions vues par le modèle, `inputSchema` et `outputSchema` exacts.

### 7.1 Outils

| Outil (nom vu par le modèle) | Arguments | `data` renvoyé | Annotations | Tâche |
|---|---|---|---|---|
| `sql_toolbox__list_tables` | — | `{tables: [{name, description}]}` | `readOnlyHint: true` | MCP-03 |
| `sql_toolbox__describe_table` | `table` | colonnes, types, clés étrangères, 3 lignes d'exemple, valeurs des colonnes de statut | `readOnlyHint: true` | MCP-03 |
| `sql_toolbox__run_select` | `sql`, `purpose` | `{columns, rows, row_count, truncated}` (§7.2) | `readOnlyHint: true` | MCP-03 |
| `sql_toolbox__preview_write` | `sql` | aperçu d'impact (§7.4) | `readOnlyHint: true` | MCP-05 |
| `sql_toolbox__execute_write` | `sql` | `{operation, rows_affected, per_table}` | `readOnlyHint: false`, `destructiveHint: true` | MCP-05 |

### 7.2 Résultat de `run_select` (P-3)

- `rows` : liste de listes, dans l'ordre de `columns` ; valeurs JSON scalaires (nombre, chaîne, `null`).
- `row_count` : nombre de lignes **présentes dans `rows`**, pas le total.
- `truncated: true` : il y avait plus de `[database] row_limit` lignes (le serveur en lit `row_limit + 1`).
- `content` contient le même JSON en texte compact, et `structuredContent` les données.

### 7.3 Erreurs de nos serveurs

Une erreur d'exécution est un résultat `isError: true` dont le `structuredContent` vaut `{code, message}`, avec un code du §8 (`SQL_ERROR`, `SQL_FORBIDDEN`, `TOOL_TIMEOUT`). Le texte (`content`) reprend le message, que le modèle utilise pour se corriger (CORE-09). Un outil inconnu ou des paramètres invalides donnent une erreur JSON-RPC `-32602`.

### 7.4 Aperçu d'impact (`preview_write`) → `confirmation_required.impact`

| Champ | Contenu |
|---|---|
| `operation` | `INSERT`, `UPDATE` ou `DELETE` (garde SQL) |
| `rows_affected` | Lignes visées par l'instruction |
| `per_table` | Différences signées de `COUNT(*)` par table, cascades incluses ; nulles pour un `UPDATE` |
| `sample` | Jusqu'à 5 lignes visées ; `[]` en v1 (HITL-03) |
| `blocked` | `[{reason, count}]` : contraintes qui font rejeter toute l'instruction (`ON DELETE RESTRICT`) ; `count` vaut `null` en v1 (P-16) ; `rows_affected` vaut alors 0 et `per_table` `{}` |

L'aperçu s'exécute dans une transaction **toujours annulée** (`ROLLBACK`). La base est identique avant et après.

## 8. Codes d'erreur des outils (`ToolResult.error.code`)

Ce tableau fait référence pour `tool_result.error.code` (`events.md` §5.2, point E-3). Ces codes ne sont **jamais** des codes d'événement `error`.

| Code | Posé par | Quand |
|---|---|---|
| `UNKNOWN_TOOL` | moteur | Le modèle demande un outil qui n'existe pas (étape 1) |
| `DUPLICATE_CALL` | moteur | Même outil, mêmes arguments que l'appel précédent : non exécuté |
| `INVALID_ARGUMENTS` | moteur (`validate_arguments`) | Arguments non conformes à l'`inputSchema` ; une réparation autorisée |
| `TOOL_DENIED` | moteur (politique) | `deny` de la politique |
| `ACTION_REJECTED` | moteur (HITL) | Confirmation refusée, expirée ou interrompue |
| `MCP_SERVER_UNAVAILABLE` | registry | Serveur arrêté, absent ou hors protocole, après une tentative de redémarrage |
| `TOOL_TIMEOUT` | registry, `sql_toolbox` | Pas de réponse à temps, ou requête SQL trop longue |
| `TOOL_FAILED` | registry | Échec signalé par un serveur sans code plus précis |
| `SQL_ERROR` | `sql_toolbox` | Erreur SQLite, renvoyée au modèle pour auto-correction (`max_sql_retries`) |
| `SQL_FORBIDDEN` | `sql_toolbox`, politique | Requête refusée par la garde SQL |

Les codes propres aux outils mémoire restent ceux de `memory.md` §3.4, en minuscules (`not_found`, `entity_not_found`, `ambiguous_entity`, `invalid_patch`, `conflict`).

**Traduction des exceptions du client MCP (MCP-02) par le registry :**

| Exception (`sensai.core.errors`, `sensai.mcp.jsonrpc`) | Code |
|---|---|
| `McpCommandNotFound`, `McpServerDied`, `McpProtocolError` | `MCP_SERVER_UNAVAILABLE` |
| `McpTimeout` | `TOOL_TIMEOUT` |
| `JsonRpcError` | `TOOL_FAILED` |

## 9. Ce que voit l'interface

`session_ready.tools`, `GET /api/tools` et `/tools` en CLI (`websocket.md` §5.2, P-15), construits à partir de `registry.tools()` et `registry.servers()` :

| Champ | Source |
|---|---|
| `tools[].name`, `server`, `description` | `ToolInfo` |
| `tools[].read_only`, `destructive` | `ToolInfo.annotations`, `null` si non déclarées |
| `tools[].requires_confirmation` | Politique (§5), seul champ affiché par le front |
| `servers[].name`, `status`, `error` | `ServerStatus` |

## 10. Écarts avec PROJECT.md et les fiches

| Source | Ce contrat | Raison |
|---|---|---|
| PROJECT §6.3 : préfixe « en cas de collision » | Toujours préfixé | Noms stables quand on ajoute un serveur (P-17) |
| PROJECT §7.5 : `ToolResult` non détaillé | §3.3 | Champs utilisés par le moteur, les événements et l'interface |
| MEM-03 : `memory_change` avec `id` | `ref` | Même nom partout (P-14) |
| MCP-02 : exceptions `Mcp*` | Traduites en codes de `tool_result` (§8) | Une panne d'outil n'arrête pas le tour (point E-3 d'`events.md`) |
| PROJECT §7.5 : `Engine(config, registry, broker, memory, judge)` | Paramètres ajoutés par les tâches qui les livrent | CORE-05 ne prend que `config`, `client`, `broker` et `session_id` |

## 11. Points ouverts

| # | Sujet | Avec |
|---|---|---|
| T-1 | Serveurs de confiance (ALL-02, question 7) : `sql_toolbox` et les outils natifs en v1. À confirmer, et à noter dans `docs/decisions.md` | P2 |
| T-2 | Ajouter `UNKNOWN_TOOL` à `events.md` §5.2, et y renvoyer vers le §8 de ce document | P1 |
| T-3 | Valeur de `reason` pour un refus automatique (`AutoRejectBroker`) : proposer `"auto"` dans `events.md` §3.7 ; `"cancelled"` en attendant | P1, P4 |
| T-4 | Aperçu d'une suppression en mémoire (cascades de `memory_delete`) : méthode du `MemoryStore` qui renvoie un `impact` (§7.4) | P3 |
| T-5 | Section §7 à valider et compléter (descriptions, schémas, `outputSchema`) | P2 |

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P1 — propriétaire | | |
| P2 — MCP, registry, HITL | | |
| P3 — outils mémoire | | |
| P4 — interface | | |
