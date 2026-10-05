# BadgeJuge

Badge qui donne le verdict du juge sur une réponse chiffrée, avec le surlignage des affirmations.

## Annotations
1. **Quatre états** : en cours, ✓ Vérifiée, ! À vérifier, ✗ Contredite. Le symbole est toujours présent : la couleur seule ne suffit pas.
2. **Surlignage** : affirmation contredite soulignée en rouge ; au survol, la vraie valeur et sa colonne source. Affirmation vérifiée soulignée en vert discret.
3. **Détail** : liste des affirmations avec leur verdict, pertinence, fidélité, contrôle numérique.

## Règles
- Le juge ne bloque pas l'affichage : la réponse arrive d'abord, le badge ensuite.
- Règles du badge : voir PROJECT.md §6.6.

## Événements
`judge_started`, `judge_report`.

## Démo
Acte 3 (hallucination attrapée).
