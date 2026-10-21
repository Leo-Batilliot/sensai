# Contrat — Mémoire de l'agent (`memory.db`) et outils mémoire

| | |
|---|---|
| **Tâche** | MEM-01 (PLANNING §7, sprint 1) |
| **Propriétaire** | P3 — Mémoire & Évaluation |
| **Consommateurs** | P1 (registry, injection dans le prompt, événements), P4 (panneau mémoire, `GET /api/memory`, `ui_action` WS), P2 (politique HITL) |
| **Statut** | v1 gelée le mar 06/10 ; **v1.1** (note du comité, §7) gelée le **mer 21/10 à 18 h** (ajouts uniquement) |
| **Fait foi** | [`src/sensai/memory/schema.sql`](../../src/sensai/memory/schema.sql) pour le schéma, [`schemas/memory_tools.v1.json`](schemas/memory_tools.v1.json) pour les outils mémoire, [`schemas/document_tools.v1.json`](schemas/document_tools.v1.json) pour les outils de la note (§7) |
| **Spécification** | PROJECT.md §6.4 (M3), §6.17 (M4) |

> À relier depuis `tools.md` (P1) : ce document en est la section « Outils mémoire ». Il est séparé pour éviter un conflit sur un fichier que P1 rédige en parallèle.

---

## 1. Principes

1. **La mémoire appartient à l'agent**, dans une base dédiée `memory.db`, jamais dans `business.db` : les données de l'entreprise et celles de l'agent n'ont ni les mêmes droits, ni le même cycle de vie.
2. **Toute entrée s'adresse par `(kind, ref)`** — dans les outils, les événements, le WebSocket et l'audit.
   - `kind` ∈ `glossary`, `entity`, `relation`, `fact` (tranche le point ouvert **P-8** de `websocket.md`).
   - `ref` est **toujours une chaîne** : le `term` pour le glossaire, l'`id` entier converti en texte pour les autres.
3. **Chaque écriture est auditée** dans `memory_audit`, dans la même transaction que l'écriture.
4. **Les outils mémoire sont des outils comme les autres** : mêmes champs que les outils MCP (`name`, `description`, `inputSchema`, `annotations`), pour que la validation des arguments (T5) et la politique HITL (A3) s'appliquent sans cas particulier. Seule la suppression demande une confirmation.

## 2. Schéma `memory.db`

Le DDL complet est dans `schema.sql` ; ce tableau en est le résumé.

| Table | `kind` | `ref` | Colonnes métier | Modifiable par `patch` |
|---|---|---|---|---|
| `glossary` | `glossary` | `term` | `term` (clé, insensible à la casse), `definition`, `sql_hint` | `term`, `definition`, `sql_hint` |
| `entities` | `entity` | `id` | `type` (`client`, `person`, `product`, `metric`, `team`, `other`), `name` (unique par type, insensible à la casse), `aliases` (tableau JSON), `external_ref` (`clients:42`), `attributes` (objet JSON) | `type`, `name`, `aliases`, `external_ref`, `attributes` |
| `relations` | `relation` | `id` | `src_id`, `type` (snake_case, ex. `managed_by`), `dst_id`, `attributes` | `type`, `attributes` |
| `facts` | `fact` | `id` | `entity_id` (nullable), `category` (`preference`, `business_rule`, `note`), `content`, `source` (`user`, `agent`) | `content`, `category`, `entity` |
| `memory_audit` | — | — | `op`, `kind`, `ref`, `before`, `after`, `actor` (`agent`, `user`), `session_id`, `at` | jamais (journal en ajout seul) |
| `memory_fts` | — | — | index FTS5 `(kind, ref, text)`, alimenté **uniquement par des triggers** | — |

