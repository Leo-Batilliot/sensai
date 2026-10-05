# Reglages

Panneau qui permet de changer les réglages de génération de la session et d'afficher la configuration serveur en lecture seule.

## Annotations
1. **Génération · modifiable** : les trois réglages que l'utilisateur peut changer depuis le navigateur.
2. **`num_ctx`** : liste de valeurs (4 096, 8 192, 16 384, 32 768). Avertit que le modèle sera rechargé. La jauge de la barre d'état suit la nouvelle valeur.
3. **Juge on/off** : `judge_enabled`. Désactivé, le badge est remplacé par la chip « Juge désactivé ».
4. **Appliquer** : envoie `set_config {patch: {temperature, num_ctx, judge_enabled}}` ; le serveur répond `config_updated`. Effectif au tour suivant, historique conservé. **Réinitialiser** envoie un `set_config` dont le `patch` est `defaults` de `GET /api/config`.
5. **Lecture seule** : liste lue dans `GET /api/config` (`read_only`) : hôte Ollama, modèle du juge, `keep_alive`, garde-fous de l'agent, limites SQL, délai HITL, tolérance numérique. Modifiables seulement dans `config.toml`.

## Règles
- Le modèle reste dans la barre d'état (`set_model`), pas ici.
- Valeurs courantes : `session_ready.config` à la connexion, puis `config_updated.config` après chaque changement.
- Bornes des contrôles : `bounds` de `GET /api/config`, aucune valeur en dur.
- Erreur `invalid_config` affichée dans un toast, sans fermer le panneau ; rien n'est appliqué (tout ou rien).

## Contrat
`set_config` → `config_updated` ; erreur `invalid_config` (`websocket.md` §4.6, §5.2, §7.1). Lecture : `GET /api/config` (§9).

## User story
US-X1-02.
