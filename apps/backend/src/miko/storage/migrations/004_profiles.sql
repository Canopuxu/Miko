CREATE TABLE profile_entries (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL CHECK(subject IN ('miko','owner')),
    kind TEXT NOT NULL CHECK(kind IN ('overview','fact','recent','excerpt')),
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    friend TEXT NOT NULL DEFAULT '',
    as_of TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'owner_input',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK((account='' AND friend='') OR (account<>'' AND friend<>''))
);
CREATE VIRTUAL TABLE profile_fts USING fts5(entry_id UNINDEXED, tokens);
CREATE INDEX profile_scope ON profile_entries(account, friend, subject, kind);
ALTER TABLE qq_friends ADD COLUMN owner_relationship TEXT NOT NULL DEFAULT '';
ALTER TABLE qq_friends ADD COLUMN miko_relationship TEXT NOT NULL DEFAULT '';
ALTER TABLE qq_friends ADD COLUMN miko_introduced INTEGER NOT NULL DEFAULT 0;
ALTER TABLE qq_friends ADD COLUMN intro_receipt_id INTEGER;
