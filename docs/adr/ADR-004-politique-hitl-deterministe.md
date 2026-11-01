# ADR-004 — Politique de confirmation : classification déterministe, annotations, refus par défaut

- **Statut** : acceptée
- **Date** : 11/10/2026 (HITL-01, HITL-02) ; profils ajoutés le 27/10/2026 (PERS-01) ; rédigée le 01/11/2026
- **Auteur** : P2 (T.BE) ; rédaction finale P4 (A.P), à partir de PROJECT.md et du code
- **Voir aussi** : `src/sensai/tools/policy.py`, `src/sensai/mcp_servers/sql_toolbox/guard.py`, [ADR-009](ADR-009-profil-restreint-seulement.md), [`docs/user-stories/A3.md`](../user-stories/A3.md), [`docs/SECURITY.md`](../SECURITY.md)

## Contexte

A3 demande que l'agent s'arrête et attende l'accord de l'utilisateur avant une action critique ou irréversible : à l'acte 4, Karim supprime des clients inactifs. Il faut donc décider, avant chaque appel d'outil, s'il s'exécute, s'il attend une confirmation ou s'il est refusé.

Le piège : un outil qui s'appelle `run_select` peut recevoir un `DELETE`, un serveur tiers peut se déclarer « lecture seule » sans l'être, et le modèle peut se tromper ou subir une injection de prompt.

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Demander au modèle** si l'action est dangereuse | Aucune règle à écrire | Le modèle qui veut agir est aussi celui qui juge : une injection suffit à tout lever |
| **B. Liste de noms d'outils** à confirmer | Simple, prévisible | Ne voit pas ce que fait vraiment une requête SQL ; un outil tiers inconnu passe |
| **C. Mots-clés SQL** (`DELETE`, `DROP`…) | Rapide | Contournable (CTE, commentaires, casse, `REPLACE INTO`) |
| **D. Politique déterministe en couches, la plus stricte gagne** | Ne dépend pas du modèle ; SQLite lui-même dit ce que fait la requête ; ce qui n'est pas prouvé sûr est confirmé | Plus de code ; quelques confirmations prudentes sur des outils tiers |

## Décision

**Option D**, dans `policy.evaluate`, avant chaque appel :

1. **Le profil actif** : un outil hors profil est refusé (`TOOL_DENIED`), même inventé par le modèle (ADR-009).
2. **La configuration** : `[hitl] deny` refuse, `[hitl] always_confirm` impose une confirmation.
3. **Ce que la requête SQL ferait vraiment** : SQLite prépare la requête derrière `EXPLAIN` (rien ne s'exécute) et un *authorizer* liste chaque action. Une action hors liste blanche est refusée (`SQL_FORBIDDEN`) ; une écriture demande une confirmation.
4. **Les annotations MCP** (`readOnlyHint`, `destructiveHint`) : crues seulement pour les serveurs de confiance (`sql_toolbox`). Un outil tiers non déclaré en lecture seule est confirmé.
5. **Refus par défaut** : si rien ne dit que l'outil est sans effet, il est confirmé.

Chaque règle donne sa raison en français, affichée dans la fenêtre de confirmation. La plus stricte gagne : refus, puis confirmation, puis autorisation. Sans réponse avant `confirmation_timeout_s` (120 s), l'action est refusée.

## Conséquences

- **Le modèle ne décide jamais** de la confirmation : l'acte 4 s'arrête sur la fenêtre « Confirmer la suppression de N lignes », avec l'aperçu de l'impact simulé puis annulé.
- **Mesuré** : rappel destructif de 1,00 avec la configuration livrée (aucune écriture sans confirmation, [`EVALUATION.md`](../EVALUATION.md) §3) et aucun faux positif sur les questions en lecture. Les attaques de la suite adversariale sont arrêtées par cette couche ([`SECURITY.md`](../SECURITY.md)).
- **Les lectures restent sans friction** : une requête que SQLite classe en lecture s'exécute sur une connexion verrouillée trois fois (fichier en lecture seule, `PRAGMA query_only`, authorizer).
- **Coût** : un serveur tiers peu annoté demande des confirmations à chaque appel ; pour l'éviter, il faut le déclarer de confiance, ce qui est une décision d'administrateur.
