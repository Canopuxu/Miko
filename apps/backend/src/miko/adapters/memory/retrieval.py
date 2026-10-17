"""Persistent Miko material projection for Tulpa's read-only local retrieval."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from miko.adapters.memory.tulpa_upstream.retrieval import Plan, scope_sql, text_matches
from miko.adapters.memory.tulpa_upstream.store import tokens


class LocalRecall:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, source_id TEXT UNIQUE NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'miko', conversation_id TEXT NOT NULL,
                    content TEXT NOT NULL, timestamp INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(tokens);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def sync(self, documents):
        """Only changed material is reindexed; this database is disposable derived data."""
        with closing(self.connect()) as db, db:
            existing = {r['source_id']: r for r in db.execute('SELECT * FROM messages')}
            for name, old in existing.items():
                if name not in documents:
                    db.execute('DELETE FROM message_fts WHERE rowid=?', (old['id'],))
                    db.execute('DELETE FROM messages WHERE id=?', (old['id'],))
            for name, doc in documents.items():
                payload = json.dumps(doc['row'], ensure_ascii=False, sort_keys=True)
                source = doc['row']['source']
                content = ' '.join(filter(None, [doc['description'], source.get('title'), source.get('keywords')]))
                old = existing.get(name)
                if old and old['payload'] == payload and old['content'] == content:
                    continue
                db.execute('''INSERT INTO messages(source_id,conversation_id,content,timestamp,payload)
                    VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,content=excluded.content,
                    timestamp=excluded.timestamp,payload=excluded.payload''',
                    (name, doc['peer'], content, doc.get('timestamp', 0), payload))
                mid = db.execute('SELECT id FROM messages WHERE source_id=?', (name,)).fetchone()[0]
                if not old or old['content'] != content:
                    db.execute('DELETE FROM message_fts WHERE rowid=?', (mid,))
                    db.execute('INSERT INTO message_fts(rowid,tokens) VALUES(?,?)', (mid, tokens(content)))

    def material(self, peers):
        plan = Plan(platforms=['miko'], conversations=[json.dumps(['miko', peer]) for peer in peers])
        where, params = scope_sql(plan)
        with closing(self.connect()) as db:
            return {r['source_id']: json.loads(r['payload']) for r in db.execute(
                'SELECT m.* FROM messages m WHERE ' + where, params)}

    def search(self, query, allowed, limit=40):
        if not allowed:
            return []
        where = 'm.source_id IN (' + ','.join('?' for _ in allowed) + ')'
        with closing(self.connect()) as db:
            return [row['source_id'] for row in text_matches(db, where, list(allowed), query, limit=limit)]

    def recent(self, allowed, limit=64):
        if not allowed:
            return []
        with closing(self.connect()) as db:
            # Tulpa's per-bucket round-robin; buckets here are Miko material types.
            return [r['source_id'] for r in db.execute('''WITH candidates AS (
                SELECT *,row_number() OVER(PARTITION BY json_extract(payload,'$.group')
                    ORDER BY timestamp DESC,id DESC) AS slot FROM messages WHERE source_id IN (''' +
                ','.join('?' for _ in allowed) + ')) SELECT source_id FROM candidates ORDER BY slot,timestamp DESC,id DESC LIMIT ?', [*allowed, limit])]
