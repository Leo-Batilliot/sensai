# ADR-007 — Le SQL est écrit par l'agent dans l'appel d'outil

- **Statut** : acceptée ; le plan B n'a pas été nécessaire
- **Date** : 05/10/2026 (PROJECT.md §6.2) ; confirmée par la mesure du 30/10/2026 ; rédigée le 31/10/2026
- **Auteur** : P1 (G.WI)
- **Voir aussi** : [PROJECT.md](../../PROJECT.md) §6.2 et §16 (risque R1), [`docs/decisions.md`](../decisions.md) D-09 et D-10, [`docs/SECURITY.md`](../SECURITY.md)

## Contexte

Toutes les réponses chiffrées passent par une requête SQL sur `business.db`. Le sujet demande que le modèle **décide lui-même** d'appeler des outils (T1), par un serveur MCP. Le risque principal du projet (R1) est la qualité SQL d'un modèle de 7 à 8 milliards de paramètres qui tourne en local.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. L'agent écrit le SQL dans l'argument d'un outil** (`run_select(sql)`), après avoir lu le schéma avec `list_tables` et `describe_table` | Le modèle choisit quand interroger, combien de fois, et corrige sa requête au vu de l'erreur ; T1 démontré ; un seul appel au modèle par étape | Dépend de la qualité SQL du modèle ; le SQL reçu n'est pas fiable et doit être contrôlé |
| **B. Pipeline en deux temps** : un appel au modèle produit un SQL contraint par un schéma JSON, puis le code l'exécute | Plus robuste sur de petits modèles (sortie structurée, T5) | Double la latence ; retire au modèle la décision d'appeler l'outil (T1 affaibli) ; une seule requête par question |
| **C. Outils métier figés** (`top_clients(n, période)`…) sans SQL libre | Exact et sûr par construction | Ne couvre que les questions prévues : ce n'est plus un copilote BI |

## Décision

**Option A**, avec l'**option B gardée comme plan B** si l'exactitude mesurée était trop basse.

Le serveur MCP `sql_toolbox` expose `list_tables`, `describe_table`, `run_select`, `preview_write` et `execute_write`. Le modèle écrit le SQL dans l'appel ; le code ne lui fait jamais confiance :

- **Garde SQL** (`mcp_servers/sql_toolbox/guard.py`) : connexion en lecture seule pour `run_select`, *authorizer* SQLite, classification par `EXPLAIN` et non par mots-clés, instructions dangereuses refusées partout (`ATTACH`, `PRAGMA`, DDL, plusieurs instructions).
- **Politique de confirmation** : une écriture est jugée sur ce qu'elle fait réellement, quel que soit l'outil appelé (ADR-004).
- **Auto-correction** (CORE-09) : une erreur SQLite revient au modèle avec l'ordre de corriger sa requête, au plus `[agent] max_sql_retries` fois (2). Un refus de la garde n'est pas une erreur à corriger : le modèle est prévenu de ne pas réessayer.

Pour aider le modèle sans changer d'architecture : schéma compact avec deux lignes d'exemple par table, règles de dates SQLite et outil de date dans le prompt, glossaire de la mémoire (M3), et choix du modèle par le harness (D-09).

## Conséquences

- **Le plan B n'a pas été déclenché** : avec `qwen3:8b` et le prompt v4, le golden set donne une EX de 0,74 et 100 % de SQL valide (D-10). À prompt égal (v1), `llama3.1:8b` faisait 0,37 contre 0,58 pour `qwen3:8b` : le choix du modèle et du prompt a suffi, sans changer d'architecture.
- **La sécurité ne dépend pas du modèle** : sur 37 attaques en CI avec un modèle scripté qui obéit à tout, et 40 attaques sur le vrai modèle, aucune action interdite ([`SECURITY.md`](../SECURITY.md)).
- **Coût** : chaque question demande plusieurs allers-retours (schéma, puis requête, parfois correction), ce qui pèse sur la latence (16 s en médiane avec la réflexion de qwen3).
- **Limite** : les 5 échecs restants sur 19 sont des jointures, des « top N » et un filtre de dates : du SQL valide qui répond à une question un peu différente. Le juge vérifie que la réponse est fidèle aux lignes renvoyées, pas que la requête était la bonne : ces réponses reçoivent un badge ✅ (q001 et q002 dans le rapport du 30/10 sans exemples). C'est la limite principale de la vérification (EV1).
