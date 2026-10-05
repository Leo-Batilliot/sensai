# ModaleHITL

Fenêtre bloquante qui montre une action destructive et son impact avant de demander une confirmation explicite.

## Annotations
1. **En-tête** : alerte, outil appelé, mention « irréversible ».
2. **Compte à rebours** : `expires_in_s` ; à zéro, refus automatique (US-A3-03).
3. **Requête** : le SQL exact qui sera exécuté.
4. **Impact** : lignes touchées par table, cascades comprises (`impact.per_table`).
5. **Protégés** : éléments bloqués et pourquoi (`impact.blocked`).
6. **Échantillon** : 5 lignes visées (`impact.sample`).
7. **Boutons** : « Refuser » à gauche, focus par défaut ; « Confirmer » rouge, libellé qui répète le nombre de lignes. Échap = refuser.

## Après la décision
Toasts : refusée (base intacte), délai dépassé (annulée, journalisée), confirmée (lignes supprimées, `write_audit`).

## Événements
`confirmation_required` → envoi de `confirm {action_id, decision, comment?}` → `confirmation_resolved`.

## User stories
US-A3-01, US-A3-03, US-M3-03.
