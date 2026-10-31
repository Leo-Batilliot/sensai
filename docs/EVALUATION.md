# Évaluation de SENSAI

| | |
|---|---|
| **Statut** | Brouillon (DOC-07). Les chiffres marqués *à mesurer* viennent des runs sur la machine de démo (EVAL-08, EVAL-09, EVAL-10, CORE-18) |
| **Propriétaire** | P3 (relu par P1) |
| **Détail technique** | [`eval/README.md`](../eval/README.md) (format des jeux, rapports), [`docs/contracts/judge.md`](contracts/judge.md) (métriques, juge) |

## 1. Méthode

- **Golden set** : `eval/golden.jsonl`, 25 questions (40 avec EVAL-02c) en 8 catégories : `aggregate`, `join`, `time_filter`, `top_n`, `glossary`, `write`, `out_of_scope`, `adversarial`. Chaque question a son SQL de référence, sauf hors périmètre et attaques.
- **Entrées figées** : base de démo générée par graine (42) et date de référence (2026-10-20) ; outil de date remplacé par une horloge figée sous le même nom ; température 0 et `options.seed` envoyé à Ollama, juge compris.
- **Isolation** : un moteur neuf et une mémoire neuve par question ; la mémoire ne contient que les règles de `eval/memory_rules.json`. Le moteur ne confirme rien : la base n'est jamais modifiée.
- **Traçabilité** : chaque rapport enregistre le modèle, le juge, la version et l'empreinte du prompt (A4), l'état des exemples dynamiques (A7), la mémoire (M3), la graine et la date.

## 2. Métriques

| Métrique | Ce qu'elle mesure | Population |
|---|---|---|
| EX | Le résultat du dernier `run_select` équivaut à celui du SQL de référence | Questions avec SQL de référence en lecture |
| SQL valide | Au moins une requête a abouti | Idem |
| Rappel destructif | Aucune écriture sans confirmation | Questions d'écriture et attaques |
| Faux positifs HITL | Confirmation demandée sans raison | Questions en lecture |
| Fidélité, pertinence | Score du juge (affirmations soutenues, pertinence 1 à 5) | Réponses jugées |
| JSON valide | Rapports du juge et arguments d'outils valides, au 1er essai et après réparation | Tous les appels |
| Latence | p50 / p95 du tour, du premier token et du juge | Toutes les questions |
| Tokens | Prompt et réponse du dernier appel du tour, en moyenne | Toutes les questions |

Définitions exactes : [`judge.md`](contracts/judge.md) §3.

## 3. Résultats et comparaisons (EVAL-08, EVAL-09)

*À mesurer* sur `v1.0.0-rc1`, avec le modèle final (CORE-12). Chaque ligne est un run du harness, comparé au témoin par `sensai eval compare`.

| Comparaison | Témoin | Variante | Ce qui change |
|---|---|---|---|
| v0.5 / v1.0 | `v0.5.0` (worktree) | `v1.0-rc` | Tout le produit |
| Prompt v1 / v2 (A4) | `--prompt-version v1` | `--prompt-version v2` | Prompt système (CORE-14) |
| Avec / sans exemples (A7) | `--no-fewshot` | défaut | Exemples dynamiques, prompt v2 |
| Avec / sans mémoire (M3) | `--no-memory` | défaut | Règles du glossaire, questions `glossary` |
| Juge seul / juge + contrôle numérique | §4 | §4 | Détecteur |

Les écarts sont à lire avec la limite du §8 : sur 25 à 40 questions, une question vaut 2,5 à 4 points d'EX.

## 4. Calibration du juge (EVAL-07)

Jeu : `eval/judge_calibration.jsonl`, 20 réponses étiquetées affirmation par affirmation à partir des vraies lignes de la base de démo : 7 justes, 8 chiffres faux, 3 entités interverties, 2 affirmations sans source. Une réponse est fausse (classe positive) si l'une de ses affirmations est contredite : 11 réponses sur 20.

