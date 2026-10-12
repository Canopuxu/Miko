"""Project durable Miko records into the original Tulpa memory workflow.

The projection is replayable; collection, epochs, batches, evidence validation,
optimistic revisions and history remain implemented by the vendored Tulpa.
"""
import asyncio
import json
import threading

from miko.storage.chat import ChatStore
from miko.adapters.memory.tulpa_upstream.tulpa import Tulpa, MemoryWorker, initialize, collect


class Projection(ChatStore):
    def __init__(self, path, source):
        self.path = path
        self.source = source
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, original_id TEXT NOT NULL, author TEXT NOT NULL,
                platform TEXT NOT NULL, conversation_id TEXT NOT NULL, conversation TEXT NOT NULL,
                conversation_type TEXT NOT NULL, sender_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
                content TEXT NOT NULL, is_self INTEGER NOT NULL, reply_to TEXT, source_id TEXT NOT NULL,
                UNIQUE(original_id,conversation_id));
              CREATE INDEX IF NOT EXISTS msg_timeline ON messages(platform,conversation_id,timestamp,id);
              CREATE TABLE IF NOT EXISTS projection_cursor(id INTEGER PRIMARY KEY, sequence INTEGER NOT NULL);
            ''')
            initialize(db)
            first = db.execute('SELECT 1 FROM projection_cursor').fetchone() is None
        self.sync(initial=first)

    def sync(self, initial=False):
        with self.lock, self.source.connect() as source, self.connect() as db:
            cursor = db.execute('SELECT sequence FROM projection_cursor WHERE id=1').fetchone()
            after = cursor[0] if cursor else 0
            # A running web response may finish after newer records have arrived:
            # always reconcile completed records, rather than dropping it at a cursor.
            rows = source.execute('''SELECT m.*,c.title,p.kind FROM messages m
              JOIN conversations c ON c.id=m.conversation_id JOIN people p ON p.id=m.author_id
              WHERE m.status='completed' ORDER BY m.sequence''').fetchall()
            live = set()
            for r in rows:
                if r['source'] == 'qq':
                    _, account, friend, native = r['id'].split(':')
                    event = source.execute('SELECT * FROM qq_events WHERE account=? AND friend=? AND message_id=?',
                                           (account, friend, native)).fetchone()
                    if not event or event['recalled'] or event['origin'] not in ('friend', 'manual', 'miko'):
                        continue
                    subjects = ('owner', 'miko') if r['kind'] == 'contact' else (r['kind'],)
                else:
                    subjects = ('miko',)
                    native = r['id']
                for subject in subjects:
                    cid = subject + '|' + (r['conversation_id'] if r['source'] == 'qq' else 'web:owner')
                    live.add((r['id'], cid))
                    is_self = int(r['kind'] == subject)
                    name = ('Miko' if subject == 'miko' else '你') + ' · ' + (r['title'] if r['source'] == 'qq' else '你')
                    old = db.execute('SELECT * FROM messages WHERE original_id=? AND conversation_id=?',
                                     (r['id'], cid)).fetchone()
                    if old:
                        if old['conversation'] != name:
                            db.execute('UPDATE messages SET conversation=? WHERE id=?', (name, old['id']))
                        if old['content'] != r['text']:
                            db.execute('UPDATE messages SET content=? WHERE id=?', (r['text'], old['id']))
                        continue
                    inserted = db.execute('''INSERT INTO messages(original_id,author,platform,conversation_id,
                      conversation,conversation_type,sender_id,timestamp,content,is_self,source_id)
                      VALUES(?,?,'qq',?,?,'direct',?,?,?,?,?)''',
                      (r['id'], r['kind'], cid, name, r['author_id'], r['created_at'], r['text'], is_self, native))
                    db.execute("INSERT OR IGNORE INTO tulpa_scopes(platform,conversation_id,kind) VALUES('qq',?,'direct')", (cid,))
                    if not initial and (r['sequence'] > after or r['created_at'] >= db.execute('SELECT since FROM tulpa_control').fetchone()[0]):
                        collect(db, inserted.lastrowid)
            for r in db.execute('SELECT id,original_id,conversation_id FROM messages').fetchall():
                if (r['original_id'], r['conversation_id']) not in live:
                    db.execute('DELETE FROM messages WHERE id=?', (r['id'],))
            latest = source.execute('SELECT coalesce(max(sequence),0) FROM messages').fetchone()[0]
            db.execute('INSERT OR REPLACE INTO projection_cursor VALUES(1,?)', (latest,))


class SocialMemory(Tulpa):
    def __init__(self, path, chat):
        from miko.adapters.memory.tulpa_upstream.store import configure_cache
        configure_cache(chat.settings.local_root / 'cache/jieba')
        super().__init__(Projection(path, chat.store))
        self.chat = chat
        self.configured = chat.provider is not None
        self.loop = None
        self.worker = MemoryWorker(self)

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.worker.start()

    def consolidate(self, sid, provider=None):
        detail = self.detail(sid)
        subject, cid = detail['conversation_id'].split('|', 1)

        def summarize(payload):
            payload = {**payload, 'subject': subject, 'conversation': cid}
            for group in ('stage', 'context'):
                for r in payload[group]:
                    r['author'] = subject if r.pop('self') else ('owner' if cid.startswith('web:') or not cid.startswith('qq:') else 'friend')
            future = asyncio.run_coroutine_threadsafe(self.chat.summarize_memory(payload), self.loop)
            return future.result(timeout=150)

        return super().consolidate(sid, provider or summarize)

    def sources(self, sid, ids=None):
        detail = self.detail(sid)
        selected = ids if ids is not None else detail['sources']
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('''SELECT id,original_id,author,content,timestamp FROM messages
              WHERE conversation_id=? ORDER BY timestamp,id''', (detail['conversation_id'],)) if r['id'] in selected]

    def context(self, query, key=None):
        cid = f'qq:{key[0]}:{key[1]}' if key else 'web:owner'
        listing = self.listing()['scopes']
        result = []
        for row in listing:
            subject, peer = row['conversation_id'].split('|', 1)
            if peer != cid:
                continue
            found = self.retrieve({'platform': 'qq', 'conversation_id': row['conversation_id'],
                                   'content': query, 'sender_id': f'qq:{key[1]}' if key else 'owner', 'id': -1})
            if found['memory'] or found['manual'] or found['episodes']:
                result.append({'subject': subject, 'peer': peer, 'automatic': found['memory'],
                               'manual_corrections': found['manual'], 'sources': self.sources(row['id']),
                               'episodes': [self.sources(row['id'], e['message_ids']) for e in found['episodes']]})
        return result
