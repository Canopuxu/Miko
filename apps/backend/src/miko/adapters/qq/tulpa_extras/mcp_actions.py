"""Tulpa MCPActions receipt storage (MIT); operation preparation stays in Miko."""
import json
import time


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Receipts:
    def __init__(self, access, client_factory):
        self.access, self.client_factory = access, client_factory
        with access.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,request_key TEXT NOT NULL,
                    signature TEXT NOT NULL,kind TEXT NOT NULL,conversation_id TEXT NOT NULL,
                    params TEXT NOT NULL,target TEXT NOT NULL,summary TEXT NOT NULL,
                    state TEXT NOT NULL,created REAL NOT NULL,
                    updated REAL NOT NULL,result TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(grant_id,request_key));
                CREATE TABLE IF NOT EXISTS operation_events(
                    id INTEGER PRIMARY KEY,operation_id TEXT NOT NULL,state TEXT NOT NULL,at REAL NOT NULL);
            ''')

    def recover(self):
        with self.access.connect() as db:
            rows = db.execute("SELECT id FROM operations WHERE state='EXECUTING'").fetchall()
            for row in rows:
                self.transition(db, row['id'], 'UNKNOWN')

    def transition(self, db, oid, state, result=None):
        db.execute('UPDATE operations SET state=?,updated=?,result=? WHERE id=?',
                   (state, time.time(), dump(result or {}), oid))
        db.execute('INSERT INTO operation_events(operation_id,state,at) VALUES(?,?,?)', (oid, state, time.time()))

    def get(self, oid, gid=None):
        with self.access.connect() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (oid,)).fetchone()
            if not row or (gid is not None and row['grant_id'] != gid):
                raise ValueError('操作不存在。')
            result = {k: row[k] for k in ('id','grant_id','kind','conversation_id','summary','state','created','updated')}
            result['result'] = json.loads(row['result'])
            return result
