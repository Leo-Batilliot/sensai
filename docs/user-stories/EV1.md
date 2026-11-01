# EV1 — Automated Eval & Hallucination Detection

Deux volets. **En ligne** : chaque réponse chiffrée est vérifiée après coup, sans bloquer l'utilisateur, par un contrôle numérique déterministe et un juge LLM ; le badge est calculé par le code. **Hors ligne** : un harness rejoue le golden set et mesure le système, pour comparer deux versions.

Chaque critère est relié à un test (`pytest <chemin>::<test>`, sans Ollama, lancé par la CI) ou à une étape de démo. Tâches : EVAL-03 (juge), EVAL-04 (contrôle numérique), EVAL-05 (juge en ligne), EVAL-02/02b (golden set), EVAL-06a/06 (harness), EVAL-07 (calibration), WEB-09 (badge). Contrat : [`docs/contracts/judge.md`](../contracts/judge.md).

## US-EV1-01
En tant que **contrôleuse de gestion**, je veux **savoir si les chiffres annoncés sont confirmés par les données**, afin de **ne pas présenter un chiffre faux en comité**.

**Critères d'acceptation**
- Chaque réponse chiffrée (au moins un `run_select` réussi dans le tour) reçoit un badge Vérifiée / À vérifier / Contredite, ou « Vérification indisponible » si le juge ne répond pas.
  — vérifié par : `tests/unit/core/test_engine_judge.py::test_a_numeric_answer_gets_a_badge_after_message_done`, `tests/unit/core/test_engine_judge.py::test_a_contradicted_answer`, `tests/unit/core/test_engine_judge.py::test_a_failing_judge_still_reports`, `tests/unit/eval/test_judge.py::test_badge_rules` ; pas de badge sans chiffre, `tests/unit/core/test_engine_judge.py::test_no_run_select_no_judge`. Badge dans le navigateur : `web/src/judge/judge.test.ts › badgeView › words the badge of each fixture, symbol included`, `› says that the figures were not checked when the judge failed` ; démo : acte 1, badge ✅ Vérifiée.
- Une affirmation contredite est surlignée avec la valeur réelle : le rapport cite l'extrait mot pour mot et sa `expected_value`, le contrôle numérique liste les nombres absents des données.
  — vérifié par : `tests/unit/eval/test_judge.py::test_numeric_check_catches_what_a_blind_judge_misses`, `tests/unit/eval/test_numeric_check.py::test_mentions_are_copied_verbatim`, `tests/unit/contracts/test_judge_contract.py::test_valid_report_is_accepted`. Surlignage dans le navigateur : `web/src/judge/judge.test.ts › highlight › marks the contradicted claim of the contradiction fixture`, `› marks a missing number on its own, as contradicted or to review` ; démo : acte 3, affirmation surlignée en rouge.
- Le badge arrive quelques secondes après la réponse sans bloquer la lecture : le juge tourne après `message_done`, la session est libre pendant ce temps.
  — vérifié par : `tests/unit/core/test_engine_judge.py::test_the_session_is_free_while_the_judge_runs`.

**Statut** : fait (EVAL-03, EVAL-04, EVAL-05, WEB-09)

## US-EV1-02
En tant que **membre de l'équipe de développement**, je veux **comparer deux versions du système sur un jeu de questions de référence**, afin de **vérifier qu'un changement améliore réellement la qualité avant de le livrer**.

**Critères d'acceptation**
- Une commande exécute le golden set (40 questions depuis EVAL-02c ; 25 lors des mesures du 30/10) et produit un rapport horodaté (JSON + Markdown).
  — vérifié par : `tests/unit/eval/test_harness.py::test_run_and_report_writes_both_reports`, `tests/unit/eval/test_harness.py::test_ex_and_sql_valid_per_question`, `tests/unit/eval/test_golden.py::test_gold_sql_gives_rows_on_the_demo_data`. `sensai eval --tag …` tourne sur le moteur réel (EVAL-06) : `tests/integration/test_eval_runtime.py::test_the_golden_set_on_the_real_tools`, `test_eval_run_stops_at_once_when_ollama_is_down`. Runs réels du 30/10 sur la machine de démo : `eval/reports/2026-10-30_v1.0-*.md`, EX 0,74 avec la configuration livrée ([`EVALUATION.md`](../EVALUATION.md) §3).
- Une commande compare deux rapports et affiche les écarts par métrique.
  — vérifié par : `tests/unit/eval/test_report.py::test_compare_shows_deltas_and_changed_questions`, `tests/unit/eval/test_report.py::test_eval_compare_command` ; démo : `sensai eval compare v1.0-qwen3-8b-v4 v1.0-qwen3-8b-v4-sans-exemples`.
- Deux exécutions identiques donnent les mêmes résultats (graine, date figée, température 0) ; seule la latence change.
  — vérifié par : `tests/unit/eval/test_harness.py::test_two_identical_runs_give_the_same_figures`, `tests/unit/eval/test_harness.py::test_each_question_gets_a_fresh_memory_with_the_rules`.

**Statut** : fait (EVAL-06, EVAL-09)

## US-EV1-03
En tant que **responsable qualité**, je veux **mesurer la fiabilité du juge lui-même**, afin de **savoir si je peux faire confiance à ses badges**.

**Critères d'acceptation**
- Un jeu de réponses annotées à la main contient des erreurs injectées.
  — vérifié par : premiers cas dans `tests/fixtures/judge/cases.jsonl`, `tests/unit/eval/test_judge.py::test_judge_gives_the_expected_badge` ; jeu de 20 réponses annotées affirmation par affirmation : `eval/judge_calibration.jsonl`, `tests/unit/eval/test_calibration.py::test_the_set_has_the_planned_mix_of_errors`.
- Le rapport indique la précision et le rappel du juge.
  — vérifié par : `sensai eval calibrate`, `tests/unit/eval/test_calibration.py::test_report_and_tables` ; contrôle numérique seul mesuré (`test_the_numeric_check_alone_on_the_set`) ; contrôle numérique seul : précision 1,00, rappel 0,64 (`eval/reports/2026-10-27_calibration-numeric.md`) ; juge LLM sur le vrai modèle : **non mesuré** avant le rendu.
- Les cas où le juge se trompe sont listés et analysés dans `docs/EVALUATION.md`.
  — vérifié par : [`docs/EVALUATION.md`](../EVALUATION.md) §4 : les 4 réponses fausses que le contrôle numérique manque (3 entités interverties, 1 chiffre proche d'une autre valeur) et sa fausse alerte possible sont analysées. Celles du juge LLM ne le sont pas, faute de mesure.

**Statut** : fait en partie : jeu annoté et contrôle numérique mesurés ; calibration du juge LLM non mesurée
