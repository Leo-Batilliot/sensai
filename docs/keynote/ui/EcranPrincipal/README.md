# EcranPrincipal

Vue d'ensemble de l'interface web : barre d'état en haut, fil de conversation au centre, panneau droit Timeline / Mémoire.

## Disposition
- Barre d'état : 48 px, toute la largeur (`statusbar-h`).
- Conversation : largeur libre, contenu limité à 760 px (`chat-max-w`), saisie collée en bas.
- Panneau droit : 360 px (`sidepanel-w`), deux onglets **Timeline** et **Mémoire**. Repliable pour la démo plein écran.
- La modale HITL recouvre tout l'écran (voir ModaleHITL).

## Lettres
| | Zone | Carte |
|---|---|---|
| A | Fil de conversation | Conversation |
| B | Panneau Timeline / Mémoire | Timeline, PanneauMemoire |
| C | Bloc SQL replié | BlocSQL |
| D | Tableau de résultats | TableauResultats |
| E | Fiche d'indicateurs | FicheIndicateurs |
| F | Badge du juge | BadgeJuge |
| 1–6 | Barre d'état (dont ⚙ Réglages et Nouvelle conversation) | BarreEtat, Reglages, NouvelleConversation |

## À valider en point
- Panneau droit en onglets (proposé) ou deux colonnes ?
- Résultats affichés dans le message (proposé) ou seulement dans le panneau ?
- Responsive : non prioritaire, cible = écran de démo 1280 px et plus.
