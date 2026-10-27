-- memory.db: the agent's own long-term memory (M3), separate from business.db.
-- Contract: docs/contracts/memory.md (MEM-01).
--
-- Conventions
--   * kind: every memory entry is addressed by (kind, ref). kind is one of
--     'glossary', 'entity', 'relation', 'fact'. ref is always text: the term for the
--     glossary, the integer id as text for the other kinds.
--   * Timestamps are UTC ISO 8601 text 'YYYY-MM-DDTHH:MM:SSZ' (no fraction, no offset).
--     The column default fills them; Python code uses sensai.memory.schema.now_iso().
--     Checked with strftime(...) IS x, not =, because strftime() returns NULL on a bad
--     format and a CHECK that evaluates to NULL passes.
--   * JSON columns are checked with CASE so that malformed JSON fails the CHECK
--     instead of raising "malformed JSON" from json_type().
--   * memory_fts is kept in sync by the triggers at the end of this file: the store
--     never writes to it directly.
--   * Foreign keys are only enforced on connections that run PRAGMA foreign_keys = ON.
--     Open the database with sensai.memory.schema.connect(), which turns it on.

PRAGMA foreign_keys = ON;
PRAGMA user_version = 2;  -- 2: committee note tables (M4), migration_v2.sql

CREATE TABLE entities (
  id            INTEGER PRIMARY KEY,
  type          TEXT NOT NULL
                CHECK (type IN ('client','person','product','metric','team','other')),
  name          TEXT NOT NULL COLLATE NOCASE CHECK (trim(name) <> ''),
  aliases       TEXT NOT NULL DEFAULT '[]'
                CHECK (CASE WHEN json_valid(aliases) THEN json_type(aliases) = 'array' ELSE 0 END),
  external_ref  TEXT                      -- business.db row as '<table>:<id>', e.g. 'clients:42'
                CHECK (external_ref IS NULL OR (
                  external_ref GLOB '[a-z]*:[0-9]*'
                  AND substr(external_ref, 1, instr(external_ref, ':') - 1) NOT GLOB '*[^a-z_]*'
                  AND substr(external_ref, instr(external_ref, ':') + 1) NOT GLOB '*[^0-9]*')),
  attributes    TEXT NOT NULL DEFAULT '{}'
                CHECK (CASE WHEN json_valid(attributes) THEN json_type(attributes) = 'object' ELSE 0 END),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at),
  UNIQUE (type, name)
);

CREATE TABLE relations (
  id          INTEGER PRIMARY KEY,
  src_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  type        TEXT NOT NULL CHECK (type GLOB '[a-z]*' AND type NOT GLOB '*[^a-z_]*'),  -- snake_case verb, e.g. 'managed_by'
  dst_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  attributes  TEXT NOT NULL DEFAULT '{}'
              CHECK (CASE WHEN json_valid(attributes) THEN json_type(attributes) = 'object' ELSE 0 END),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at),
  CHECK (src_id <> dst_id),
  UNIQUE (src_id, type, dst_id)
);

CREATE TABLE facts (
  id          INTEGER PRIMARY KEY,
  entity_id   INTEGER REFERENCES entities(id) ON DELETE CASCADE,  -- NULL = general fact
  category    TEXT NOT NULL CHECK (category IN ('preference','business_rule','note')),
  content     TEXT NOT NULL CHECK (trim(content) <> ''),
  source      TEXT NOT NULL CHECK (source IN ('user','agent')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at)
);

CREATE TABLE glossary (
  term        TEXT PRIMARY KEY COLLATE NOCASE CHECK (trim(term) <> ''),
  definition  TEXT NOT NULL CHECK (trim(definition) <> ''),
  sql_hint    TEXT,                       -- advised SQL fragment, e.g. 'marge / ca > 0.25'
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at)
);

-- Written by the store in the same transaction as the change it records.
-- Rows are never updated nor deleted.
CREATE TABLE memory_audit (
  id          INTEGER PRIMARY KEY,
  op          TEXT NOT NULL CHECK (op IN ('create','update','delete')),
  kind        TEXT NOT NULL CHECK (kind IN ('glossary','entity','relation','fact')),
  ref         TEXT NOT NULL,
  before      TEXT CHECK (before IS NULL OR json_valid(before)),  -- row as JSON, NULL on create
  after       TEXT CHECK (after IS NULL OR json_valid(after)),    -- row as JSON, NULL on delete
  actor       TEXT NOT NULL CHECK (actor IN ('agent','user')),
  session_id  TEXT,
  at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
              CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', at) IS at),
  CHECK ((op = 'create') = (before IS NULL)),
  CHECK ((op = 'delete') = (after IS NULL))
);

