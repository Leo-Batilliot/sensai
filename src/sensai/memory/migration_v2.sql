-- memory.db v1 -> v2 (ARTIF-01): adds the committee note tables, nothing else changes.
-- Must create exactly what schema.sql creates for them (tested).

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

PRAGMA user_version = 2;
