-- Modified by Yuna-Nexus on 2026-10-26; see NOTICE.md. SPDX-License-Identifier: AGPL-3.0-only
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  decision TEXT NOT NULL DEFAULT 'pending',
  zotero_key TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  batch_id TEXT,
  created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS identifiers (
  identifier TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id)
);
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  hash TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(id),
  action TEXT NOT NULL CHECK(action IN ('save','reject')),
  expires INTEGER NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'prepared',
  created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pending_papers ON papers(decision, batch_id, id);
CREATE INDEX IF NOT EXISTS token_paper ON tokens(paper_id);
CREATE TRIGGER IF NOT EXISTS single_batch BEFORE UPDATE OF batch_id ON papers
WHEN OLD.batch_id IS NOT NULL AND NEW.batch_id IS NOT NULL AND OLD.batch_id != NEW.batch_id
BEGIN SELECT RAISE(ABORT, 'paper already reserved'); END;
