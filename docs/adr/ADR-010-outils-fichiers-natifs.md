# ADR-010 — Outils fichiers natifs derrière notre couche de permissions, plutôt que le serveur MCP `filesystem`

- **Statut** : acceptée
- **Date** : 20/10/2026 (décision D-03) ; mise en œuvre le 24/10/2026 (FILE-01) ; rédigée le 01/11/2026
- **Auteur** : P2 (T.BE) ; rédaction finale P4 (A.P), à partir de PROJECT.md §6.9, de D-03 et du code
- **Voir aussi** : `src/sensai/tools/file_permissions.py`, `src/sensai/tools/file_tools.py`, [`docs/decisions.md`](../decisions.md) D-03, [`docs/user-stories/T4.md`](../user-stories/T4.md)

## Contexte

T4 demande des opérations sur les fichiers (lire, écrire, créer…) **et une couche de permissions** qui définit les fichiers et dossiers accessibles, vérifiée avant toute opération. À l'acte 5, Inès exporte la note du comité dans `reports/`, et une tentative d'écrire `config.toml` doit être refusée.

Jusqu'au 20/10, le plan prévoyait le serveur MCP officiel `@modelcontextprotocol/server-filesystem` (MCP-08).

## Options considérées

| Option | Avantages | Inconvénients |
|---|---|---|
| **A. Serveur MCP `filesystem` tiers** | Déjà écrit ; montre un deuxième serveur MCP existant | Ses dossiers autorisés sont une permission, mais **la sienne** : on ne la contrôle ni ne la teste ; dépend de Node et `npx` ; pas de motifs interdits ni de journal à nous |
| **B. Accès direct aux fichiers** dans les outils, sans couche commune | Simple | Chaque outil refait ses vérifications : un oubli suffit ; pas de point unique à tester |
| **C. Outils natifs (`file_list`, `file_read`, `file_write`) derrière une seule vérification** | Une seule porte, la nôtre, testée contre les attaques de chemin ; journal de chaque décision | Le « serveur MCP existant » de T1 reste `mcp-server-time` seul |

## Décision

**Option C.**

- **Racines déclarées** dans `[files]` : `reports/` en lecture-écriture, `data/imports/` en lecture seule. Rien n'est accessible en dehors.
- **Vérification sur le vrai chemin** : `FilePermissions.check()` suit les liens symboliques et résout `..` avant de comparer des chemins résolus, jamais des chaînes. `reports/../config.toml` ou un lien de `reports/` vers `/etc/passwd` sont refusés comme `/etc/passwd`.
- **Motifs toujours refusés**, même sous une racine : `.env`, `config.toml`, `*.db`, `*.sqlite`, `*.key`, `*.pem`. Extensions d'écriture limitées (`.md`, `.csv`, `.json`, `.txt`), taille de lecture plafonnée.
- **`file_write` est déclaré destructif** : la politique de confirmation (ADR-004) demande toujours l'accord, avec un aperçu (chemin, taille, fichier existant ou non, début du contenu).
- **Chaque décision est journalisée** dans un fichier d'audit.

## Conséquences

- **T4 est démontrable en direct** : export confirmé dans `reports/`, écriture de `config.toml` refusée avec sa raison.
- **Le serveur `filesystem` se branche toujours par configuration** (exemple commenté dans `config.example.toml`) pour qui le veut, mais ce n'est pas lui qui écrit les rapports de la démo.
- **Attaques de chemin testées** dans la suite adversariale ([`SECURITY.md`](../SECURITY.md)).
- **Limite** : ce n'est pas un bac à sable du système d'exploitation ; un processus lancé par un autre outil ne passerait pas par cette couche. Le bac à sable de l'OS est listé dans les évolutions.