-- Committee note (M4, memory.md §7): a document written section by section, apart from
-- the memory entries (DocumentStore). Every effective write bumps documents.revision and
-- adds its document_revisions row in the same transaction; rows are never changed.
CREATE TABLE documents (
  id          TEXT PRIMARY KEY,            -- 'note-comite'
  title       TEXT NOT NULL CHECK (trim(title) <> ''),
  revision    INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_at  TEXT NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', created_at) IS created_at),
  updated_at  TEXT NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at)
);

CREATE TABLE document_sections (
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id  TEXT NOT NULL                -- 'rentabilite'
              CHECK (section_id GLOB '[a-z0-9]*' AND section_id NOT GLOB '*[^a-z0-9-]*'
                     AND length(section_id) <= 64),
  position    INTEGER NOT NULL,
  title       TEXT NOT NULL CHECK (trim(title) <> ''),
  content     TEXT NOT NULL DEFAULT '',
  updated_at  TEXT NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', updated_at) IS updated_at),
  PRIMARY KEY (doc_id, section_id)
);

CREATE TABLE document_revisions (
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision    INTEGER NOT NULL CHECK (revision >= 1),
  op          TEXT NOT NULL CHECK (op IN ('replace','append','insert_after','delete','restore')),
  section_id  TEXT,                        -- NULL for restore
  before      TEXT CHECK (before IS NULL OR json_valid(before)),  -- section (or note) before
  after       TEXT CHECK (after IS NULL OR json_valid(after)),    -- section (or note) after
  actor       TEXT NOT NULL CHECK (actor IN ('agent','user')),
  session_id  TEXT,
  at          TEXT NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', at) IS at),
  PRIMARY KEY (doc_id, revision),
  CHECK ((op = 'restore') = (section_id IS NULL))
);

CREATE INDEX idx_relations_src ON relations(src_id);
CREATE INDEX idx_relations_dst ON relations(dst_id);
CREATE INDEX idx_facts_entity ON facts(entity_id);
CREATE INDEX idx_memory_audit_entry ON memory_audit(kind, ref);

-- Full-text search over glossary, entities (name + aliases) and facts.
-- remove_diacritics 2: 'delai' matches 'délai'.
CREATE VIRTUAL TABLE memory_fts USING fts5(
  kind UNINDEXED,
  ref UNINDEXED,
  text,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER glossary_fts_insert AFTER INSERT ON glossary BEGIN
  INSERT INTO memory_fts (kind, ref, text)
  VALUES ('glossary', new.term, new.term || ' ' || new.definition);
END;

CREATE TRIGGER glossary_fts_update AFTER UPDATE ON glossary BEGIN
  DELETE FROM memory_fts WHERE kind = 'glossary' AND ref = old.term;
  INSERT INTO memory_fts (kind, ref, text)
  VALUES ('glossary', new.term, new.term || ' ' || new.definition);
END;

CREATE TRIGGER glossary_fts_delete AFTER DELETE ON glossary BEGIN
  DELETE FROM memory_fts WHERE kind = 'glossary' AND ref = old.term;
END;

CREATE TRIGGER entities_fts_insert AFTER INSERT ON entities BEGIN
  INSERT INTO memory_fts (kind, ref, text)
  VALUES ('entity', CAST(new.id AS TEXT),
          new.name || ' ' || coalesce((SELECT group_concat(value, ' ') FROM json_each(new.aliases)), ''));
END;

CREATE TRIGGER entities_fts_update AFTER UPDATE ON entities BEGIN
  DELETE FROM memory_fts WHERE kind = 'entity' AND ref = CAST(old.id AS TEXT);
  INSERT INTO memory_fts (kind, ref, text)
  VALUES ('entity', CAST(new.id AS TEXT),
          new.name || ' ' || coalesce((SELECT group_concat(value, ' ') FROM json_each(new.aliases)), ''));
END;

CREATE TRIGGER entities_fts_delete AFTER DELETE ON entities BEGIN
  DELETE FROM memory_fts WHERE kind = 'entity' AND ref = CAST(old.id AS TEXT);
END;

CREATE TRIGGER facts_fts_insert AFTER INSERT ON facts BEGIN
  INSERT INTO memory_fts (kind, ref, text) VALUES ('fact', CAST(new.id AS TEXT), new.content);
END;

CREATE TRIGGER facts_fts_update AFTER UPDATE ON facts BEGIN
  DELETE FROM memory_fts WHERE kind = 'fact' AND ref = CAST(old.id AS TEXT);
  INSERT INTO memory_fts (kind, ref, text) VALUES ('fact', CAST(new.id AS TEXT), new.content);
END;

-- Also fires when a fact is removed by ON DELETE CASCADE from its entity.
CREATE TRIGGER facts_fts_delete AFTER DELETE ON facts BEGIN
  DELETE FROM memory_fts WHERE kind = 'fact' AND ref = CAST(old.id AS TEXT);
END;
