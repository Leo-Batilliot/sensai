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
