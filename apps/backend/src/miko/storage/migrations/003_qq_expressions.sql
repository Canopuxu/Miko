ALTER TABLE qq_events ADD COLUMN payload TEXT NOT NULL DEFAULT '{}';
ALTER TABLE qq_events ADD COLUMN recalled INTEGER NOT NULL DEFAULT 0;
CREATE TABLE qq_bubbles (
    run_id TEXT NOT NULL REFERENCES qq_runs(id),
    position INTEGER NOT NULL,
    expression TEXT NOT NULL,
    text TEXT NOT NULL,
    segments TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'pending',
    receipt_id INTEGER,
    timestamp REAL,
    error TEXT,
    PRIMARY KEY (run_id, position)
);
CREATE TABLE qq_stickers (
    account TEXT NOT NULL,
    digest TEXT NOT NULL,
    description TEXT NOT NULL,
    info TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (account, digest)
);
