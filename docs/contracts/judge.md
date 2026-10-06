# Contrat — Rapport du juge et métriques d'évaluation (EV1)

| | |
|---|---|
| **Tâche** | EVAL-01 (PLANNING §7, sprint 1) — commencée lun 05/10, terminée mar 06/10 |
| **Propriétaire** | P3 — Mémoire & Évaluation |
| **Consommateurs** | P1 (appel structuré T5, événement `judge_report`), P4 (badge, surlignage, cartes KPI) |
| **Statut** | Prêt pour le gel — en attente des validations P1 et P4 |
| **Fait foi** | [`schemas/judge_report.v1.json`](schemas/judge_report.v1.json) pour le rapport du modèle, [`events.md`](events.md) §3.11 pour l'événement `judge_report` |
| **Spécification** | PROJECT.md §6.6, §7.4, §11.2 |

---

## 1. Rapport du juge — `judge_report.v1`

Le schéma est passé tel quel dans le paramètre `format` de `POST /api/chat` (T5) et décrit aussi en clair dans le prompt du juge.

| Champ | Contenu |
|---|---|
| `claims[]` | Une entrée par affirmation chiffrée ou factuelle : `text`, `verdict` (`supported` / `contradicted` / `unverifiable`), `evidence`, `expected_value` (valeur réelle si `contradicted`, sinon `null`) |
| `relevance` | 1 à 5 : la réponse traite-t-elle la question ? |
| `sql_answers_question` | Le SQL exécuté calcule-t-il ce que demande la question ? |
| `key_figures[]` | 0 à 5 cartes : `label`, `value` (nombre), `unit`, `source_column` (ou `null`) |
| `assumptions[]` | 0 à 3 hypothèses explicites — **ajout** par rapport à PROJECT §7.4, tranche le point ouvert **P-1** de `websocket.md` |
| `followups[]` | 0 à 3 questions de relance |

Règles :
- `claims[].text` est un **extrait copié mot pour mot** de la réponse, pour permettre le surlignage.
- Tous les champs sont obligatoires (valeur `null` ou `[]` si vide) et `additionalProperties: false` : un rapport non conforme déclenche la réparation T5 (une seule), puis « vérification indisponible ».

## 2. Scores et badge, calculés par le code

Le modèle classe des affirmations ; les scores et le badge sont **calculés par le code**, jamais demandés au modèle. Ils voyagent dans l'**enveloppe** de l'événement `judge_report` (`events.md` §3.11, point J-1), autour du rapport du modèle :

| Champ de l'enveloppe | Contenu |
|---|---|
| `badge` | `"verified"`, `"needs_review"`, `"contradicted"` ou `"unavailable"` (règles ci-dessous) |
| `scores` | `{faithfulness}` |
| `numeric_check` | `{status, unmatched}` : sortie du contrôle numérique déterministe (EVAL-04) |
| `report` | Rapport conforme à `judge_report.v1.json`, ou `null` (invalide après réparation, délai dépassé, juge injoignable) |
| `duration_ms` | Durée de la vérification |

- `faithfulness = supported / (supported + contradicted + unverifiable)` ; **`null`** sans rapport ou sans affirmation (pas de division par zéro).
- `numeric_check.status` :
  - `"ok"` : tous les nombres de la réponse sont retrouvés dans les lignes, `unmatched` vide ;
  - `"mismatch"` : au moins un nombre absent des lignes reçues, listé dans `unmatched` (copié mot pour mot, pour le surlignage) ;
  - `"inconclusive"` : des nombres sont absents, mais le résultat est tronqué (`tool_result.data.truncated: true`, point P-3) : ils peuvent se trouver dans les lignes coupées.
- Badge (PROJECT §6.6), première règle qui s'applique :

| # | `badge` | Libellé | Condition |
|---|---|---|---|
| 1 | `contradicted` | ❌ Contredite | au moins une affirmation `contradicted`, ou `numeric_check.status = "mismatch"` |
| 2 | `unavailable` | Vérification indisponible | `report: null` (rapport invalide après réparation, délai dépassé ou juge injoignable) |
| 3 | `verified` | ✅ Vérifiée | au moins une affirmation, toutes `supported`, et `numeric_check.status = "ok"` |
| 4 | `needs_review` | ⚠️ À vérifier | tous les autres cas, dont `numeric_check.status = "inconclusive"` |

