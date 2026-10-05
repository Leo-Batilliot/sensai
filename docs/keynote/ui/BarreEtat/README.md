# BarreEtat

Barre du haut qui montre le modèle, les serveurs MCP, la consommation du contexte et l'état de connexion.

## Annotations
1. **Sélecteur de modèle** : liste issue de `GET /api/models`, indique si le modèle gère les outils. Envoie `set_model`, effectif au tour suivant, historique conservé (US-X1-02).
2. **Serveurs MCP** : une chip par serveur de `GET /api/tools` ; plein = connecté, pointillé = indisponible.
3. **Jauge de contexte** : `ctx_used / ctx_max` de `message_done.usage`, mise à jour après chaque réponse.
4. **Connexion WebSocket** : connecté, reconnexion en cours, perdue.
5. **Réglages** : bouton ⚙ (libellé « Réglages » au survol et pour les lecteurs d'écran), ouvre le panneau Réglages.
6. **Nouvelle conversation** : vide l'historique de session via `reset` (voir NouvelleConversation).

## États
- Bandeau rouge : WebSocket perdu, reconnexion automatique.
- Bandeau jaune : Ollama injoignable (`error` avec `recoverable: true`).

## Événements
`session_ready`, `message_done`, `error`. Envoie `set_model`, `set_config`, `reset`.
