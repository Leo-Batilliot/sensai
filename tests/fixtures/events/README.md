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
| `file_write_approve.jsonl` | Acte 5 : export de la note dans `reports/` | `file_write` sous confirmation, `impact.file` (chemin, taille, nouveau fichier, début du contenu), `approve`, « Fichier créé » |
| `question.jsonl` | Acte 1 avec la question de précision (X4) | `ask_user` → `question_asked` (« Ce trimestre » / « Tout l'historique », champ libre) → `question_answered` → réponse `Réponse : Tout l'historique`, puis le tour de `nominal.jsonl` |
| `file_write_reject.jsonl` | Acte 5 : export refusé | Même début, `reject` avec commentaire, `ACTION_REJECTED`, aucun fichier écrit |
| `document.jsonl` | Acte 2 : la note du comité (M4) | `document_read` (plan de la note), `document_patch` sur la seule section « Rentabilité », `document_updated` (révision 1), réponse sans juge |
| `errors.jsonl` | Trois pannes, un tour chacune | `OLLAMA_UNAVAILABLE` (fin de tour) ; serveur de date indisponible (`MCP_SERVER_UNAVAILABLE`, l'agent demande la période) ; requêtes en échec (`SQL_ERROR`), un appel répété ignoré (`DUPLICATE_CALL`, le tour continue), puis la limite de 6 appels atteinte (`MAX_ITERATIONS`, fin de tour) |

## Règles pour le `MockEngine`

Implémentation : [`src/sensai/web/engines/mock.py`](../../../src/sensai/web/engines/mock.py), lancé par `make web-mock`.

- **Choix du scénario** d'après le message (minuscules, premier mot-clé trouvé) :

  | Le message contient | Scénario rejoué |
  |---|---|
  | « supprime » | Acte 4 (`hitl_*`) : attend la décision, puis la branche correspondante |
  | « exporte » | Acte 5 (`file_write_*`) : même pause que l'acte 4, pour une écriture de fichier |
  | « délai moyen » | Acte 1 avec sa question (`question.jsonl`) : attend la réponse (`answer`), puis rejoue `question_answered` et le résultat d'`ask_user` avec le choix fait |
  | « retiens », « mémorise » | Acte 2, tour 1 (`memory.jsonl`) |
  | « note du comité » | Acte 2, la note (`document.jsonl`) : les appels `document_*` sont exécutés pour de vrai sur la note du mock, à sa révision courante |
  | « clients rentables » | Acte 2, tour 2 (`memory.jsonl`) |
  | « piège », « brasserie lemaire » | Acte 3 (`judge_contradiction.jsonl`) |
  | « erreur », « panne » | Tour suivant d'`errors.jsonl`, en boucle (`reset` repart du premier) |
  | autre chose | Acte 1 (`nominal.jsonl`) |

- **`ui_action`** : tour synthétique (`origin: "ui"`) ; `memory_delete` demande une confirmation, `memory_update` non. Les outils `document_*` du panneau Note passent par le même code que le vrai moteur (`web/engines/note.py`), sur la note du mock : `document_delete_section` demande une confirmation.
- **Un tour par message** : un fichier peut contenir plusieurs tours (`memory.jsonl`, `errors.jsonl`) ; le `MockEngine` rejoue le tour suivant à chaque `user_message`.
- **Identifiants** : `session_id` (`s-fixture`) est remplacé par celui de la session ; les `turn_id`, `call_id` et `action_id` peuvent être régénérés, puisqu'ils sont opaques.
- **Rythme** : les `ts` donnent l'ordre, pas le rythme. Le `MockEngine` impose le sien (environ 30 ms par `token`, 400 ms par outil) et réécrit `ts` à l'émission.
- **HITL** : `hitl_approve.jsonl` et `hitl_reject.jsonl` (de même `file_write_approve.jsonl` et `file_write_reject.jsonl`) sont identiques jusqu'à `confirmation_required` inclus. Le `MockEngine` joue cette partie commune, attend le message `confirm`, puis continue avec la suite du fichier qui correspond à la décision. Sans réponse avant `expires_in_s`, il émet lui-même `confirmation_resolved {decision: "timeout", reason: "timeout"}`.
- **`cancel`** : le `MockEngine` arrête la lecture et émet `message_done {interrupted: true, usage: null}` avec les `token` déjà envoyés, sans juge.

## Modifier une fixture

Les tests de contrat ([`tests/unit/contracts/test_websocket_contract.py`](../../unit/contracts/test_websocket_contract.py)) vérifient chaque ligne et chaque tour : champs présents et bien typés, ordre des événements, concaténation des `token` égale à `message_done.content`, affirmations du juge copiées mot pour mot de la réponse, couples `decision` / `reason`. Lancer `make test` après toute modification.
