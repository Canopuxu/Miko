import json
import time
import uuid

from miko.storage.chat import ChatStore
from miko.adapters.qq.messages import model_message


class QqStore:
    def __init__(self, chat: ChatStore):
        self.chat = chat

    def recover(self):
        with self.chat.connect() as db:
            db.execute("UPDATE qq_bubbles SET state='unknown' WHERE state='sending'")
            db.execute("UPDATE qq_bubbles SET state='cancelled' WHERE state IN ('pending','preparing')")
            db.execute("""UPDATE qq_friends SET blocked=1, note='发送时后台中断，请到 QQ 核对；不会重发'
                WHERE EXISTS (SELECT 1 FROM qq_runs r WHERE r.account=qq_friends.account
                AND r.friend=qq_friends.friend AND r.state='sending'
                AND (EXISTS (SELECT 1 FROM qq_bubbles b WHERE b.run_id=r.id AND b.state='unknown')
                     OR NOT EXISTS (SELECT 1 FROM qq_bubbles b WHERE b.run_id=r.id)))""")
            db.execute("""UPDATE qq_runs SET state='unknown' WHERE state='sending'
                AND (EXISTS (SELECT 1 FROM qq_bubbles b WHERE b.run_id=qq_runs.id AND b.state='unknown')
                     OR NOT EXISTS (SELECT 1 FROM qq_bubbles b WHERE b.run_id=qq_runs.id))""")
            db.execute("UPDATE qq_runs SET state='interrupted' WHERE state IN ('generating','sending')")
            db.execute("UPDATE qq_events SET origin='unknown' WHERE origin='pending'")
            db.execute("UPDATE qq_friends SET mode='owner', pending_since=NULL, pending_last=NULL, due_at=NULL")
            confirmed = [dict(r) for r in db.execute("""SELECT r.* FROM qq_runs r WHERE r.state='sent'
                AND NOT EXISTS (SELECT 1 FROM qq_bubbles b WHERE b.run_id=r.id)
                AND NOT EXISTS (SELECT 1 FROM messages m
                    WHERE m.id='qq:' || r.account || ':' || r.friend || ':' || r.receipt_id)""")]
            pieces = [dict(r) for r in db.execute("""SELECT r.account,r.friend,b.* FROM qq_bubbles b
                JOIN qq_runs r ON r.id=b.run_id WHERE b.state='sent' AND b.receipt_id IS NOT NULL ORDER BY r.created_at,b.position""")]
        # A crash may occur after the receipt is saved but before its message is saved.
        for run in confirmed:
            key = (run['account'], run['friend'])
            self.event(key, run['receipt_id'], run['text'], run['updated_at'], 'miko')
            self.origin(key, run['receipt_id'], 'miko')
            self.record(key, run['receipt_id'], 'miko')
        for piece in pieces:
            key = (piece['account'], piece['friend'])
            self.confirm_event(key, piece['receipt_id'], piece['text'], piece['timestamp'],
                               {'segments': json.loads(piece['segments'])})
            self.record(key, piece['receipt_id'], 'miko')

    def add_friend(self, account, friend, name, chat_type=1):
        cid = f'qq:{account}:{friend}'
        stamp = time.time_ns() // 1_000_000
        with self.chat.connect() as db:
            if chat_type == 1:
                db.execute("INSERT OR IGNORE INTO people VALUES (?, 'contact', ?)", (f'qq:{friend}', name))
            db.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?, 'qq', ?, ?)", (cid, name, stamp, stamp))
            db.execute("INSERT OR IGNORE INTO qq_friends(account, friend, name, conversation_id,chat_type) VALUES (?, ?, ?, ?,?)",
                       (account, friend, name, cid, chat_type))

    def member(self, uin, name):
        with self.chat.connect() as db:
            db.execute("INSERT OR IGNORE INTO people VALUES (?, 'contact', ?)", (f'qq:{uin}', name or uin))

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

    def event(self, key, mid, text, stamp, origin, payload=None):
        with self.chat.connect() as db:
            return bool(db.execute('''INSERT OR IGNORE INTO qq_events
                (account,friend,message_id,text,timestamp,origin,payload) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (*key, mid, text, stamp, origin, json.dumps(payload or {}, ensure_ascii=False))).rowcount)

    def get_event(self, key, mid):
        with self.chat.connect() as db:
            row = db.execute('SELECT * FROM qq_events WHERE account=? AND friend=? AND message_id=?', (*key, mid)).fetchone()
            return dict(row) if row else None

    def confirm_event(self, key, mid, text, stamp, payload):
        with self.chat.connect() as db:
            db.execute('''INSERT INTO qq_events(account,friend,message_id,text,timestamp,origin,payload)
                VALUES(?,?,?,?,?,'miko',?) ON CONFLICT(account,friend,message_id) DO UPDATE SET
                origin='miko',text=excluded.text,payload=excluded.payload''',
                (*key, mid, text, stamp, json.dumps(payload, ensure_ascii=False)))

    def recall(self, key, mid):
        with self.chat.connect() as db:
            db.execute('UPDATE qq_events SET recalled=1 WHERE account=? AND friend=? AND message_id=?', (*key, mid))

    def quote_snapshot(self, key, mid, quote):
        with self.chat.connect() as db:
            row = db.execute('SELECT payload FROM qq_events WHERE account=? AND friend=? AND message_id=?', (*key, mid)).fetchone()
            if row:
                payload = json.loads(row['payload'])
                payload['quoted_message'] = quote
                db.execute('UPDATE qq_events SET payload=? WHERE account=? AND friend=? AND message_id=?',
                           (json.dumps(payload, ensure_ascii=False), *key, mid))

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
            return db.execute("""SELECT 1 FROM qq_runs r WHERE account=? AND friend=? AND
                ((receipt_id=? AND state='sent') OR EXISTS
                    (SELECT 1 FROM qq_bubbles b WHERE b.run_id=r.id AND b.receipt_id=? AND b.state='sent'))""",
                (*key, mid, mid)).fetchone() is not None

    def plan(self, rid, bubbles, texts):
        with self.chat.connect() as db:
            db.executemany('INSERT INTO qq_bubbles(run_id,position,expression,text) VALUES (?,?,?,?)',
                [(rid, i, json.dumps(b, ensure_ascii=False), texts[i]) for i, b in enumerate(bubbles)])
            db.execute('UPDATE qq_runs SET text=? WHERE id=?', ('\n'.join(texts), rid))

    def update_bubble(self, rid, position, **fields):
        with self.chat.connect() as db:
            db.execute('UPDATE qq_bubbles SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE run_id=? AND position=?',
                       (*fields.values(), rid, position))
            if fields.get('state') == 'sent':
                row = db.execute('''SELECT r.account,r.friend,b.expression,b.receipt_id FROM qq_bubbles b
                    JOIN qq_runs r ON r.id=b.run_id WHERE b.run_id=? AND b.position=?''', (rid, position)).fetchone()
                if row and json.loads(row['expression']).get('introduces_miko'):
                    db.execute('''UPDATE qq_friends SET miko_introduced=1,intro_receipt_id=?
                        WHERE account=? AND friend=? AND miko_introduced=0''',
                        (row['receipt_id'], row['account'], row['friend']))

    def cancel_tail(self, rid):
        with self.chat.connect() as db:
            db.execute("UPDATE qq_bubbles SET state='cancelled' WHERE run_id=? AND state IN ('pending','preparing')", (rid,))

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
            runs = [dict(r) for r in db.execute(f'SELECT * FROM qq_runs {where} ORDER BY created_at DESC LIMIT 30', args)]
            for run in runs:
                run['bubbles'] = [dict(r) for r in db.execute('SELECT position,text,state,receipt_id,error FROM qq_bubbles WHERE run_id=? ORDER BY position', (run['id'],))]
            return runs

    def messages(self, key):
        rows = self.chat.messages(self.friend(key)['conversation_id'])
        for row in rows:
            event = self.get_event(key, int(row['id'].rsplit(':', 1)[-1]))
            payload = json.loads(event['payload']) if event else {}
            if event:
                row.update(message_id=event['message_id'], segments=payload.get('segments', []),
                           quoted_message=payload.get('quoted_message'), recalled=bool(event['recalled']))
                if payload.get('sender_name') and row['author']['kind'] == 'contact':
                    row['author']['name'] = payload['sender_name']
                row['directed'] = payload.get('directed', False)
                row['sender_uid'] = payload.get('sender_uid', '')
        return rows

    def context(self, key):
        # Recent messages include confirmed owner / Miko sends, independent of QQ account identity.
        friend = self.friend(key)
        # A fresh introduction must not inherit old test exchanges claiming they already met.
        since = friend['activated_at'] * 1000 if not friend['miko_introduced'] or friend['chat_type'] == 2 else 0
        rows = [r for r in self.messages(key) if not r['recalled'] and r['created_at'] >= since][-40:]
        return [{'author': r['author'], 'text': r['text'], 'created_at': r['created_at'],
                 'message_id': r['message_id'], 'segments': model_message(r),
                 'directed': r.get('directed', False), 'sender_uid': r.get('sender_uid', ''),
                 **({'quoted_message': r['quoted_message']} if r['quoted_message'] else {})} for r in rows]
