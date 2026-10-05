# NouvelleConversation

Bouton qui vide l'historique de la session pour repartir d'une conversation vierge, avec une confirmation et un état vide.

## Annotations
1. **Bouton** dans la barre d'état, désactivé pendant une génération ou une confirmation HITL en attente.
2. **Confirmation** : popover sous le bouton, focus sur « Annuler ». Rappelle que la mémoire métier, le modèle et les réglages sont conservés. Pas de confirmation si la conversation est vide.
3. **État vide** : accroche, trois questions d'exemple (un clic les envoie), champ de saisie.

## Contrat
Envoie `reset` ; le serveur répond `session_reset` (`websocket.md` §4.4, §5.2). Le fil et la timeline sont vidés, la jauge de contexte affiche « — » jusqu'à la prochaine réponse.

## Démo
Utile entre deux actes, et après le redémarrage de l'acte 2 pour montrer que la mémoire survit à la conversation.
