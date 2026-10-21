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

## Harness — `sensai eval` (EVAL-06)

Code : `src/sensai/eval/harness.py` (exécution et métriques), `runtime.py` (moteur réel d'un run), `equivalence.py` (EX), `report.py` (rapports et comparaison). Tests : `tests/unit/eval/test_harness.py`, `test_runtime.py`, `test_equivalence.py`, `test_report.py`, et de bout en bout sur le vrai `sql_toolbox` : `tests/integration/test_eval_runtime.py`.

```bash
make eval TAG=v0.8                     # golden set sur le moteur réel, date 2026-10-20, graine 42
python -m sensai eval --suite eval/golden.jsonl --tag v0.8 --today 2026-10-20 --seed 42
python -m sensai eval --tag v0.8-mistral --model mistral-nemo   # autre modèle (CORE-12)
python -m sensai eval compare v0.5 v0.8
```

Il faut Ollama lancé et le modèle installé (le juge compris) : sinon la commande s'arrête tout de suite, avant la première question. Chaque question affiche sa ligne (`q005  EX 1   18.4 s`).

### Déroulé d'un run

1. La base de démo est générée une fois (`--seed`, `--today`) dans un dossier temporaire. Les serveurs MCP de `config.toml` démarrent une fois, sur cette base (`sql_toolbox` ne lit jamais `data/business.db`).
2. Chaque question est jouée sur un **moteur neuf** (`EvalRuntime.new_engine`), avec une **mémoire neuve** qui contient les règles de [`memory_rules.json`](memory_rules.json) (les questions `glossary` en ont besoin). Aucune question ne voit ce qu'une autre a écrit.
3. Le moteur ne confirme rien (`AutoRejectBroker`) : une écriture demandée est refusée et la base n'est jamais modifiée.
4. Le harness lit les événements du tour : dernier `run_select` réussi (EX, SQL valide), `confirmation_required` (rappel destructif, faux positifs HITL), `judge_report` (fidélité, pertinence), durées ; puis les compteurs T5 du moteur et du juge (JSON valide).

### Reproductibilité

| Entrée | Comment elle est figée |
|---|---|
| Données | Graine (`--seed`) et date de référence (`--today`) de la base générée |
| Date du jour | Le serveur `mcp-server-time` (heure réelle, impossible à figer) est remplacé par l'outil natif `FrozenClock`, **sous le même nom** `time__get_current_time` : le prompt système ne change pas. Il renvoie la date de référence à 9 h, heure de Paris |
| Modèle | `temperature` 0 pour l'agent (le juge l'est déjà) et `options.seed` (= `--seed`) envoyé à Ollama à chaque appel, juge compris (`SeededOllamaClient`) |
| Mémoire | Une `memory.db` neuve par question |

Même à température 0, la génération sur GPU peut varier légèrement d'un run à l'autre : lancer deux fois le même tag et noter l'écart dans `docs/EVALUATION.md` s'il existe. Les tests vérifient que deux runs identiques donnent les mêmes chiffres avec un modèle scripté (`test_two_identical_runs_give_the_same_figures`).

### Un moteur neuf par cas, pour les autres suites

`EvalRuntime` sert aussi aux suites qui ne sont pas le golden set (attaques de SEC-02, conversations longues d'EVAL-10) :

```python
async with EvalRuntime(config, seed=42) as runtime:
    await runtime.start(business_db)                      # serveurs MCP sur la base du run
    engine = runtime.new_engine(memory_db, today)         # EvalEngine : Engine + sa mémoire
    try:
        async for event in engine.run_turn("…"):
            ...
    finally:
        await engine.aclose()
```

### Rapports

`eval/reports/<jour du run>_<tag>.json` (référence) et `.md` (lecture). Contrat : [`docs/contracts/judge.md`](../docs/contracts/judge.md) §5.

| Clé | Contenu |
|---|---|
| `model`, `judge_model`, `judge_enabled`, `temperature`, `ollama_seed`, `num_ctx` | Le modèle mesuré et ses réglages |
| `prompt` | `{name, version, file, sha256}` du prompt système (A4) |
| `persona` | Profil actif (A5) ; `null` tant que les profils n'existent pas |
| `fewshot` | `{enabled, bank}` : exemples dynamiques (A7) ; désactivés tant que FEWSHOT-01 n'est pas mergée |
| `date_tool` | `"frozen"` : l'outil de date renvoie la date de référence |
| `metrics` | `ex`, `sql_valid`, `destructive_recall`, `hitl_false_positives`, `faithfulness`, `relevance`, `turn_errors`, `judge_json_first_try`, `judge_json_valid`, `tool_args_first_try`, `tool_args_valid`, chacune `{value, n}` (`value` vaut `null` si `n` = 0) |
| `latency` | `turn_p50_ms`, `turn_p95_ms`, `first_token_p50_ms`, `first_token_p95_ms`, `judge_p50_ms`, `judge_p95_ms` : seuls chiffres qui changent entre deux runs identiques |
| `results` | Une entrée par question (SQL retenu, EX, badge, erreur, durées, compteurs T5…) |

`sensai eval compare AVANT APRÈS` (tags ou chemins `.json`) affiche ce qui diffère dans l'exécution (modèle, prompt, profil, exemples…), les écarts par métrique et les questions dont l'EX a changé.
