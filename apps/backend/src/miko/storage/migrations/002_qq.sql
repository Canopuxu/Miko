CREATE TABLE qq_friends (
    account TEXT NOT NULL,
    friend TEXT NOT NULL,
    name TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    enabled INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'owner',
    activated_at REAL NOT NULL DEFAULT 0,
    pending_since REAL,
    pending_last REAL,
    due_at REAL,
    PRIMARY KEY (account, friend)
);
CREATE TABLE qq_runs (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    friend TEXT NOT NULL,
    state TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    receipt_id INTEGER,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX qq_runs_peer ON qq_runs(account, friend, created_at);
CREATE TABLE qq_events (
    account TEXT NOT NULL,
    friend TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    timestamp REAL NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (account, friend, message_id)
);
