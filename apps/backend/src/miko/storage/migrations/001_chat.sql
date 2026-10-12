CREATE TABLE people (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('owner', 'miko', 'contact')),
    display_name TEXT NOT NULL
);
INSERT INTO people VALUES ('owner', 'owner', '你'), ('miko', 'miko', 'Miko');

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('web', 'qq')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    turn_id TEXT NOT NULL,
    author_id TEXT NOT NULL REFERENCES people(id),
    source TEXT NOT NULL CHECK (source IN ('web', 'qq')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'cancelled', 'error', 'interrupted')),
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX messages_conversation ON messages(conversation_id, sequence);
CREATE INDEX messages_turn ON messages(turn_id);