| Détecteur | Précision | Rappel | VP | FP | FN | VN |
|---|---:|---:|---:|---:|---:|---:|
| Contrôle numérique seul | 1,00 | 0,64 | 7 | 0 | 4 | 9 |
| Juge seul | *à mesurer* | | | | | |
| Juge + contrôle numérique | *à mesurer* | | | | | |

Le contrôle numérique ne dépend d'aucun modèle : ces chiffres sont identiques sur toute machine (`sensai eval calibrate --numeric-only`, rapport `eval/reports/2026-10-27_calibration-numeric.md`).

**Ce que le contrôle numérique manque, et pourquoi**
- **Entités interverties** (c04, c10, c19) : tous les chiffres de la réponse existent dans les lignes, mais rattachés au mauvais client, commercial ou secteur. Seul le juge peut les voir.
- **Chiffre faux proche d'une autre valeur** (c18) : 104 940,48 € au lieu de 140 940,48 € pour la restauration ; or l'hôtellerie vaut 105 588,34 €, à moins de 1 % : la tolérance s'applique à toutes les cellules du résultat, pas à la bonne.
- **Bruit relevé** (c13) : le « 12 » du nom de produit « Intercalaires A4 12 positions » est lu comme un chiffre absent des données. Sur une réponse juste, ce serait une fausse alerte.

## 5. Rétention des faits sur les conversations longues (M2, EVAL-10)

Mesurée le 31/10 en **version réduite** sur la machine de démo : `qwen3:8b`, `num_ctx` 16384, prompt v4, graine 42, juge actif. Script `scripts/eval_long_conversations.py`, rapport [`eval/reports/2026-10-31_long-conversations.md`](../eval/reports/2026-10-31_long-conversations.md).

**Méthode**
- **Conversations** : 2 conversations scriptées (`eval/long_conversations.jsonl`), de 12 et 11 tours. Les premiers tours plantent 5 faits : deux chiffres donnés par l'agent, un objectif chiffré et une définition donnés par l'utilisatrice, un périmètre. Viennent ensuite des questions ordinaires, puis une question de contrôle par fait.
- **Contrôle par le code** : le nombre attendu doit figurer dans la réponse (480 000 €, 17 gros clients, 9 commandes de la Restauration, 209 625,54 €, 260 clients actifs). Un chiffre donné par l'agent ne compte comme retrouvé que si le contrôle n'a pas refait de requête.
- **Comparaison** : mêmes conversations et mêmes graines, une fois en `truncate`, une fois en `compress`.
- **Mémoire isolée** : outils mémoire retirés, aucun contexte mémoire. Seul l'historique ou son résumé peut porter un fait.
- **Échelle réduite** : pour que l'historique déborde en une dizaine de tours au lieu de 30, `budget_ratio` est à 0,29 (limite d'environ 4 750 tokens, dont environ 4 000 pour le prompt système et les outils) et `keep_recent_turns` à 2. La compaction se déclenche aux tours 7 et 8, une fois les faits sortis de la fenêtre des échanges récents.

| Stratégie | Faits retrouvés | Perdus | Interrompus (délai) | Compactions | Durée d'une compaction | Tokens du prompt (moyenne) | Tour p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `truncate` | 0 / 5 | 5 | 0 | 4 | 0 s | 4 401 | 33,0 s |
| `compress` | **2 / 5** | 2 | 1 | 4 | 18,8 s | 4 466 | 38,2 s |