**Conventions**
- Horodatages : UTC, texte `YYYY-MM-DDTHH:MM:SSZ`, remplis par défaut par la base. Côté Python, utiliser `sensai.memory.schema.now_iso()` (`isoformat()` ajoute des microsecondes et `+00:00` et serait refusé).
- `patch` **remplace** les champs donnés (un `patch` sur `aliases` ou `attributes` remplace toute la valeur). `id`, `created_at`, `updated_at` ne sont jamais modifiables ; le store met `updated_at` à jour.
- Suppressions en cascade : supprimer une entité supprime ses relations et ses faits. Chaque entrée supprimée par cascade a sa propre ligne d'audit.
- `memory_fts` indexe le glossaire (`term` + `definition`), les entités (`name` + alias) et les faits (`content`), sans accents (`delai` trouve « délai »). Les relations ne sont pas indexées : on les atteint par leurs entités.
- `PRAGMA user_version = 1` : version du schéma, pour une migration éventuelle.
- Ouverture : `sensai.memory.schema.connect()` (active les clés étrangères, sans quoi les cascades ne s'appliquent pas).

**Écarts volontaires avec PROJECT §6.4**
- `memory_audit.table_name` → `kind`, `row_id` → `ref` : mêmes valeurs que dans les outils et le WebSocket.
- `relations.updated_at` ajouté (une relation peut être modifiée par `memory_update`).
- `memory_fts` : colonnes `kind` et `ref` déclarées `UNINDEXED`, tokenizer `unicode61 remove_diacritics 2`.
- Contraintes `CHECK` ajoutées (formats d'horodatage, JSON valide, `src_id <> dst_id`, cohérence `op` / `before` / `after`).

## 3. Outils exposés à l'agent

Définitions complètes (descriptions vues par le modèle, `inputSchema`, annotations) : `schemas/memory_tools.v1.json`.

| Outil | Rôle | `readOnly` | `destructive` | `idempotent` | Confirmation |
|---|---|:---:|:---:|:---:|:---:|
| `memory_search(query, kind?, limit?)` | Recherche FTS5 dans glossaire, entités, faits | ✅ | — | ✅ | Non |
| `glossary_define(term, definition, sql_hint?)` | Crée ou remplace une définition | — | — | ✅ | Non |
| `memory_upsert_entity(type, name, aliases?, attributes?, external_ref?)` | Crée ou complète une entité | — | — | ✅ | Non |
| `memory_relate(src, type, dst)` | Relie deux entités | — | — | ✅ | Non |
| `memory_add_fact(content, category, entity?)` | Ajoute un fait | — | — | — | Non |
| `memory_update(kind, ref, patch)` | Modifie une entrée | — | — | ✅ | Non (audité) |
| `memory_delete(kind, ref)` | Supprime une entrée | — | ✅ | ✅ | **Oui** |

> **Les quatre annotations sont toujours déclarées explicitement.** Dans la spécification MCP, `destructiveHint` et `openWorldHint` valent `true` par défaut quand ils sont absents : un outil d'écriture sans `destructiveHint: false` serait traité comme destructif par la politique HITL (P2), et l'acte 2 serait interrompu par une confirmation.

### 3.1 Désignation d'une entité (`src`, `dst`, `entity`)

L'agent désigne une entité par son **nom**, un **alias** ou sa **ref** (id en texte). Résolution, dans cet ordre :
1. chaîne de chiffres → `id` ;
2. nom exact, insensible à la casse ;
3. alias exact, insensible à la casse.

Plusieurs candidats (ex. un client et une personne nommés pareil) → erreur `ambiguous_entity` avec la liste ; aucun → `entity_not_found`.

« Insensible à la casse » est appliqué **en Python** (`str.casefold()`) par le store : `COLLATE NOCASE` ne replie que l'ASCII, et « ÉLAN » ≠ « élan » pour SQLite. La contrainte `UNIQUE` de la base n'est qu'un filet de sécurité.

Les champs optionnels des outils sont **omis**, jamais `null` : les `inputSchema` n'ont pas de type nullable (point M-4).

`memory_upsert_entity` considère qu'une entité existe déjà si, **pour le même `type`**, son nom ou l'un de ses alias est égal au `name` ou à l'un des `aliases` fournis. Elle est alors complétée (alias ajoutés, attributs fusionnés clé par clé) au lieu d'être dupliquée : « Lemaire » et « Brasserie Lemaire » restent une seule entité (US-M3-02).

### 3.2 Résultats

Les résultats sont renvoyés au modèle en texte JSON, et dans `ToolResult.data` pour le moteur.

```json
// memory_search
{"results": [
  {"kind": "glossary", "ref": "client rentable", "label": "client rentable",
   "snippet": "client rentable : marge brute > 25 % sur la période", "sql_hint": "marge / ca > 0.25"},
  {"kind": "entity", "ref": "12", "label": "Brasserie Lemaire", "snippet": "Brasserie Lemaire (Lemaire, BL)"}
]}

// écritures (glossary_define, memory_upsert_entity, memory_relate, memory_add_fact, memory_update, memory_delete)
{"op": "create", "kind": "glossary", "ref": "client rentable",
 "summary": "Terme « client rentable » défini"}
```

- `op` ∈ `create`, `update`, `delete`, `unchanged`. `unchanged` (ex. même définition renvoyée deux fois) n'écrit rien, ni dans l'audit, ni en événement.
- `memory_search` renvoie les résultats triés par pertinence (`bm25`), `limit` 5 par défaut.

### 3.3 Recherche : le texte brut n'atteint jamais `MATCH`

Une apostrophe (« chiffre d'affaires »), un `?` ou un `-` sont de la syntaxe FTS5 : passé tel quel, le texte de l'utilisateur provoque une erreur. Et `unicode61` ne fait pas de racinisation : « clients rentables » ne trouve pas « client rentable ». Le store (MEM-02) construit donc la requête lui-même, pour `memory_search` comme pour l'injection du contexte (MEM-04) :

1. découpe en mots (lettres et chiffres Unicode), mise en minuscules ;
2. retrait des mots vides français courts (`de`, `la`, `les`, `nos`, `quel`…) et des mots d'une lettre ;
3. retrait d'un `s` ou `x` final sur les mots de plus de 3 lettres ;
4. chaque mot entre guillemets avec un préfixe `*`, reliés par `OR` : « Quels sont nos clients rentables ? » → `"client"* OR "rentable"*`.

Le glossaire étant court, MEM-04 peut aussi rapprocher les termes en Python sur le texte normalisé, en plus de FTS5.

### 3.4 Erreurs

Une erreur d'exécution est un résultat d'outil en erreur (équivalent de `isError: true` en MCP), renvoyé au modèle pour qu'il corrige, jamais une exception jusqu'à l'utilisateur.

| `code` | Quand |
|---|---|
| `not_found` | `(kind, ref)` inexistant (`memory_update`, `memory_delete`), y compris depuis l'interface : pas de code WS séparé, l'erreur arrive dans un `tool_result` en échec |
| `entity_not_found` | `src`, `dst` ou `entity` ne désigne aucune entité |
| `ambiguous_entity` | Plusieurs entités correspondent ; le message liste les candidats `(type, name, ref)` |
| `invalid_patch` | Champ de `patch` inconnu ou non modifiable pour ce `kind` (§2), ou valeur refusée par une contrainte |
| `conflict` | Violation d'unicité (renommage vers un nom déjà pris, relation déjà existante) |

Les arguments mal formés (type, champ requis manquant) sont refusés **avant** l'appel par la validation `jsonschema` du registry (T5), pas par le store.

## 4. Événements et HITL

- Après chaque écriture effective (`op` ≠ `unchanged`), le moteur émet `memory_updated` avec `op`, `kind`, `ref`, `summary`.
  **À aligner dans `events.md` (P1)** : PROJECT §7.1 nomme ce champ `id` ; ce contrat propose `ref` (chaîne), comme le WebSocket.
- `memory_delete` passe par la politique HITL. L'aperçu d'impact suit la forme de `confirmation_required.impact`, cascades comprises :

  ```json
  {"operation": "DELETE", "rows_affected": 1,
   "per_table": {"entities": -1, "relations": -2, "facts": -3},
   "sample": [{"kind": "entity", "ref": "12", "label": "Brasserie Lemaire"}],
   "blocked": []}
  ```

- **Actions lancées depuis l'interface** (point **P-6**, réponse de P1) : message WS `ui_action {tool, arguments}` → `Engine.run_ui_action`, un générateur asynchrone traité comme un tour.
  - Liste blanche : seuls `memory_update` et `memory_delete` sont appelables depuis l'interface. Leurs arguments sont ceux du §3 (`kind`, `ref`, `patch`), passés tels quels.
  - Le tour commence par `turn_started {origin: "ui", user_message: null}` (point **P-5**), avec un `turn_id` normal.
  - Même politique HITL et même broker que la session : `memory_delete` demande donc une confirmation.
  - Audit avec `actor = 'user'` et le `session_id`.
  - Refus avec `BUSY` si un tour est en cours ; rien n'est ajouté à l'historique de conversation.
- **Injection du contexte** (MEM-04, point **P-2**, accepté par P1) : événement `context_injected {refs: [{kind, ref, label}]}`, émis juste après `turn_started` et seulement s'il y a des entrées.
  - `label` : le terme pour le glossaire, le nom pour une entité, le début du contenu pour un fait.
  - Il dit ce qui a été **fourni** au modèle, pas ce qu'il a appliqué : libellé d'interface « contexte mémoire fourni ».
  - Le harness s'en sert pour la comparaison avec et sans mémoire.

## 5. `GET /api/memory` (P4)

Renvoie les colonnes du §2, `aliases` et `attributes` décodés en JSON, plus `ref` pour chaque entrée :

```json
{"glossary": [{"ref": "client rentable", "term": "client rentable", "definition": "…", "sql_hint": "…", "updated_at": "2026-10-12T09:14:03Z"}],
 "entities": [{"ref": "12", "type": "client", "name": "Brasserie Lemaire", "aliases": ["Lemaire", "BL"], "external_ref": "clients:42", "attributes": {}, "updated_at": "…"}],
 "relations": [{"ref": "3", "src": "12", "type": "managed_by", "dst": "7", "attributes": {}, "updated_at": "…"}],
 "facts": [{"ref": "17", "entity": "12", "category": "preference", "content": "…", "source": "user", "updated_at": "…"}]}
```

## 6. Points tranchés et points ouverts

| # | Sujet | Statut |
|---|---|---|
| P-8 (WS) | Valeurs de `kind` | ✅ `glossary`, `entity`, `relation`, `fact` |
| P-6 (WS) | Actions mémoire depuis l'interface | ✅ `ui_action` → `Engine.run_ui_action`, liste blanche `memory_update` / `memory_delete` (§4) ; `ui_action` à ajouter dans `websocket.md` (P4) |
| M-1 | `memory_updated.id` → `ref` dans `events.md` | À valider par P1 |
| M-2 | Événement `context_injected` (P-2) | ✅ Accepté par P1, forme du §4 |
| M-3 | Lien depuis `tools.md` vers ce document | À faire par P1 |
| M-4 | Types nullables (`["string", "null"]`) dans le paramètre `tools` d'Ollama | Évités par précaution : optionnel = omis. Un modèle qui envoie `null` déclenche une réparation (T5) |
| V-2 (v1.1) | Où vit la note du comité | Proposition retenue : `memory.db`, `DocumentStore` séparé (§7.1). À confirmer à ALL-15 |
| M-5 | `ToolResult.document_change` pour que le moteur émette `document_updated` (§7.3) | À ajouter par P1 dans `tools.md` §3.4 avec l'événement (`events.md`), même forme que `memory_change` |
| M-6 | `section_id` d'une section créée par `insert_after` : calculé par le store ou donné par le modèle | Calculé par le store à partir du titre (§7.2) : le modèle ne peut pas créer de doublon |

## 7. Note du comité (M4, contrats v1.1)

Reporté de [`v1.1-extensions.md`](v1.1-extensions.md) §3.4 (décision [D-06](../decisions.md)). Implémentation : ARTIF-01 (P3), panneau Note : WEB-17 (P4). Spécification : PROJECT.md §6.17.

### 7.1 Principes

1. **Même base, autre store** (point V-2) : la note vit dans `memory.db`, la seule base de l'agent à remettre à zéro (`make demo-reset`), mais derrière un `DocumentStore` séparé du `MemoryStore` : la note n'est pas une entrée de mémoire, elle n'a ni `kind` ni `ref`, et n'apparaît ni dans `memory_search`, ni dans `context_injected`, ni dans `GET /api/memory`.
2. **Une section par appel.** Le modèle ne réécrit jamais toute la note : chaque écriture porte sur une seule section, 4 000 caractères au plus.
3. **Tout est versionné.** Chaque écriture effective incrémente `documents.revision` et ajoute une ligne à `document_revisions`, dans la même transaction. Revenir en arrière crée une nouvelle révision (`restore`), sans rien effacer.
4. **Verrou optimiste.** Chaque écriture donne la révision sur laquelle elle porte (`base_revision`) : si la note a changé entre-temps (par l'interface, par exemple), l'écriture échoue et le modèle relit avant de recommencer.
5. Une seule note active en v1 : `note-comite`, créée vide à partir du modèle `[documents] template` au premier accès.

### 7.2 Schéma

Tables ajoutées à `memory.db` par ARTIF-01 (`schema.sql` fera foi, `PRAGMA user_version = 2`, migration depuis la version 1 : création des tables, rien d'autre ne change). Mêmes conventions que le §2 : horodatages UTC `YYYY-MM-DDTHH:MM:SSZ`, clés étrangères actives.

```sql
CREATE TABLE documents (
  id          TEXT PRIMARY KEY,            -- 'note-comite'
  title       TEXT NOT NULL,
  revision    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE document_sections (
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id  TEXT NOT NULL,               -- 'rentabilite'
  position    INTEGER NOT NULL,
  title       TEXT NOT NULL,
  content     TEXT NOT NULL DEFAULT '',
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (doc_id, section_id)
);

CREATE TABLE document_revisions (
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision    INTEGER NOT NULL,
  op          TEXT NOT NULL CHECK (op IN ('replace','append','insert_after','delete','restore')),
  section_id  TEXT,
  before      TEXT,                        -- JSON de la section avant
  after       TEXT,                        -- JSON de la section après
  actor       TEXT NOT NULL,               -- 'agent' | 'user'
  session_id  TEXT,
  at          TEXT NOT NULL,
  PRIMARY KEY (doc_id, revision)
);
```

- `section_id` : minuscules, chiffres et `-`, 64 caractères au plus (`rentabilite`, `top-clients`). Celui d'une section créée par `insert_after` est calculé par le store à partir du titre (sans accents, espaces → `-`, suffixe `-2`, `-3`… en cas de doublon).
- `position` : entiers croissants ; `insert_after` décale les sections suivantes.
- `before` / `after` : la section entière en JSON (`{section_id, position, title, content}`) ; `before` est `null` pour `insert_after`, `after` est `null` pour `delete`. Pour `restore`, `section_id` est `null` et `before` / `after` contiennent la note entière (`{title, sections: [...]}`).

### 7.3 Outils

Définitions complètes (descriptions vues par le modèle, `inputSchema`, annotations) : [`schemas/document_tools.v1.json`](schemas/document_tools.v1.json). Outils natifs, sans préfixe ; les quatre annotations sont déclarées explicitement, comme pour la mémoire (§3).

| Outil | Arguments | `readOnly` | `destructive` | Confirmation | Proposé au modèle |
|---|---|:---:|:---:|:---:|:---:|
| `document_read` | `{section_id?}` | ✅ | — | Non | ✅ |
| `document_patch` | `{section_id, op, content, title?, base_revision}` | — | — | Non (réversible, versionné) | ✅ |
| `document_delete_section` | `{section_id, base_revision}` | — | ✅ | **Oui** | ✅ |
| `document_restore` | `{revision}` | — | — | Non (crée une révision) | **Non** : `ui_action` uniquement (`"ui_only": true` dans le schéma) |

- `op` de `document_patch` : `replace` (nouveau contenu de la section), `append` (ajout en fin de section, séparé par une ligne vide), `insert_after` (nouvelle section après `section_id`, `title` obligatoire, `content` est son contenu). `title` est ignoré pour `replace` et `append`.
- `document_delete_section` passe par la politique HITL ; son aperçu suit la forme de `confirmation_required.impact` :

  ```json
  {"operation": "DELETE", "rows_affected": 1,
   "per_table": {"document_sections": -1},
   "sample": [{"section_id": "risques", "title": "Risques", "chars": 812}],
   "blocked": []}
  ```

**Résultats** (texte JSON pour le modèle, `ToolResult.data` pour le moteur) :

```json
// document_read sans argument : le plan
{"doc_id": "note-comite", "title": "Note au comité de direction", "revision": 4,
 "sections": [{"section_id": "synthese", "position": 1, "title": "Synthèse", "chars": 640},
              {"section_id": "rentabilite", "position": 2, "title": "Rentabilité", "chars": 1210}]}

// document_read avec section_id
{"doc_id": "note-comite", "revision": 4, "section_id": "rentabilite", "title": "Rentabilité",
 "content": "Les 5 clients les plus rentables du trimestre…"}

// écritures (document_patch, document_delete_section, document_restore)
{"doc_id": "note-comite", "revision": 5, "op": "replace", "section_id": "rentabilite",
 "summary": "Section « Rentabilité » mise à jour (révision 5)"}
```

- Après chaque écriture réussie, le moteur émet `document_updated {doc_id, revision, section_id, op, summary}` (événement de P1, `events.md`), comme `memory_updated`. Le résultat d'outil porte pour cela un `document_change` dans `ToolResult` (même rôle que `memory_change`, `tools.md` §3.4).
- **Plan injecté** : à chaque tour, le plan de la note (titres et révision, une ligne par section, sans les contenus) est ajouté au prompt système dans un budget fixe (M2), pour que le modèle connaisse `section_id` et `base_revision` sans appel. Le contenu d'une section ne s'obtient que par `document_read`.

### 7.4 Erreurs

Comme au §3.4 : un résultat d'outil en erreur, renvoyé au modèle, jamais une exception.

| `code` | Quand | Le modèle doit |
|---|---|---|
| `REVISION_CONFLICT` | `base_revision` ≠ révision courante. Le message donne la révision courante | relire (`document_read`) puis recommencer |
| `section_not_found` | `section_id` absent de la note (ou `revision` inconnue pour `document_restore`) | relire le plan |
| `content_too_long` | `content` dépasse 4 000 caractères (filet de sécurité : le `inputSchema` le refuse déjà) | découper en plusieurs `append` |
| `invalid_patch` | `insert_after` sans `title` | redonner un titre |

`REVISION_CONFLICT` est en majuscules, comme les codes des outils de `tools.md` §8, parce que l'interface l'affiche (édition concurrente depuis le panneau) ; les autres codes suivent la convention de la mémoire.

### 7.5 Interface (P4)

- `ui_action` : la liste blanche du §4 gagne `document_patch` (édition depuis le panneau, `actor = 'user'`) et `document_restore`. Même broker et même politique que la session.
- Routes REST en lecture seule (`websocket.md` §9, P4) : `GET /api/documents/current` (`{id, title, revision, sections: [{section_id, position, title, content, updated_at}]}`), `GET /api/documents/current/revisions` (`{revisions: [{revision, op, section_id, actor, at}]}`), `GET /api/documents/current/export?format=md`.
- `session_ready.document_id` (P4) : `"note-comite"`.

## Validation

| Rôle | Nom | Validé le |
|---|---|---|
| P3 — propriétaire | | |
| P1 — moteur | | |
| P2 — politique HITL | | |
| P4 — interface | | |
