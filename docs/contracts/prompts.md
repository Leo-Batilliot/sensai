# Contrat — Prompts versionnés (A4) et exemples dynamiques (A7)

| | |
|---|---|
| **Tâches** | PROMPT-01 (A4), FEWSHOT-01 (A7), CORE-14 (prompt v2, P1) |
| **Propriétaire** | P3 — Mémoire & Évaluation (avec P1 pour le contenu des prompts) |
| **Consommateurs** | P1 (moteur, configuration, événements, CLI), P4 (`set_config`, `GET /api/prompts`, timeline), P3 (harness) |
| **Statut** | v1.1 — reporté de [`v1.1-extensions.md`](v1.1-extensions.md) §3.5 et §3.6 (décision [D-06](../decisions.md)) ; gel le **mer 21/10 à 18 h** |
| **Fait foi** | `src/sensai/core/prompts/registry.toml` pour les versions (PROMPT-01), [`schemas/example.v1.json`](schemas/example.v1.json) pour la banque d'exemples |
| **Spécification** | PROJECT.md §6.11 (A4), §6.16 (A7) |

---

## 1. Registre des prompts (A4)

### 1.1 `src/sensai/core/prompts/registry.toml`

Une entrée `[[prompt]]` par version publiée, données du paquet (lues avec `importlib.resources`, comme les prompts eux-mêmes).

```toml
[[prompt]]
name = "system_sql"
version = "v1"
file = "system_sql_v1.md"
created = "2026-10-11"
parent = ""
sha256 = "…"                      # empreinte du fichier : toute modification impose une nouvelle version
notes = "Mid-term : schéma compact, règles de dates SQLite, outil de date."

[[prompt]]
name = "system_sql"
version = "v2"
file = "system_sql_v2.md"
created = "2026-10-25"
parent = "v1"
sha256 = "…"
notes = "Hypothèses explicites, refus hors périmètre, $persona, $examples, plan de la note (M4)."
```

| Champ | Type | Règle |
|---|---|---|
| `name` | chaîne | Famille du prompt : `system_sql` (le juge garde son prompt `judge.md`, hors registre en v1.1) |
| `version` | chaîne | `v` + entier, unique par `name`, jamais réutilisée |
| `file` | chaîne | Fichier de `core/prompts/`, nommé `<name>_<version>.md` |
| `created` | date ISO | Jour de publication |
| `parent` | chaîne | Version dont celle-ci dérive, `""` pour la première |
| `sha256` | chaîne | SHA-256 hexadécimal des **octets** du fichier |
| `notes` | chaîne | Ce qui change par rapport au parent, en une phrase |

**Invariants, vérifiés par un test (PROMPT-01)**
1. **Une version publiée ne change jamais** : `sha256` égale l'empreinte du fichier. Corriger une coquille, c'est publier `v3`.
2. Chaque `file` existe, chaque `parent` non vide existe dans la même famille.
3. Les trous `$nom` d'un fichier sont exactement ceux que le moteur sait remplir pour cette version (`core/prompts.py`, règle « chaque trou a une valeur et chaque valeur un trou »). v1 : `$schema`, `$tools_hint`, `$date_rule`, `$memory`. v2 ajoute `$persona`, `$examples` et `$document_outline`.

### 1.2 Choisir une version

- `[agent] prompt_version` (P1, `config.example.toml`), défaut `"v2"` une fois CORE-14 mergée ; `"v1"` est le retour arrière.
- Variable `SENSAI_AGENT_PROMPT_VERSION`, option `--prompt-version` de `sensai chat` et `sensai eval`, clé `prompt_version` de `set_config` (P4, `websocket.md` §4.6).
- Version absente du registre : `PROMPT_VERSION_NOT_FOUND` (P1, `events.md` §5.1), au démarrage ou en réponse à `set_config` ; la session garde sa version.
- `sensai prompts` liste le registre (version courante marquée) ; `GET /api/prompts` renvoie `{"current": "v2", "versions": [{name, version, created, parent, sha256, notes}]}` (P4).

### 1.3 Ce que les autres enregistrent

- **Harness** : chaque rapport contient `prompt: {name, version, file, sha256}` ([`judge.md`](judge.md) §5). Tant que le registre n'existe pas, `version` est lue dans le nom du fichier (`system_sql_v1.md` → `v1`) ; ensuite, elle vient du registre et le harness vérifie l'empreinte.
- **WebSocket** : `session_ready.prompt_version` (P4).

## 2. Exemples dynamiques (A7)