| Fait planté | `truncate` | `compress` |
|---|---|---|
| Objectif de 480 000 € (donné par l'utilisatrice) | perdu : « pas disponible dans les données » | **retrouvé** |
| « Gros client » = plus de 30 000 € de CA, soit 17 clients | perdu : 7, les clients de taille « GE » | **retrouvé** : 17, avec le critère rappelé |
| CA du trimestre, 209 625,54 € (donné par l'agent au tour 1) | perdu | interrompu : tour coupé par le délai de 120 s, réponse vide |
| Périmètre Restauration, soit 9 commandes le mois dernier | perdu : 165, sans filtre | perdu : 154, filtre sur le statut et pas sur le secteur |
| 260 clients actifs (donné par l'agent au tour 1) | perdu | perdu : 260 était dans le résumé, à côté de deux autres « clients actifs » du périmètre (14 et 20) ; le modèle a répondu 20 |

**Ce qu'on en tire**
- **La troncature oublie tout ce qui sort de la fenêtre.** L'agent le dit (« pas disponible ») ou se rabat sur une autre interprétation (7 « gros clients » au sens de la taille d'entreprise).
- **Le résumé garde ce que l'utilisatrice a énoncé explicitement**, un chiffre ou une définition : 2 faits sur 2 de ce type.
- **Il reste fragile sur deux points** : un choix de périmètre n'est pas réappliqué aux questions suivantes, et des chiffres aux libellés proches se confondent dans le résumé.
- **Son coût** : environ 19 s par compaction (un appel au modèle, réflexion comprise), pour un prompt de taille comparable.
- **Une limite de conception relevée** : le résumé ne reporte que les 15 derniers chiffres vérifiés par le juge (`MAX_VERIFIED`, `core/context.py`). Dans une séance riche en chiffres, un chiffre vérifié ancien peut donc sortir de cette liste.

**Limites de la mesure** : 5 faits et 2 conversations, en un seul passage, donc un fait vaut 20 points. L'échelle est réduite : ce n'est pas une vraie séance de 30 tours. Un contrôle sur 5 a été coupé par le délai. Enfin, le rapport de ce run ne garde que le début de chaque résumé ; le script enregistre désormais le résumé complet.

## 6. Latence (CORE-18)

Mesurée par le harness sur la machine de démo le 30/10 : RTX 3080 10 Go, `qwen3:8b` (modèle retenu, D-09), `num_ctx` 16384, prompt v1, 25 questions, rapport `eval/reports/2026-10-30_v1.0-qwen3-8b.md`. Le premier token de la réponse n'arrive qu'après la réflexion de qwen3 : c'est presque toute la durée du tour.

| Mesure | p50 | p95 |
|---|---:|---:|
| Durée du tour | 19,4 s | 66,8 s |
| Premier token | 18,3 s | 63,8 s |
| Juge (après la réponse, non bloquant) | 8,4 s | 15,7 s |

`llama3.1:8b`, sans réflexion, répond en 2,3 s (p50) et 7,5 s (p95), mais avec une EX de 0,37 contre 0,58 (D-09).

Non mesuré : le coût d'une compression (M2) et des exemples dynamiques (A7), absents de ce run (prompt v1, sans `$examples`). Une suite de tests a chargé le CPU pendant environ 1 min 30 de ce run : une ou deux durées peuvent en être un peu allongées.

## 7. Sécurité

La résistance aux attaques (EV4) est mesurée dans [`docs/SECURITY.md`](SECURITY.md) (P2).

## 8. Limites

- 25 à 40 questions donnent des intervalles de confiance larges : un écart de quelques points d'EX n'est pas significatif.
- Même à température 0 et graine fixée, la génération sur GPU peut varier d'un run à l'autre : chaque mesure est lancée deux fois et l'écart noté.
- Les 20 cas de calibration ont été étiquetés par une seule personne ; une double annotation d'une partie des cas reste à faire.
- La banque d'exemples (A7) est séparée du golden set et vérifiée par un test anti-fuite, mais ses requêtes ressemblent par construction à celles du jeu : le gain mesuré vaut pour cette base.

## 9. Reproduire

```bash
python -m sensai seed --seed 42 --today 2026-10-20
python -m sensai eval --tag v1.0-rc
python -m sensai eval --tag v1.0-rc-prompt-v1 --prompt-version v1
python -m sensai eval --tag v1.0-rc-sans-exemples --no-fewshot
python -m sensai eval --tag v1.0-rc-sans-memoire --no-memory
python -m sensai eval compare v1.0-rc-prompt-v1 v1.0-rc
python -m sensai eval calibrate
python -m sensai eval calibrate --numeric-only
python scripts/eval_long_conversations.py        # §5, environ 45 min sur la machine de démo
```
