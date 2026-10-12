import time
import uuid

from miko.storage.chat import ChatStore


class QqStore:
    def __init__(self, chat: ChatStore):
        self.chat = chat

    def recover(self):
        with self.chat.connect() as db:
            db.execute("""UPDATE qq_friends SET blocked=1, note='发送时后台中断，请到 QQ 核对；不会重发'
                WHERE EXISTS (SELECT 1 FROM qq_runs r WHERE r.account=qq_friends.account
                AND r.friend=qq_friends.friend AND r.state='sending')""")
            db.execute("UPDATE qq_runs SET state='unknown' WHERE state='sending'")
            db.execute("UPDATE qq_runs SET state='interrupted' WHERE state='generating'")
            db.execute("UPDATE qq_events SET origin='unknown' WHERE origin='pending'")
            db.execute("UPDATE qq_friends SET mode='owner', pending_since=NULL, pending_last=NULL, due_at=NULL")
            confirmed = [dict(r) for r in db.execute("""SELECT r.* FROM qq_runs r WHERE r.state='sent'
                AND NOT EXISTS (SELECT 1 FROM messages m
                    WHERE m.id='qq:' || r.account || ':' || r.friend || ':' || r.receipt_id)""")]
        # A crash may occur after the receipt is saved but before its message is saved.
        for run in confirmed:
            key = (run['account'], run['friend'])
            self.event(key, run['receipt_id'], run['text'], run['updated_at'], 'miko')
            self.origin(key, run['receipt_id'], 'miko')
            self.record(key, run['receipt_id'], 'miko')

    def add_friend(self, account, friend, name):
        cid = f'qq:{account}:{friend}'
        stamp = time.time_ns() // 1_000_000
        with self.chat.connect() as db:
            db.execute("INSERT OR IGNORE INTO people VALUES (?, 'contact', ?)", (f'qq:{friend}', name))
            db.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?, 'qq', ?, ?)", (cid, name, stamp, stamp))
            db.execute("INSERT OR IGNORE INTO qq_friends(account, friend, name, conversation_id) VALUES (?, ?, ?, ?)",
                       (account, friend, name, cid))

    def friends(self):
        with self.chat.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM qq_friends ORDER BY rowid')]

    def friend(self, key):
        with self.chat.connect() as db:
            row = db.execute('SELECT * FROM qq_friends WHERE account=? AND friend=?', key).fetchone()
            return dict(row) if row else None

    def update_friend(self, key, **fields):
        with self.chat.connect() as db:
            db.execute('UPDATE qq_friends SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE account=? AND friend=?',
                       (*fields.values(), *key))

    def event(self, key, mid, text, stamp, origin):
        with self.chat.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO qq_events VALUES (?, ?, ?, ?, ?, ?)',
                                   (*key, mid, text, stamp, origin)).rowcount)

    def record(self, key, mid, author):
        row = self.friend(key)
        with self.chat.connect() as db:
            event = db.execute('SELECT * FROM qq_events WHERE account=? AND friend=? AND message_id=?', (*key, mid)).fetchone()
            if not event:
                return
            stamp = int(event['timestamp'] * 1000)
            message_id = f'qq:{key[0]}:{key[1]}:{mid}'
            db.execute("""INSERT OR IGNORE INTO messages
                (id, conversation_id, turn_id, author_id, source, text, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'qq', ?, 'completed', ?, ?)""",
                (message_id, row['conversation_id'], message_id, author, event['text'], stamp, stamp))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (stamp, row['conversation_id']))

    def echoes(self, key):
        with self.chat.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM qq_events WHERE account=? AND friend=? AND origin='pending'", key)]

    def origin(self, key, mid, origin):
        with self.chat.connect() as db:
            db.execute('UPDATE qq_events SET origin=? WHERE account=? AND friend=? AND message_id=?', (origin, *key, mid))

    def receipt(self, key, mid):
        with self.chat.connect() as db:
            return db.execute("SELECT 1 FROM qq_runs WHERE account=? AND friend=? AND receipt_id=? AND state='sent'",
                              (*key, mid)).fetchone() is not None

    def new_run(self, key, now):
        rid = uuid.uuid4().hex
        with self.chat.connect() as db:
            db.execute("INSERT INTO qq_runs(id,account,friend,state,created_at,updated_at) VALUES (?, ?, ?, 'generating', ?, ?)",
                       (rid, *key, now, now))
        return rid

    def update_run(self, rid, **fields):
        fields['updated_at'] = time.time()
        with self.chat.connect() as db:
            db.execute('UPDATE qq_runs SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), rid))

    def runs(self, key=None):
        with self.chat.connect() as db:
            where, args = ('WHERE account=? AND friend=?', key) if key else ('', ())
            return [dict(r) for r in db.execute(f'SELECT * FROM qq_runs {where} ORDER BY created_at DESC LIMIT 30', args)]

    def context(self, key):
        friend = self.friend(key)
        # Recent messages include confirmed owner / Miko sends, independent of QQ account identity.
        rows = self.chat.messages(friend['conversation_id'])[-40:]
        return [{'author': r['author'], 'text': r['text'], 'created_at': r['created_at']} for r in rows]
