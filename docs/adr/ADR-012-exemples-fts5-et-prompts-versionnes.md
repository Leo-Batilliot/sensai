# ADR-012 — Exemples dynamiques par FTS5 plutôt qu'embeddings ; prompts versionnés par registre et empreinte

- **Statut** : acceptée ; exemples désactivés par défaut depuis D-10 (31/10/2026)
- **Date** : 24/10/2026 (PROMPT-01, FEWSHOT-01) ; rédigée le 01/11/2026
- **Auteur** : P3 (L.BA) ; rédaction finale P4 (A.P), à partir de PROJECT.md, de D-10 et du code
- **Voir aussi** : `src/sensai/core/fewshot.py`, `src/sensai/core/prompts.py`, `src/sensai/core/prompts/registry.toml`, [`docs/contracts/prompts.md`](../contracts/prompts.md), [`docs/user-stories/A4.md`](../user-stories/A4.md), [`docs/user-stories/A7.md`](../user-stories/A7.md), [`docs/decisions.md`](../decisions.md) D-10

## Contexte

Deux fonctionnalités touchent au prompt système :

- **A4** demande de traiter les prompts comme des artefacts versionnés : datés, comparables, avec retour arrière, et de mesurer l'effet d'une version.
- **A7** demande de construire le prompt dynamiquement, par exemple avec des exemples choisis selon la question.

## Options considérées

**Pour les exemples (A7)**

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Exemples fixes** dans le prompt | Simple | Ce n'est pas A7 ; des tokens perdus sur des exemples sans rapport |
| **B. Embeddings via Ollama** (`/api/embed`) | Retrouve les reformulations | Un second modèle en mémoire GPU, rechargé à chaque tour sur une carte de 10 Go |
| **C. FTS5 (bm25) sur une banque d'exemples validés** | Déjà utilisé par la mémoire (ADR-005) ; aucune dépendance ; déterministe | Ne retrouve que les mots partagés |

**Pour les versions (A4)**

| Option | Avantages | Inconvénients |
|---|---|---|
| **D. Prompt dans le code**, versionné par git | Aucun outil | Impossible de choisir une version au lancement ou de la noter dans un rapport |
| **E. Outil externe** (PromptLayer, Langfuse) | Complet | Service externe : contraire au « rien ne quitte cette machine » |
| **F. Fichiers `.md` publiés dans un registre avec leur empreinte sha256** | Une version publiée ne change jamais ; choisie par la configuration ; notée dans chaque rapport | Corriger une coquille demande une nouvelle version |

## Décision

**Options C et F.**

- **Banque d'exemples** (`src/sensai/data/examples.jsonl`, 36 paires question → SQL validées), séparée du golden set ; un test anti-fuite vérifie qu'aucun exemple ne recouvre une question d'évaluation.
- **À chaque tour**, si `[fewshot] enabled`, les `k` (3) exemples les plus proches (bm25 sur la question et les tags) remplissent le trou `$examples` ; événement `examples_injected`.
- **Registre** `core/prompts/registry.toml` : nom, version, fichier, parent, date, notes et sha256. L'empreinte est vérifiée par les tests et au démarrage. `[agent] prompt_version` choisit la version ; `sensai prompts` les liste et les compare ; le harness accepte `--prompt-version`.
- **Versions publiées** : v1 (mid-term), v2 (hypothèses, `ask_user`, trous `$persona` et `$examples`), v3 (règle d'écriture), v4 (écriture directe, aperçu calculé par l'application).

## Conséquences

- **A4 mesuré** : v1 → v3 fait passer le rappel destructif de 0,25 à 0,75 et l'EX de 0,58 à 0,74 ([`EVALUATION.md`](../EVALUATION.md) §3). Le retour à v1 reste une ligne de configuration.
- **A7 mesuré, et sans gain avec qwen3:8b** : EX 0,68 avec exemples contre 0,74 sans, soit une question sur 19 ; les deux tours coupés au délai l'ont été avec exemples. **D-10 : exemples désactivés par défaut** (`[fewshot] enabled = false`), fonctionnalité livrée et activable, résultat présenté tel quel à la keynote.
- **Leçon** : un modèle qui raisonne déjà (qwen3) tire peu d'exemples proches ; le gain d'A7 dépend du modèle, ce que seul le harness pouvait montrer.
- **Limite** : FTS5 ne retrouve pas une reformulation sans mot commun ; les embeddings restent une évolution.
