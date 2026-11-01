# ADR-005 — Mémoire de l'agent dans une base dédiée, recherche FTS5

- **Statut** : acceptée
- **Date** : 05/10/2026 (MEM-01) ; alias ajoutés le 27/10/2026 (MEM-05) ; rédigée le 01/11/2026
- **Auteur** : P3 (L.BA) ; rédaction finale P4 (A.P), à partir de PROJECT.md §6.4 et du code
- **Voir aussi** : `src/sensai/memory/` (`schema.sql`, `store.py`, `fts.py`, `context.py`), `src/sensai/tools/memory_tools.py`, [`docs/contracts/memory.md`](../contracts/memory.md), [`docs/user-stories/M3.md`](../user-stories/M3.md)

## Contexte

M3 demande une mémoire structurée dans une vraie base : faits, entités, relations, que l'agent lit, écrit et met à jour lui-même d'une séance à l'autre. À l'acte 2, Claire dit « un client rentable, c'est une marge brute supérieure à 25 % du CA HT » ; le lendemain, la question « clients rentables » doit appliquer cette règle sans qu'elle la répète.

Il faut choisir où ranger cette mémoire et comment retrouver ce qui concerne une question.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Fichier plat** (JSON ou Markdown) relu en entier | Trivial | Ce n'est pas la base demandée par M3 ; pas de requêtes ni de transactions ; tout le fichier dans le prompt |
| **B. Tables dans `business.db`** | Une seule base | Mélange les données de l'entreprise et celles de l'agent ; la garde SQL devrait laisser l'agent écrire dans la base métier ; une remise à zéro de la démo efface l'une avec l'autre |
| **C. Embeddings et base vectorielle** | Retrouve les reformulations | Glisse vers le RAG (R1) ; un second modèle en mémoire GPU ; inutile pour un vocabulaire métier court |
| **D. Base SQLite dédiée (`memory.db`), recherche plein texte FTS5** | Vraie base relationnelle, transactions, cascade ; FTS5 est intégré à SQLite, sans dépendance ; base de l'agent séparée de la base métier | Pas de recherche sémantique : un synonyme absent n'est pas retrouvé |

## Décision

**Option D.**

- **`memory.db`**, distincte de `business.db` : tables `entities`, `relations`, `facts`, `glossary`, journal `memory_audit`, index `memory_fts`.
- **CRUD piloté par l'agent** via les outils `memory_search`, `glossary_define`, `memory_update`, `memory_delete`. Chaque écriture effective ajoute sa ligne d'audit dans la même transaction ; une suppression est confirmée (ADR-004), avec l'aperçu des cascades.
- **Contexte injecté à chaque tour** : les termes du glossaire dont tous les mots sont dans la question, puis les meilleures correspondances FTS5 parmi entités et faits, sous un budget de tokens fixe. L'événement `context_injected` dit ce qui a été fourni.
- **Le texte brut n'atteint jamais `MATCH`** : `fts.py` construit une requête FTS5 sûre (apostrophes, ponctuation, pluriels en préfixe).
- **Noms normalisés** (MEM-05) : casse, accents et formes juridiques ne comptent pas, donc « Lemaire SARL » et « lemaire » désignent la même entité.

## Conséquences

- **Mesuré** : sans la mémoire, les questions de glossaire tombent à 0 sur 3 ; avec elle, 3 sur 3 ([`EVALUATION.md`](../EVALUATION.md) §3).
- **Visible** : panneau « Mémoire » du web (modifier, supprimer), chip « Contexte mémoire fourni » sous la réponse.
- **Deux bases indépendantes** : supprimer `data/memory.db` oublie tout ce que l'agent a appris sans toucher aux données de l'entreprise, et la garde SQL n'a jamais à laisser l'agent écrire dans `business.db` pour mémoriser. `sensai demo-reset` remet les deux au point de départ de l'acte 1.
- **Limite** : FTS5 ne trouve pas un synonyme qui ne partage aucun mot (« clients qui rapportent » ne retrouve pas « client rentable »). Les embeddings restent une évolution.
