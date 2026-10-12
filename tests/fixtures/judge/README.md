# Cas du juge

Réponses à juger, dont des erreurs injectées volontairement (EVAL-03, PLANNING §4.2). Données de l'acte 1 (PROJECT §8.3), mêmes lignes que `tests/unit/eval/test_numeric_check.py`. Testés par `tests/unit/eval/test_judge.py`.

Une ligne = un cas, tous les champs obligatoires :

| Champ | Contenu |
|---|---|
| `id`, `description` | Identifiant `j` + 2 chiffres, ce que le cas vérifie |
| `question`, `sql`, `columns`, `rows`, `truncated` | Ce que le juge reçoit : la question et le dernier `run_select` réussi du tour (`tools.md` §7.2) |
| `answer` | La réponse de l'agent à vérifier |
| `injected_error` | L'erreur injectée, ou `null` pour une réponse sans erreur |
| `detected_by` | Qui doit attraper l'erreur : `numeric` (contrôle numérique, sans modèle) et/ou `judge` (modèle) |
| `expected_badge` | Badge attendu (`judge.md` §2) |
| `stub_report` | Le rapport qu'un juge correct renvoie, conforme à `judge_report.v1.json` : sert de réponse du modèle simulé en CI |

| Cas | Erreur injectée | Attrapée par |
|---|---|---|
| j01 | aucune | badge `verified` |
| j02 | marge inventée | contrôle numérique + juge |
| j03 | délai faux | contrôle numérique + juge |
| j04 | total faux | contrôle numérique + juge |
| j05 | clients intervertis (chiffres justes) | juge seul |
| j06 | délai pris sur une autre ligne pour un client sans paiement | juge seul |
| j07 | comparaison fausse | juge seul |
| j08 | aucune, affirmation invérifiable | badge `needs_review` |
| j09 | aucune, résultat tronqué | badge `needs_review` |

En CI, le modèle est simulé par `stub_report` : les tests prouvent que le contrôle numérique attrape seul j02 à j04, et que le badge suit les verdicts. Que le **vrai** modèle attrape aussi j05 à j07 se vérifie avec Ollama lancé :

```bash
make test-all   # ou : .venv/bin/python -m pytest -m ollama tests/unit/eval/test_judge.py
```