### 2.1 Banque d'exemples

`src/sensai/data/examples.jsonl` (données du paquet, versionnées), une ligne par exemple, **séparée du golden set** (`eval/golden.jsonl`). Format : [`schemas/example.v1.json`](schemas/example.v1.json).

```json
{"id": "ex-07", "question": "Quel est le chiffre d'affaires HT par commercial le mois dernier ?", "sql": "SELECT r.full_name, ROUND(SUM(i.amount_ht), 2) AS ca_ht FROM invoices i JOIN clients c ON c.id = i.client_id JOIN sales_reps r ON r.id = c.sales_rep_id WHERE i.issued_on >= date(:today, 'start of month', '-1 month') AND i.issued_on < date(:today, 'start of month') AND i.status <> 'cancelled' GROUP BY r.full_name ORDER BY ca_ht DESC", "tags": ["jointure", "filtre_temporel", "commercial"]}
```

| Champ | Type | Règle |
|---|---|---|
| `id` | chaîne | `ex-` + 2 chiffres ou plus, unique, jamais réutilisé |
| `question` | chaîne | Telle qu'un utilisateur la tape, en français |
| `sql` | chaîne | Un seul `SELECT` (ou `WITH … SELECT`) exécutable sur la base de démo, sans `;` final |
| `tags` | liste de chaînes | 1 à 6 mots-clés en snake_case, sans accents (`jointure`, `top_n`, `glossaire`…) |

- **Dates relatives** : le paramètre `:today`, jamais `date('now')` (qui ne suit pas la date de référence de la démo). L'injection le laisse tel quel avec la consigne « remplace `:today` par la date renvoyée par l'outil de date » (même règle que le prompt v1).
- **Test anti-fuite** (FEWSHOT-01) : aucune `question` de la banque n'est une question du golden set, et aucun `sql` n'est un `gold_sql`, à la normalisation près (casse, espaces, alias de tables). Sinon l'EX du harness serait gonflée par la réponse donnée en exemple.
- Chaque `sql` s'exécute sans erreur sur la base de démo (graine 42, date de référence 2026-10-20), avec `:today` lié comme dans le golden set.

### 2.2 Sélection et injection

- Index FTS5 en mémoire (bm25) sur `question` et `tags`, construit au démarrage ; requête construite comme pour la mémoire (`memory.md` §3.3 : le texte brut n'atteint jamais `MATCH`).
- Au plus `[fewshot] k` exemples (défaut 3), de score au moins `[fewshot] min_score` (défaut 0 : toujours les `k` meilleurs). Aucun exemple au-dessus du seuil : rien n'est injecté, pas d'événement.
- `[fewshot] enabled = false` (ou `set_config` `fewshot_enabled: false`, P4) désactive l'injection : c'est le témoin de la comparaison avec et sans exemples (EVAL-08).
- Les exemples remplissent le trou `$examples` du prompt v2 ; avec le prompt v1, qui n'a pas ce trou, rien n'est injecté.
- Le moteur émet **`examples_injected {examples: [{id, question, score}]}`** (point V-3 : un événement à part, `context_injected` restant réservé à la mémoire et à son ablation), après `context_injected` (et `context_compacted` s'il y en a un), avant tout `token`, `thinking` ou `tool_call`, au plus une fois par tour. Événement de P1 (`events.md`) ; `score` est le score bm25 ramené à un nombre positif, du plus proche au moins proche.

### 2.3 Ce que le harness enregistre

`fewshot: {enabled, bank}` dans chaque rapport ([`judge.md`](judge.md) §5) : `bank` est le fichier de la banque (`examples.jsonl`) quand les exemples sont actifs, `null` sinon.

## 3. Points tranchés et points ouverts

| # | Sujet | Statut |
|---|---|---|
| V-3 (`v1.1-extensions.md`) | `examples_injected` à part ou dans `context_injected` | Proposition retenue : à part (§2.2), à confirmer à ALL-15 |
| PR-1 | Le prompt du juge (`judge.md`) entre-t-il dans le registre ? | Non en v1.1 : il ne change pas pendant l'extension. À reconsidérer s'il évolue |
| PR-2 | Version par défaut tant que CORE-14 n'est pas mergée | `"v1"` : la clé `[agent] prompt_version` arrive avec PROMPT-01, sa valeur par défaut passe à `"v2"` avec CORE-14 |

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P3 — propriétaire | | |
| P1 — moteur, configuration, événements | | |
| P4 — interface | | |