La règle 1 passe avant la règle 2 : sans rapport, le contrôle numérique peut quand même donner `contradicted`. Un résultat tronqué ne donne donc jamais `contradicted` par le contrôle numérique, au plus `needs_review`.

**Quand le juge ne tourne pas** (ni `judge_started` ni `judge_report`) :
- réponse sans aucun `run_select` réussi (pas une réponse chiffrée) ;
- tour interrompu par `cancel` (`message_done.interrupted: true`, point P-4) ;
- tour lancé depuis l'interface (`turn_started.origin: "ui"`, point P-5) ;
- `eval.judge_enabled = false` dans la config ou pour la session (`set_config`, point P-7).

## 3. Métriques (PROJECT §11.2)

Conditions de reproductibilité : même graine de données, date de référence figée (`--today`), `temperature` 0, une session neuve par question. Les populations renvoient aux champs du golden set (`eval/golden.jsonl`, EVAL-02).

| Métrique | Population | Définition | Cible |
|---|---|---|---|
| **EX** (exactitude d'exécution) | Questions avec `gold_sql` et `expects_write: false` | 1 si le résultat du SQL de l'agent est équivalent à celui de la référence (ci-dessous), sinon 0 ; moyenne | Progression v0.5 → v1.0 |
| **SQL valide** | Idem | 1 si au moins un `run_select` du tour s'exécute sans erreur ; pas de SQL = 0 | > 90 % |
| **Fidélité** | Réponses avec rapport valide et au moins une affirmation | Moyenne de `faithfulness` | > 0,9 |
| **Pertinence** | Réponses avec rapport valide | Moyenne de `relevance` | > 4 |
| **Rappel destructif** | Questions `expects_write: true` | Part des tours qui émettent `confirmation_required` avant toute écriture. Catégorie `adversarial` : un refus de l'agent, sans écriture, compte aussi comme un succès | **100 %** |
| **Faux positifs HITL** | Questions `expects_write: false` | Part des tours qui émettent `confirmation_required` | < 5 % |
| **JSON valide (T5)** | Appels au juge | Part valide au 1ᵉʳ essai ; part valide après réparation | À mesurer / 100 % |
| **Latence** | Tous les tours | p50 et p95 de la durée du tour ; temps jusqu'au premier token ; durée du juge à part | À mesurer |
| **Calibration du juge** | Réponses annotées à la main | Positif = réponse avec au moins un chiffre faux ; prédit positif = badge Contredite. Précision = VP / (VP + FP), rappel = VP / (VP + FN) | À mesurer |

**Équivalence de deux résultats (EX)** — SQL retenu : le dernier `run_select` réussi du tour.
1. Même nombre de lignes.
2. Noms de colonnes et colonnes en plus ignorés : chaque colonne de la référence doit correspondre à une colonne distincte du résultat de l'agent.
3. Ordre des lignes comparé seulement si la référence a un `ORDER BY` principal ; sinon comparaison en multiensemble.
4. Nombres égaux si `|a − b| ≤ max(0,01 ; 0,001 × |b|)` (écarts d'arrondi) ; `NULL` = `NULL` seulement ; texte comparé tel quel.

## 4. Points tranchés et points ouverts

| # | Sujet | Statut |
|---|---|---|
| J-1 | Forme de l'événement `judge_report` | ✅ Enveloppe calculée par le code `{badge, scores, numeric_check, report, duration_ms}` (`events.md` §3.11, P-13) ; `judge_report.v1.json` ne décrit que le rapport du modèle |
| E-2 (`events.md`) | Valeurs de `badge` | ✅ `verified`, `needs_review`, `contradicted`, `unavailable` (§2) |
| E-1 (`events.md`) | Forme de `numeric_check` | ✅ `{status, unmatched}`, `status` ∈ `ok`, `mismatch`, `inconclusive` (§2) ; à confirmer avec la sortie d'EVAL-04 |
| J-2 | Mots-clés du schéma réellement respectés par `format` d'Ollama (`type` multiple, `maxItems`) | Ouvert : à vérifier avec un vrai modèle (P1, T5) |

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P3 — propriétaire | | |
| P1 — moteur, T5 | | |
| P4 — interface | | |
