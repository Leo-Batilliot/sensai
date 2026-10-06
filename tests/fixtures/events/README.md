# Fixtures d'événements du moteur

Séquences d'événements réalistes, conformes à [`events.md`](../../../docs/contracts/events.md) et à [`websocket.md`](../../../docs/contracts/websocket.md). Elles servent à développer et tester le front sans le vrai moteur (`MockEngine`, WEB-04), puis à tester le reducer du front (WEB-05).

Une ligne = un événement complet, tel que le serveur l'envoie sur le WebSocket.

| Fichier | Scénario | Ce qu'on y voit |
|---|---|---|
| `nominal.jsonl` | Acte 1 : top 5 des clients rentables | `thinking`, outil de date, `list_tables`, `describe_table`, une requête en erreur puis corrigée (`SQL_ERROR`), tableau de résultats, réponse en Markdown, badge ✅ avec cartes KPI, hypothèse et relances |
| `memory.jsonl` | Acte 2, en deux tours | Tour 1 : `glossary_define` puis `memory_updated`. Tour 2 : `context_injected` (« Contexte mémoire fourni »), requête qui applique la règle, badge ✅ |
| `judge_contradiction.jsonl` | Acte 3 : chiffre faux | Badge ❌, affirmation `contradicted` avec `expected_value`, nombre listé dans `numeric_check.unmatched` |
| `hitl_approve.jsonl` | Acte 4 : purge confirmée | `confirmation_required` (27 clients, 41 contacts), `approve`, suppression effectuée |
| `hitl_reject.jsonl` | Acte 4 : purge refusée | Même début, `reject` avec commentaire, `tool_result` en échec `ACTION_REJECTED`, base intacte |
| `errors.jsonl` | Trois pannes, un tour chacune | `OLLAMA_UNAVAILABLE` (fin de tour), serveur de date indisponible (`MCP_SERVER_UNAVAILABLE`, l'agent demande la période), boucle détectée (`MAX_ITERATIONS`) |

## Règles pour le `MockEngine`

- **Un tour par message** : un fichier peut contenir plusieurs tours (`memory.jsonl`, `errors.jsonl`) ; le `MockEngine` rejoue le tour suivant à chaque `user_message`.
- **Identifiants** : `session_id` (`s-fixture`) est remplacé par celui de la session ; les `turn_id`, `call_id` et `action_id` peuvent être régénérés, puisqu'ils sont opaques.
- **Rythme** : les `ts` donnent l'ordre, pas le rythme. Le `MockEngine` impose le sien (environ 30 ms par `token`, 400 ms par outil) et réécrit `ts` à l'émission.
- **HITL** : `hitl_approve.jsonl` et `hitl_reject.jsonl` sont identiques jusqu'à `confirmation_required` inclus. Le `MockEngine` joue cette partie commune, attend le message `confirm`, puis continue avec la suite du fichier qui correspond à la décision. Sans réponse avant `expires_in_s`, il émet lui-même `confirmation_resolved {decision: "timeout", reason: "timeout"}`.
- **`cancel`** : le `MockEngine` arrête la lecture et émet `message_done {interrupted: true}` avec les `token` déjà envoyés, sans juge.

## Modifier une fixture

Les tests de contrat ([`tests/unit/contracts/test_websocket_contract.py`](../../unit/contracts/test_websocket_contract.py)) vérifient chaque ligne et chaque tour : champs présents et bien typés, ordre des événements, concaténation des `token` égale à `message_done.content`, affirmations du juge copiées mot pour mot de la réponse, couples `decision` / `reason`. Lancer `make test` après toute modification.
