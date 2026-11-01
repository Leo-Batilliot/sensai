# ADR-006 — Juge non bloquant après la réponse, et contrôle numérique déterministe

- **Statut** : acceptée
- **Date** : 10/10/2026 (EVAL-03, EVAL-04) ; en ligne le 12/10/2026 (EVAL-05) ; rédigée le 01/11/2026
- **Auteur** : P3 (L.BA) ; rédaction finale P4 (A.P), à partir de PROJECT.md §6.6 et du code
- **Voir aussi** : `src/sensai/eval/judge.py`, `src/sensai/eval/numeric_check.py`, [`docs/contracts/judge.md`](../contracts/judge.md), [`docs/user-stories/EV1.md`](../user-stories/EV1.md), [`EVALUATION.md`](../EVALUATION.md) §4

## Contexte

EV1 demande d'évaluer automatiquement les réponses (fidélité aux sources, pertinence) avec un LLM juge, et de signaler les affirmations contredites ou invérifiables. Pour un copilote BI, le risque principal est un **chiffre faux** présenté avec assurance : l'acte 3 le montre avec une marge inventée pour Brasserie Lemaire.

Deux questions : **quand** juger (avant ou après l'affichage), et **comment** attraper les chiffres faux, sur lesquels les petits modèles locaux se trompent souvent.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Juge bloquant** : la réponse n'est affichée qu'une fois jugée | Rien de faux n'apparaît | Supprime le streaming et double l'attente (juge p50 environ 8 s en plus d'un tour de 16 à 19 s) |
| **B. Juge LLM seul**, après la réponse | Simple ; voit le sens (entités interverties) | Un modèle de 8 milliards de paramètres rate des chiffres ; dépend de la disponibilité du modèle |
| **C. Métrique texte** (BLEU, ROUGE) contre une réponse attendue | Pas de modèle | Mesure la forme, pas l'exactitude ; impossible en direct, sans réponse attendue |
| **D. Juge non bloquant après la réponse + contrôle numérique déterministe d'abord** | Le streaming reste ; les chiffres sont vérifiés par du code, même sans modèle ; le juge ajoute ce que le code ne voit pas | Le badge arrive quelques secondes après la réponse |

## Décision

**Option D.**

- **Après `message_done`**, le moteur émet `judge_started` puis `judge_report` ; l'interface affiche « Vérification en cours… », puis le badge.
- **Le contrôle numérique passe en premier**, sans modèle : chaque nombre de la réponse doit se trouver dans les lignes reçues par l'agent, avec les formats français (« 48 712,40 € », « 12,3 k€ », « 45 % ») et une tolérance (`numeric_tolerance`, 1 %). Un nombre absent donne « contredit », même si le juge est indisponible.
- **Le modèle ne fait que classer les affirmations** selon un schéma JSON ; **le badge et les scores sont calculés par le code**, jamais demandés au modèle. Un rapport invalide est réparé une fois ; le juge ne lève jamais d'exception.
- **Les affirmations contredites sont surlignées** dans la réponse, avec la valeur attendue.

## Conséquences

- **L'acte 3 fonctionne même si le juge échoue** : le chiffre inventé est absent des lignes, le contrôle numérique le marque contredit.
- **Mesuré** : le contrôle numérique seul a une précision de 1,00 et un rappel de 0,64 sur 20 réponses étiquetées ; il ne voit ni les entités interverties ni un chiffre faux proche d'une autre valeur du résultat ([`EVALUATION.md`](../EVALUATION.md) §4). La calibration du juge LLM seul n'a pas été mesurée avant le rendu.
- **Coût** : environ 8 s de juge (p50) après chaque réponse, sans bloquer la question suivante. Le juge peut être désactivé dans les Réglages.
- **Les mêmes scores servent au harness** : fidélité et pertinence de chaque version du système ([`EVALUATION.md`](../EVALUATION.md) §3).
