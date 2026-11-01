# Brancher un serveur MCP

Ce guide explique comment donner à SENSAI les outils d'un serveur MCP existant, **sans toucher au code** : tout passe par `config.toml` et, si besoin, par les profils de `personas/`.

SENSAI en branche deux par défaut :
- `sql_toolbox`, notre serveur, qui donne accès à la base de l'entreprise ;
- `time`, un serveur existant (`mcp-server-time`), qui donne la date et l'heure.

L'exemple ci-dessous ajoute le serveur officiel `filesystem` en lecture sur un dossier.

Choix techniques : [ADR-003](../adr/ADR-003-client-mcp-ecrit-a-la-main.md) (client écrit à la main), [ADR-004](../adr/ADR-004-politique-hitl-deterministe.md) (confirmations).

## 1. Ce qu'il faut savoir avant

- SENSAI parle MCP **sur stdio** : il lance le serveur comme un programme et lui parle par son entrée et sa sortie. Un serveur qui ne s'utilise que par HTTP ne se branche pas.
- Il faut la **commande qui lance le serveur** :
  - serveurs Python : `uvx <nom-du-paquet>` (installé avec `uv`, voir le README) ;
  - serveurs Node.js : `npx -y <nom-du-paquet>` (demande Node.js).
- Le premier lancement télécharge le serveur : il faut Internet **une fois**. Ensuite, il est dans le cache.

## 2. Essayer le serveur seul (facultatif, recommandé)

L'outil officiel MCP Inspector ouvre une page qui liste les outils du serveur et permet de les appeler :

```bash
npx @modelcontextprotocol/inspector npx -y @modelcontextprotocol/server-filesystem ./reports
```

Si les outils s'affichent, le serveur fonctionne : SENSAI saura lui parler.

## 3. Le déclarer dans `config.toml`

Ouvrez `config.toml` (à la racine du projet) et ajoutez une table `[mcp.servers.<nom>]` :

```toml
[mcp.servers.sql_toolbox]
command = "python"
args = ["-m", "sensai.mcp_servers.sql_toolbox"]

[mcp.servers.time]
command = "uvx"
args = ["mcp-server-time", "--local-timezone=Europe/Paris"]

[mcp.servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "./reports"]
```

> ⚠️ **Gardez `sql_toolbox` et `time` dans la liste.** Dès que `config.toml` contient une table `[mcp.servers.*]`, elle **remplace** toute la liste par défaut. Un serveur absent de la liste n'est pas lancé. Sans `sql_toolbox`, l'agent n'a plus accès à la base.

Règles :
- **Nom du serveur** : lettres, chiffres, `-` et `_`, sans `__` (deux soulignés). Il préfixe le nom des outils.
- **Nom des outils vus par l'agent** : `<serveur>__<outil>`, par exemple `filesystem__read_file`.
- **Chemins** : relatifs au dossier d'où vous lancez `sensai`.

## 4. Autoriser ses outils dans les profils

Un profil ne montre à l'agent que les outils de sa liste `allow` ([ADR-009](../adr/ADR-009-profil-restreint-seulement.md)). Un nouveau serveur est donc **invisible** tant qu'aucun profil ne l'autorise.

Dans `personas/analyste.toml` (et les autres profils qui doivent l'utiliser), ajoutez un motif :

```toml
[tools]
allow = ["time__*", "filesystem__*", "sql_toolbox__list_tables", ...]
```

`filesystem__*` autorise tous les outils du serveur ; `filesystem__read_file` n'en autorise qu'un.

## 5. Relancer et vérifier

1. Arrêtez SENSAI (Ctrl+C dans le terminal), puis relancez `sensai web` ou `sensai chat`.
2. **Dans le navigateur**, la barre d'état affiche « Serveurs MCP : 3/3 ». Cliquez dessus : chaque serveur est « en ligne », ou « indisponible » avec la raison.
3. **En terminal**, la commande `/tools` liste les outils du profil actif : ceux du nouveau serveur commencent par `filesystem__`.
4. Posez une question qui demande l'outil, puis regardez la Timeline : l'appel `filesystem · read_file` y apparaît.

## 6. Ce que SENSAI fait des outils d'un serveur tiers

- **Confirmation par défaut** : un outil tiers qui ne se déclare pas en lecture seule (annotation MCP `readOnlyHint`) demande votre accord **à chaque appel**. Seul `sql_toolbox` est un serveur de confiance, et ce choix est dans le code (`TRUSTED_SERVERS`), pas dans la configuration : un fichier de configuration ne peut pas faire croire qu'un serveur inconnu est sûr.
- **Refus** : pour interdire un outil précis, ajoutez son nom à `[hitl] deny`, par exemple `deny = ["filesystem__write_file"]`.
- **Panne** : un serveur qui ne démarre pas est marqué indisponible, et les autres continuent. S'il plante en cours de route, il est relancé une fois.

## 7. Dépannage

| Symptôme | Cause probable | Que faire |
|---|---|---|
| « indisponible » avec « démarrage impossible » | `uvx` ou `npx` n'est pas installé, ou pas dans le PATH | Installer `uv` ou Node.js (README), puis rouvrir le terminal |
| L'application refuse de démarrer : « nom de serveur invalide » | Le nom contient `__`, un espace ou un accent | Renommer la table, par exemple `[mcp.servers.mon-serveur]` |
| Le serveur est en ligne mais l'agent ne l'utilise jamais | Ses outils ne sont dans aucun profil | Étape 4 |
| L'agent n'a plus accès à la base | `sql_toolbox` a disparu de `config.toml` | Étape 3 : recopier `sql_toolbox` et `time` |
| Une confirmation à chaque appel | Outil tiers non déclaré en lecture seule | Normal (section 6) |
