# ADR-001 — Moteur headless événementiel, adaptateurs CLI et web

- **Statut** : acceptée
- **Date** : 06/10/2026 (contrat d'événements, ALL-02) ; rédigée le 31/10/2026
- **Auteur** : P1 (G.WI)
- **Voir aussi** : [PROJECT.md](../../PROJECT.md) §5.1 à §5.4, [`docs/contracts/events.md`](../contracts/events.md)

## Contexte

Le sujet demande un chatbot en terminal (B1) puis une interface web dont la logique serveur ne change pas (X1, « the backend logic stays unchanged »). La confirmation humaine (A3) doit marcher de la même façon dans les deux : un tour s'arrête au milieu, attend une décision, puis reprend. Plus tard sont venues des exécutions sans humain : tâches planifiées (X5), harness d'évaluation, suite adversariale (EV4).

L'équipe compte quatre personnes qui travaillent en parallèle dès le premier jour. Le front (P4) ne peut pas attendre que le moteur (P1) soit fini.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. La logique dans chaque interface** : la CLI d'abord, puis le web qui la réécrit | Rapide pour la CLI seule | Deux boucles d'agent qui divergent ; X1 n'est pas respecté ; chaque correctif est à faire deux fois |
| **B. Une API REST requête / réponse** : le moteur derrière une route, qui renvoie la réponse complète | Simple, sans état côté client | Pas de streaming des tokens ; impossible de suspendre un tour pour une confirmation ou une question, puis de le reprendre |
| **C. Un moteur sans interface qui émet des événements typés**, et des adaptateurs qui les affichent | Une seule logique, testable sans interface ; le front se développe contre un moteur simulé ; toute nouvelle interface (planificateur, harness) réutilise le même moteur | Le contrat d'événements doit être figé tôt, et chaque ajout passe par lui ; la pause humaine demande une interface à implémenter par chaque adaptateur |

## Décision

**Option C.** `Engine.run_turn(message)` est un générateur asynchrone qui émet des événements typés, de `turn_started` à `judge_report` : tokens, appels et résultats d'outils, SQL, confirmations, questions, mémoire, note, contexte résumé, erreurs (`src/sensai/core/events.py`, 19 types). Leur forme est fixée dans [`events.md`](../contracts/events.md) depuis le 06/10, puis étendue sans changement cassant (contrats v1.1, D-06).

Ce qui demande un humain passe par deux interfaces, et chaque adaptateur apporte la sienne :

| Interface | CLI | Web | Sans humain |
|---|---|---|---|
| `ConfirmationBroker` (A3) | `CliConfirmationBroker` : invite `y/N` | `WebConfirmationBroker` : modale par WebSocket | `AutoRejectBroker` : toute écriture refusée |
| `QuestionBroker` (X4) | `CliQuestionBroker` : choix numérotés | `WebQuestionBroker` : carte de question | `NoHumanQuestionBroker` : l'agent écrit son hypothèse |

Le refus automatique est la valeur par défaut du moteur : une tâche planifiée, le harness et la suite adversariale ne peuvent donc rien modifier sans qu'on ait à le leur interdire. Le moteur et ses outils sont assemblés à un seul endroit (`core/app_factory.py`), pour la CLI comme pour le serveur web.

## Conséquences

- **Une seule boucle d'agent** pour la CLI, le web, les tâches planifiées, le harness et la suite adversariale : ce que l'évaluation mesure est bien le produit.
- **Développement en parallèle** : le `MockEngine` rejoue des événements enregistrés (`tests/fixtures/events/`, `make web-mock`). Le front a été construit et testé avant le moteur réel, et sert encore de démonstration sans Ollama.
- **Tests sans modèle** : un client Ollama scripté suffit pour rejouer un tour complet, avec les vrais outils, et vérifier la suite exacte des événements.
- **Coût** : chaque nouvelle fonctionnalité ajoute des événements au contrat (`context_compacted`, `question_asked`, `document_updated`…), à faire relire par les consommateurs avant de coder.
- **Piège documenté** : le broker doit recevoir sa réponse d'une autre tâche asyncio que celle qui consomme les événements, sinon les deux s'attendent mutuellement (`core/confirm.py`).
