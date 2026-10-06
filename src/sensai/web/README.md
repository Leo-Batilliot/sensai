# `sensai.web` — serveur web

Le serveur FastAPI qui relie le front React au moteur : routes REST, WebSocket `/ws/{session_id}`, et la page construite (`web/dist`). Il ne contient **aucune logique métier** : il transmet les messages du navigateur au moteur et renvoie les événements du moteur au navigateur.

- Contrat à respecter : [`docs/contracts/websocket.md`](../../../docs/contracts/websocket.md) (messages) et [`docs/contracts/events.md`](../../../docs/contracts/events.md) (événements).
- Lancer : `make web-mock` (http://127.0.0.1:8000). Jusqu'à WEB-08, le serveur tourne sur le `MockEngine`.
- Tester : `make test`.

## L'arborescence

```text
web/
├── server.py          crée l'application : routes REST, WebSocket, page construite
├── paths.py           chemins du dépôt (web/dist, fixtures, schémas du contrat)
├── websocket/         la connexion avec le navigateur
│   ├── endpoint.py    porte d'entrée /ws/{id} : accepte, envoie session_ready, lit en boucle
│   ├── messages.py    ce que fait chaque message (une méthode _on_<type> par message)
│   ├── session.py     une session par conversation : historique, tour en cours, envoi
│   ├── confirm.py     confirmations HITL : une attente par action, délai, refus en masse
│   └── settings.py    réglages modifiables par set_config, bornes, validation
└── engines/           le côté moteur
    ├── backend.py     l'interface que le moteur doit respecter (WebEngine, WebBackend)
    └── mock.py        le faux moteur : rejoue tests/fixtures/events/
```

**Règle des deux dossiers** : `websocket/` parle au navigateur, `engines/` parle au moteur. Le jour de WEB-08, le vrai moteur arrive dans `engines/`, à côté de `mock.py`, sans rien changer dans `websocket/`.

## Le trajet d'un message

```text
navigateur ──user_message──▶ websocket/endpoint.py ──▶ websocket/messages.py (_on_user_message)
                                                              │
                                                              ▼
                                                   websocket/session.py (start_turn : tâche à part)
                                                              │
                                                              ▼
                              engines/ (run_turn) ──événements──▶ websocket/session.py ──▶ navigateur
```

**La boucle de lecture (`endpoint.py`) ne fait jamais tourner un tour.** Le tour tourne dans sa propre tâche (`session.py`). Sinon, pendant une confirmation, le tour attendrait la réponse et la boucle, bloquée, ne pourrait plus lire le message `confirm` qui la contient.

## Où regarder pour…

| Je veux… | Fichier |
|---|---|
| Ajouter une route REST (`/api/config`, `/api/memory`…) | `server.py` |
| Changer ce que fait un message (`reset`, `cancel`…) ou en ajouter un | `websocket/messages.py`, et d'abord `docs/contracts/websocket.md` + `docs/contracts/schemas/ws_client.v1.json` |
| Comprendre quand la session est « occupée », ou l'historique de reconnexion | `websocket/session.py` |
| Changer le délai ou le comportement d'une confirmation | `websocket/confirm.py` |
| Rendre un réglage modifiable depuis l'interface | `websocket/settings.py`, et d'abord le contrat (§4.7) |
| Changer le scénario rejoué pour un message, ou le rythme du faux moteur | `engines/mock.py` (`ROUTES`, `Pacing`) |
| Brancher le vrai moteur (WEB-08) | `engines/backend.py` (interface à respecter), puis un nouveau fichier dans `engines/` à côté de `mock.py` |

## Tests

| Fichier | Vérifie |
|---|---|
| `tests/unit/web/test_server.py` | Routes REST, page construite, commande `sensai web` |
| `tests/unit/web/test_ws.py` | Le WebSocket de bout en bout : tours, confirmation, `BUSY`, `cancel`, `reset`, reconnexion, erreurs |
| `tests/unit/web/test_mock_engine.py` | Choix du scénario, identifiants réécrits, pannes en boucle |
| `tests/contract_rules.py` | Règles du contrat partagées : chaque événement envoyé est vérifié avec |
