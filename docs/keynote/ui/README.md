# SENSAI — Maquettes basse fidélité

Maquettes de la Web UI (fonctionnalité **X1**) du copilote BI SENSAI. Tâche **WEB-03** du planning : chat, timeline, SQL, résultats, modale HITL, badge du juge, mémoire, barre d'état. À valider au point d'équipe, puis à copier dans `docs/keynote/ui/`.

## Comment lire ces maquettes
- **Basse fidélité** : gris, contours, pas de choix graphique définitif. On valide la structure, le contenu et les états, pas le style.
- **Pastilles orange numérotées** : renvoient aux annotations du README de chaque composant. Elles n'existent pas dans le produit.
- **Barres grises** : texte factice.
- **Données** : fictives, tirées du scénario Nordvent Distribution (PROJECT.md §3). Les chiffres sont illustratifs.
- **Couleurs d'état** (vert, jaune, rouge) : uniquement pour les verdicts, erreurs et actions destructives, toujours avec un symbole (✓ ! ✗).

## Inventaire
| Carte | Rôle | Événements (§7.1) | User stories |
|---|---|---|---|
| EcranPrincipal | Vue d'ensemble | tous | — |
| BarreEtat | Modèle, MCP, jauge, connexion | `session_ready`, `message_done`, `error` | US-X1-02 |
| Conversation | Fil de chat, streaming, saisie | `turn_started`, `token`, `thinking`, `message_done` | US-X1-01 |
| Timeline | Étapes de l'agent | `tool_call`, `tool_result`, `judge_*` | US-X1-03 |
| BlocSQL | Requête exécutée | `sql_generated` | US-X1-01 |
| TableauResultats | Lignes, tri | `tool_result.data` | US-X1-01 |
| FicheIndicateurs | KPI, hypothèses, relances | `judge_report` | — |
| BadgeJuge | Verdict et surlignage | `judge_started`, `judge_report` | EV1 |
| ModaleHITL | Confirmation bloquante | `confirmation_required`, `confirmation_resolved` | US-A3-01, US-A3-03 |
| PanneauMemoire | Glossaire, entités, faits | `memory_updated` | US-M3-03 |
| Reglages | Température, `num_ctx`, juge on/off ; réglages serveur en lecture seule | `set_config` → `config_updated` ; `GET /api/config` | US-X1-02 |
| NouvelleConversation | Bouton, confirmation, état vide | `reset` → `session_reset` | — |

## Parcours de démo
| Acte | Écrans utilisés |
|---|---|
| 1. Question métier | Conversation, Timeline, BlocSQL, TableauResultats, FicheIndicateurs, BadgeJuge |
| 2. Mémoire métier | PanneauMemoire (nouvelle entrée), BlocSQL (« mémoire appliquée ») |
| 3. Hallucination attrapée | BadgeJuge (état contredit, surlignage) |
| 4. Action destructive | ModaleHITL (refus puis confirmation), toasts. En v1 (mid-term), pas de compteur de clients protégés : il arrive avec HITL-03 (S3). À signaler dans KEY-00. |
| 5. Ouverture | BarreEtat (nouveau serveur MCP), ModaleHITL sur écriture de fichier |
| 6. Preuve chiffrée | hors Web UI (slides) |

## Règles de contenu
- Interface en français, phrases courtes, vocabulaire métier (marge, délai de paiement), pas de jargon technique sauf dans les blocs SQL et la timeline.
- Nombres au format français : `12 345,6`, `48,7 k€`, `31 %`, `47,3 j`.
- Les noms d'outils et de colonnes restent en police à chasse fixe.

## Fondations visuelles (provisoires)
- Grille desktop 1280 px : barre d'état 48 px, panneau droit 360 px, conversation limitée à 760 px.
- Espacements par pas de 4 px (`space-1` à `space-8`), rayons `radius-sm` (contrôles) et `radius-md` (blocs).
- Deux thèmes (clair / sombre) pour vérifier dès maintenant que les états restent lisibles.

## Questions à trancher en point
1. Panneau droit en onglets Timeline / Mémoire, ou deux panneaux ?
2. Résultats dépliés par défaut dans le message ?
3. Modale HITL : focus par défaut sur « Refuser » et Échap = refuser — d'accord ?
4. Le badge du juge arrive après la réponse (non bloquant) : ok pour la démo ?
5. Responsive hors périmètre (écran de démo uniquement) ?
6. Réglages : panneau à la place du panneau droit (proposé) ou fenêtre modale ?
