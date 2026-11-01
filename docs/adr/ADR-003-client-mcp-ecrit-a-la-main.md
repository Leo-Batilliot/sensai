# ADR-003 — Client MCP écrit à la main (JSON-RPC sur stdio)

- **Statut** : acceptée
- **Date** : 06/10/2026 (MCP-02) ; rédigée le 01/11/2026
- **Auteur** : P2 (T.BE) ; rédaction finale P4 (A.P), à partir de PROJECT.md §5 et du code
- **Voir aussi** : `src/sensai/mcp/` (`jsonrpc.py`, `stdio.py`, `client.py`, `manager.py`), [`docs/notes/mcp-conformity.md`](../notes/mcp-conformity.md), [`docs/user-stories/T1.md`](../user-stories/T1.md)

## Contexte

T1 demande que le modèle appelle des outils externes par MCP, et le sujet précise que ce sera testé « avec votre propre outil et un outil MCP existant ». SENSAI branche donc deux serveurs : le nôtre (`sql_toolbox`, accès à la base métier) et un serveur existant (`mcp-server-time`, lancé par `uvx`).

Le sujet interdit les frameworks LLM (LangChain, LlamaIndex…). Le SDK officiel `mcp` n'en est pas un au sens strict, mais il cache tout le protocole : poignée de main, négociation de version, cycle de vie des processus. Son statut vis-à-vis de la règle était incertain au démarrage du projet (question posée au suivi, PROJECT.md §17).

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. SDK officiel `mcp`** | Rapide à écrire ; suit les évolutions du protocole | Statut incertain vis-à-vis de la règle « no framework » ; cache ce qu'on doit pouvoir expliquer au jury ; impose son modèle asynchrone et ses dépendances |
| **B. Transport HTTP (SSE / Streamable HTTP)** | Serveurs distants possibles | Inutile pour une démo 100 % locale ; `mcp-server-time` et notre serveur parlent stdio |
| **C. Client écrit à la main, JSON-RPC 2.0 sur stdio** | Aucune dépendance ; chaque étape du protocole est dans notre code et testée ; contrôle total des délais et des redémarrages | Plus de code à écrire et à tester ; à nous de suivre les révisions du protocole |

## Décision

**Option C.**

- **`jsonrpc.py`** : un message = une ligne JSON UTF-8 ; requêtes, réponses, notifications et erreurs JSON-RPC 2.0.
- **`stdio.py`** : le serveur tourne en sous-processus ; stdout et stderr sont lus en continu (un tampon plein bloquerait le serveur).
- **`client.py`** : `initialize` → `notifications/initialized` → `tools/list` / `tools/call`. Le client accepte les révisions `2024-11-05` à `2025-11-25` et refuse proprement une version inconnue.
- **`manager.py`** : démarre tous les serveurs de `[mcp.servers.*]` en parallèle. Un serveur qui ne démarre pas est marqué indisponible avec sa raison, sans bloquer les autres. Un serveur qui plante est relancé une fois au prochain appel, puis reste indisponible.
- Notre serveur `sql_toolbox` réutilise le même module `jsonrpc` : client et serveur parlent exactement le même format.

## Conséquences

- **Brancher un serveur MCP existant ne demande que de la configuration** : une table `[mcp.servers.<nom>]` avec sa commande (guide [`docs/guides/brancher-un-serveur-mcp.md`](../guides/brancher-un-serveur-mcp.md)).
- **Conformité vérifiée avec l'outil officiel** : MCP Inspector se connecte à `sql_toolbox`, et chaque point de la checklist [`mcp-conformity.md`](../notes/mcp-conformity.md) est relié à un test.
- **Pannes visibles** : la barre d'état web affiche « Serveurs MCP : 2/3 » et la raison d'un serveur indisponible (commande `uvx` absente, par exemple).
- **Limites** : stdio seulement (pas de serveur distant), et les révisions futures du protocole demanderont de mettre à jour la liste des versions acceptées.
