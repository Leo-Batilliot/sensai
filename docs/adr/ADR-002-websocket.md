# ADR-002 — WebSocket plutôt que SSE + POST

- **Statut** : acceptée
- **Date** : 05/10/2026 (contrat WebSocket, WEB-01) ; rédigée le 31/10/2026
- **Auteur** : P1 (G.WI), avec P4 (A.P)
- **Voir aussi** : [PROJECT.md](../../PROJECT.md) §5.6, [`docs/contracts/websocket.md`](../contracts/websocket.md), [ADR-001](ADR-001-moteur-headless-evenementiel.md)

## Contexte

Le moteur émet un flux d'événements pendant un tour (ADR-001). Le navigateur doit les afficher au fil de l'eau, mais aussi **répondre au milieu d'un tour** : approuver ou refuser une écriture (A3), répondre à une question de précision (X4), interrompre la réponse. Ces réponses débloquent le tour en cours : elles doivent arriver au moteur pendant qu'il attend, et être rattachées à la bonne action.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Server-Sent Events + POST** : le flux descend en SSE, les réponses remontent par des requêtes POST | Simple côté serveur ; reconnexion automatique du navigateur | Deux canaux à synchroniser : un POST de confirmation peut arriver avant que le client ait reçu l'événement, ou pour une session déjà fermée ; une déconnexion du flux ne se voit pas côté POST |
| **B. Requêtes REST et interrogation périodique** | Aucun état de connexion | Pas de streaming des tokens ; latence de l'interrogation pendant une confirmation à 120 s |
| **C. Un WebSocket par session** | Un seul canal bidirectionnel : les événements descendent, les décisions remontent dans l'ordre ; la fin de la connexion se voit immédiatement | Protocole de messages à définir et valider soi-même ; une reconnexion repart d'une session neuve |

## Décision

**Option C.** Un WebSocket par session (`/ws/{session_id}`, FastAPI). Il porte les événements du moteur vers le navigateur, et toutes les actions du navigateur vers le serveur : `user_message`, `confirm`, `answer`, `cancel`, `reset`, `set_model`, `set_config`, `set_persona`, `share`, `unshare`, `run_schedule`, `ui_action`. Chaque message reçu est validé par un schéma ; un message invalide reçoit une erreur sans couper la connexion ([`websocket.md`](../contracts/websocket.md)).

Les routes REST ne servent qu'à **lire** (santé, modèles, mémoire, note et ses révisions, exports, partages, rapports planifiés) : aucune ne modifie quoi que ce soit.

## Conséquences

- **Une déconnexion pendant une confirmation la refuse** (`reason: "disconnected"`) : fermer l'onglet ne peut jamais valider une écriture.
- **L'ordre est garanti** : une décision ne peut pas arriver avant la demande qu'elle tranche ; une décision pour une action inconnue reçoit `UNKNOWN_ACTION`.
- **L'annulation** (`cancel`) passe par le même canal que le streaming : elle arrête le tour, et refuse d'abord une confirmation en attente.
- **Coût** : la table des messages autorisés selon l'état de la session (libre, tour en cours, confirmation en attente) est écrite et testée par nous (`websocket.md`, tests de contrat).
- **Reconnexion** : l'onglet garde l'identifiant de sa session ; après un rechargement, le serveur la retrouve et l'annonce (`session_ready`). En revanche, l'historique vit en mémoire du serveur : un redémarrage le perd, alors que la mémoire et la note restent en base.
