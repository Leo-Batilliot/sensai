# Script de démo — mid-term (mer 14/10)

| | |
|---|---|
| **Tâche** | CORE-10 (P1) |
| **Pour** | Le pilote au clavier et l'équipe, en répétition (ALL-08) et le jour J |
| **Source** | [`scenario.md`](scenario.md) : actes 1, 2 et 4, minutage §8.1 |

Les commandes sont écrites pour PowerShell sous Windows (`.venv\Scripts\python`). Sous WSL, remplacer par `.venv/bin/python` ou par les cibles `make`.

## 1. Préparation (le matin, sur la machine de démo)

| # | Commande | Vérification |
|---|---|---|
| 1 | `ollama pull qwen3:8b` | `ollama list` affiche le modèle |
| 2 | Installer `uv`, puis `uvx mcp-server-time --help` | Le serveur de date est en cache : aucun téléchargement pendant la démo |
| 3 | `.venv\Scripts\python -m sensai seed` | Base générée avec la date du jour comme date de référence, la même que celle que renvoie l'outil de date. **À refaire après chaque répétition de l'acte 4** (la suppression est réelle) |
| 4 | `Remove-Item data\memory.db` | Mémoire vide : l'acte 2 part d'un glossaire vide |
| 5 | `cd web; npm ci; npm run build; cd ..` | Une seule fois : construit `web/dist` |
| 6 | `.venv\Scripts\python -m sensai web` | http://127.0.0.1:8000 : barre d'état avec `sql_toolbox` et `time` en ligne |
| 7 | Poser une question d'échauffement, puis cliquer **Nouvelle conversation** | Le modèle est chargé en mémoire (`keep_alive` 30 min) |

En CLI, la même session démarre avec `.venv\Scripts\python -m sensai chat`. Taper `/tools` doit afficher `sql_toolbox` et `time` « en ligne », puis les outils.

## 2. Acte 1 — Question métier (4 min, P1 et P2)

**Taper :**
> Quels sont nos 5 clients les plus rentables ce trimestre et quel est leur délai moyen de paiement ?

**Attendu :**
- Timeline : `time__get_current_time`, puis `sql_toolbox__run_select` (éventuellement `list_tables` et `describe_table` avant).
- Bloc SQL « lecture seule », puis tableau de 5 lignes : nom, marge, délai moyen.
- Réponse en streaming, avec la période et l'hypothèse sur le délai.
- Puis badge **Vérifiée** et cartes KPI (web). En CLI, la ligne « Vérification : Vérifiée ».

**À commenter pendant que l'agent travaille :** le modèle choisit seul ses outils (T1), la date vient d'un serveur MCP existant, le SQL passe par notre garde (lecture seule), et le juge vérifie après coup sans bloquer (EV1, T5).

**Secours :**
- si le modèle s'égare : « Donne-moi le top 5 des clients par marge brute depuis le 1er octobre, avec leur délai moyen de paiement en jours. » ;
- si le serveur `time` est indisponible : même phrase, avec une période explicite.

## 3. Acte 2 — Mémoire métier, version raccourcie (2 min 30, P3)

**Taper :**
> Chez nous, un client rentable c'est une marge brute supérieure à 25 %. Retiens-le.

**Attendu :** l'appel `glossary_define`, la ligne « mémoire : … », et l'entrée visible dans le panneau Mémoire (`/memory` en CLI).

**Puis :** cliquer **Nouvelle conversation** (`/reset` en CLI). L'historique est vidé, la mémoire est conservée.

**Taper :**
> Quels sont nos clients rentables ce trimestre ?

**Attendu :**
- l'étiquette **« Contexte mémoire fourni : client rentable »** ;
- un SQL qui applique la règle (`> 0.25`) ;
- une liste **différente** du top 5 de l'acte 1 ;
- le badge.

**Secours :** « Mémorise : client rentable = marge brute supérieure à 25 % du chiffre d'affaires HT. », puis « Liste les clients rentables du trimestre selon notre définition. »

## 4. Acte 4 — Action destructive (2 min 30, P2)

Les outils d'écriture SQL sont livrés (MCP-05, dans `v0.5.0`) : `sql_toolbox__preview_write` calcule l'impact dans une transaction annulée, et `sql_toolbox__execute_write` exécute la requête après confirmation, avec une trace dans `write_audit`.

> ⚠️ **La suppression est réelle.** Après chaque passage où l'on confirme, relancer `sensai seed` pour retrouver les 40 clients inactifs.

**Taper :**
> Supprime les clients inactifs depuis plus de 18 mois.

**Attendu :**
1. Timeline : `sql_toolbox__execute_write`, parfois précédé de `sql_toolbox__preview_write`, avec un bloc SQL « DELETE ».
2. **Confirmation demandée** (fenêtre modale sur le web, invite y/N en CLI) : le SQL exact, l'impact par table (environ 25 clients supprimés sur la base de démo), des exemples, et un compte à rebours de 120 s. **Refuser** : « refusé : rien n'est exécuté », et l'agent confirme qu'il n'a rien supprimé.
3. **Taper** « Combien de clients inactifs avons-nous ? » : toujours 40, la base est intacte.
4. **Reposer la demande et confirmer** : les 25 clients inactifs sans commande sont supprimés, et l'opération est inscrite dans `write_audit`. Les 15 autres ont des commandes ou des factures : la base les protège, la comptabilité doit les conserver.

**À dire :**
- La décision de demander confirmation vient d'une **politique déterministe** (classification du SQL et annotations des outils), jamais du modèle.
- L'aperçu est **exact**, parce qu'il est calculé dans une transaction annulée.
- Sans réponse dans le délai, c'est un refus.

**Secours :**
- Si l'agent écrit un `DELETE` naïf (`WHERE status = 'inactive'`), SQLite refuse toute l'instruction à cause des factures liées, et l'aperçu l'indique (« des lignes liées doivent être conservées »). C'est aussi une bonne démonstration : la base protège les pièces comptables. Relancer avec : « Supprime les clients inactifs qui n'ont jamais passé de commande. »
- Si l'écriture SQL ne part pas du tout : jouer la **variante sur la mémoire**, après l'acte 2. Taper « Supprime la définition de « client rentable » de la mémoire. ». La confirmation de `memory_delete` s'affiche avec l'impact : refuser, vérifier avec `/memory` que le terme est toujours là, puis reposer la demande et confirmer.

## 5. En cas de problème

| Symptôme | Réaction |
|---|---|
| Ollama ne répond pas | `ollama serve`, puis reposer la question : la session reste ouverte |
| Serveur `time` indisponible | Utiliser les phrases de secours avec une période explicite |
| Réponse trop lente ou hors sujet | Ctrl+C (CLI) ou Stop (web), puis la phrase de secours |
| Échec d'un acte | Passer à la vidéo de secours de l'acte, sans improviser |
