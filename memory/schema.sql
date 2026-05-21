CREATE TABLE IF NOT EXISTS memory_items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT,
  summary TEXT,
  content TEXT,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  vault_path TEXT,
  session_id TEXT,
  platform TEXT,
  chat_id TEXT,
  user_id TEXT,
  project_key TEXT,
  importance REAL DEFAULT 0.5,
  confidence REAL DEFAULT 0.8,
  status TEXT DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_links (
  id TEXT PRIMARY KEY,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  link_type TEXT NOT NULL,
  weight REAL DEFAULT 1.0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_observations (
  id TEXT PRIMARY KEY,
  memory_id TEXT NOT NULL,
  observation TEXT NOT NULL,
  reinforcement_score REAL DEFAULT 0.0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_notes (
  path TEXT PRIMARY KEY,
  title TEXT,
  aliases_json TEXT,
  tags_json TEXT,
  headings_json TEXT,
  summary TEXT,
  hash TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_sections (
  id TEXT PRIMARY KEY,
  note_path TEXT NOT NULL,
  heading TEXT,
  level INTEGER,
  content TEXT,
  summary TEXT,
  position INTEGER,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS vault_notes_fts
USING fts5(
  path,
  title,
  summary,
  tags,
  aliases,
  content='',
  tokenize='unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS vault_sections_fts
USING fts5(
  section_id UNINDEXED,
  note_path,
  heading,
  summary,
  content,
  content='',
  tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_items(kind);
CREATE INDEX IF NOT EXISTS idx_memory_project ON memory_items(project_key);
CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory_items(updated_at);
CREATE INDEX IF NOT EXISTS idx_links_from ON memory_links(from_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_id);
CREATE INDEX IF NOT EXISTS idx_vault_note_updated ON vault_notes(updated_at);
CREATE INDEX IF NOT EXISTS idx_vault_sections_note_path ON vault_sections(note_path);

