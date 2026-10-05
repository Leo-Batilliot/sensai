# FicheIndicateurs

Cartes de chiffres clés issues du rapport structuré du juge, avec hypothèses et questions de relance.

## Annotations
1. **Cartes KPI** : une par `key_figures` (5 max) : label, valeur, unité.
2. **Statut par carte** : vérifiée ou non vérifiable, d'après les verdicts du juge.
3. **Hypothèses** : `assumptions` du rapport du juge, ce que l'agent a supposé (ex. période du délai moyen).
4. **Relances** : `followups` (3 max), un clic envoie la question.
5. **0 carte** : quand `key_figures` est vide, la fiche est masquée.

## Événements
`judge_report` (`key_figures`, `assumptions`, `followups`).
