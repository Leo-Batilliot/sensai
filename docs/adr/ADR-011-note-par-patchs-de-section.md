# ADR-011 — Note du comité par patchs de section, avec verrou optimiste et révisions

- **Statut** : acceptée
- **Date** : 21/10/2026 (contrats v1.1) ; mise en œuvre le 25/10/2026 (ARTIF-01) et le 28/10/2026 (WEB-17) ; rédigée le 01/11/2026
- **Auteur** : P3 (L.BA) ; rédaction finale P4 (A.P), à partir de PROJECT.md §6.17, du contrat et du code
- **Voir aussi** : `src/sensai/documents/store.py`, `src/sensai/tools/document_tools.py`, [`docs/contracts/memory.md`](../contracts/memory.md) §7, [`docs/user-stories/M4.md`](../user-stories/M4.md)

## Contexte

M4 demande que l'agent tienne un document vivant, distinct de l'historique, mis à jour au fil des tours. Il doit modifier **la bonne section sans réécrire ni corrompre tout le document**, et émettre ses modifications sous une forme que le code peut appliquer.

Dans la démo, c'est la note au comité de direction : à l'acte 2, seule la section « Rentabilité » change ; l'utilisatrice peut aussi modifier une section, revenir à une révision et exporter la note.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Le modèle réécrit tout le document** à chaque tour | Simple à demander | Un petit modèle perd ou abîme des sections qu'il n'avait pas à toucher ; aucun moyen de savoir ce qui a changé |
| **B. Patch texte libre** (diff unifié, recherche-remplacement) | Fin | Un modèle de 8 milliards de paramètres produit des diffs invalides ; difficile à valider |
| **C. Fichier Markdown sur disque** | Lisible directement | Pas de révisions ni de verrou ; deux écritures simultanées (agent et panneau) s'écrasent |
| **D. Sections adressées par identifiant, une opération par appel, verrou optimiste, révisions en base** | Chaque écriture est petite, validée par un schéma et réversible ; le conflit est détecté | Le modèle doit relire la note avant d'écrire |

## Décision

**Option D.**

- **La note vit dans `memory.db`**, à part des entrées de mémoire : elle n'apparaît ni dans `memory_search` ni dans le contexte injecté.
- **Une section et une opération par appel** de `document_patch` : `replace`, `append` ou `insert_after`, 4 000 caractères au plus. La note n'est jamais réécrite en entier. Suppression d'une section par `document_delete_section`, confirmée (ADR-004).
- **Verrou optimiste** : chaque écriture donne la révision lue (`base_revision`). Si la note a changé entre-temps, l'écriture échoue avec `REVISION_CONFLICT`, et le modèle relit la note.
- **Chaque écriture effective crée une révision** dans la même transaction, avec le contenu d'avant. Une écriture qui ne change rien n'en crée pas.
- **Revenir à une révision ne supprime rien** : `restore` écrit une nouvelle révision égale à l'ancienne.
- **L'interface passe par les mêmes outils** que l'agent (`document_tools(..., actor="user")`) : mêmes règles, même historique.

## Conséquences

- **Démontrable** : à l'acte 2, seule « Rentabilité » est surlignée « modifiée » dans le panneau Note ; la révision R1 est attribuée à l'agent, une modification au clavier à « vous ».
- **Les erreurs reviennent au modèle comme résultats d'outil**, jamais comme exceptions : il peut corriger son appel.
- **Export Markdown** à la demande (`GET /api/documents/current/export`), et la note est figée dans les liens de partage (X3).
- **Limite** : une seule note par installation (`note-comite`) ; plusieurs documents demanderaient de les nommer dans les outils.
