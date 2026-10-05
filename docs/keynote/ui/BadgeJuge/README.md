# BadgeJuge

Badge qui donne le verdict du juge sur une réponse chiffrée, avec le surlignage des affirmations.

## Annotations
1. **Cinq états** : en cours, ✓ Vérifiée, ! À vérifier, ✗ Contredite, ⊘ Vérification indisponible. Le symbole est toujours présent : la couleur seule ne suffit pas.
2. **Surlignage** : affirmation contredite soulignée en rouge ; au survol, la vraie valeur et sa colonne source. Affirmation vérifiée soulignée en vert discret.
3. **Détail** : liste des affirmations avec leur verdict, pertinence, fidélité, contrôle numérique.
4. **Vérification indisponible** : le juge a échoué (rapport invalide après une réparation, délai dépassé, modèle injoignable). Badge neutre en pointillés ; au survol, la cause et la mention « les chiffres n'ont pas été contrôlés ». La réponse reste affichée.

## Règles
- Le juge ne bloque pas l'affichage : la réponse arrive d'abord, le badge ensuite.
- Règles du badge : voir PROJECT.md §6.6.
- « Vérification indisponible » (échec du juge) est distinct de « Juge désactivé » (choix de l'utilisateur dans Réglages).

## Événements
`judge_started`, `judge_report`. L'état indisponible suit un échec du juge après `judge_started` (forme exacte du signal : `events.md`).

## Démo
Acte 3 (hallucination attrapée).
