# PanneauMemoire

Panneau qui liste ce que l'agent a retenu (glossaire, entités, faits) et permet de le modifier ou de le supprimer.

## Annotations
1. **Onglet Mémoire** du panneau droit, à côté de Timeline.
2. **Nouvelle entrée** : mise en avant après un `memory_updated`.
3. **Supprimer** : ouvre la modale HITL (`memory_delete` est destructif).
4. **Édition** : formulaire en place, enregistré via l'agent, audité.
5. **Toast** : signale toute écriture faite par l'agent pendant un tour.
6. **Aperçu de suppression d'une entité** : la modale HITL liste les relations et les faits supprimés avec elle (suppression en cascade), avec le total par table. Le bouton de confirmation répète le nombre d'éléments.

## Données
`GET /api/memory` au chargement, puis événements `memory_updated`.

## User story
US-M3-03. Démo : acte 2.
