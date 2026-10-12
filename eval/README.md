# Golden set — `eval/golden.jsonl`

Jeu de référence du harness d'évaluation (EV1, PROJECT §11.1). Métriques et règle d'équivalence des résultats : [`docs/contracts/judge.md`](../docs/contracts/judge.md) §3.
Format vérifié par `tests/unit/eval/test_golden.py`.

| Version | Tâche | Entrées |
|---|---|---:|
| v0 | EVAL-02 | 15 |
| v1 | EVAL-02b | 25 |
| v2 | EVAL-02c (SQL rédigés par P2, relus par P3) | 40 |

## Format

Une question par ligne, tous les champs obligatoires :

| Champ | Type | Contenu |
|---|---|---|
| `id` | chaîne | `q` + 3 chiffres, unique, jamais réutilisé |
| `category` | chaîne | Voir ci-dessous |
| `question` | chaîne | Telle qu'un utilisateur la tape |
| `gold_sql` | chaîne ou `null` | SQL de référence ; `null` pour `out_of_scope` et `adversarial` |
| `expects_write` | booléen | La question demande une écriture : `confirmation_required` attendu avant toute écriture |
| `requires_memory` | booléen | La réponse dépend d'une règle du glossaire (catégorie `glossary`) |
| `notes` | chaîne | Règle mémorisée, motif de données utilisé (D1…D9 de `docs/keynote/scenario.md`), comportement attendu sans SQL |

Catégories : `aggregate`, `join`, `time_filter`, `top_n`, `glossary`, `write`, `out_of_scope`, `adversarial`.

## Paramètres de date

Les dates relatives (« ce trimestre », « le mois dernier ») sont des paramètres nommés, calculés à partir de la date de référence (`--today`, 2026-10-20 par défaut pour l'évaluation) :

| Paramètre | Valeur pour 2026-10-20 |
|---|---|
| `:today` | `2026-10-20` |
| `:m_start` | `2026-10-01` (1er jour du mois) |
| `:prev_m_start` | `2026-09-01` (1er jour du mois précédent) |
| `:q_start` | `2026-10-01` (1er jour du trimestre) |
| `:prev_q_start` | `2026-07-01` (1er jour du trimestre précédent) |
| `:y_start` | `2026-01-01` (1er jour de l'année) |

## Écrire un SQL de référence

- **Colonnes minimales** : seulement ce que la question demande. L'équivalence (EX) ignore les colonnes en plus chez l'agent, mais exige chaque colonne de la référence.
- **`ORDER BY` seulement si l'ordre fait partie de la réponse** (top N, classement) : sinon les lignes sont comparées en multiensemble.
- Montants arrondis avec `ROUND(x, 2)`, délais avec `ROUND(x, 1)`.
- Une écriture (`write`) donne l'instruction attendue ; elle n'entre pas dans EX, mais sert à vérifier l'aperçu d'impact (nombre de lignes dans `notes`).
- Chaque référence doit donner des lignes sur la base de démo (graine 42, date de référence 2026-10-20), et un top N ne doit pas avoir d'égalité au dernier rang : vérifié par `test_gold_sql_gives_rows_on_the_demo_data` et `test_no_tie_at_the_last_rank_of_a_top_n` (`tests/unit/eval/test_golden.py`).

## Harness — `sensai eval` (EVAL-06a)

Code : `src/sensai/eval/harness.py` (exécution et métriques), `equivalence.py` (EX), `report.py` (rapports et comparaison). Tests : `tests/unit/eval/test_harness.py`, `test_equivalence.py`, `test_report.py`.

### Déroulé d'un run

1. La base de démo est générée une fois (`--seed`, `--today`) dans un dossier temporaire.
2. Chaque question est jouée sur un **moteur neuf**, avec une **mémoire neuve** qui contient les règles de [`memory_rules.json`](memory_rules.json) (les questions `glossary` en ont besoin). Aucune question ne voit ce qu'une autre a écrit.
3. Le moteur ne confirme rien (`AutoRejectBroker`) : une écriture demandée est refusée et la base n'est jamais modifiée.
4. Le harness lit les événements du tour : dernier `run_select` réussi (EX, SQL valide), `confirmation_required` (rappel destructif, faux positifs HITL), `judge_report` (fidélité, pertinence), durées.

Le moteur est fourni par une `EngineFactory` (question, `RunSetup`). Celle du vrai moteur arrive avec le registry MCP (MCP-04) et l'assemblage du moteur (CORE-10). D'ici là, `sensai eval --tag …` affiche ce qu'il attend. L'outil de date doit renvoyer la date de référence (`RunSetup.today`) : le vrai `mcp-server-time` donne l'heure réelle et ne peut pas être figé, donc un run avec lui n'est pas reproductible sur les questions `time_filter`.

### Rapports

`eval/reports/<jour du run>_<tag>.json` (référence) et `.md` (lecture) :

| Clé | Contenu |
|---|---|
| `metrics` | `ex`, `sql_valid`, `destructive_recall`, `hitl_false_positives`, `faithfulness`, `relevance`, `turn_errors`, chacune `{value, n}` (`value` vaut `null` si `n` = 0) |
| `latency` | `turn_p50_ms`, `turn_p95_ms`, `first_token_p50_ms`, `judge_p50_ms` : seuls chiffres qui changent entre deux runs identiques |
| `results` | Une entrée par question (SQL retenu, EX, badge, erreur, durées…) |

`sensai eval compare AVANT APRÈS` (tags ou chemins `.json`) affiche les écarts par métrique et les questions dont l'EX a changé.
