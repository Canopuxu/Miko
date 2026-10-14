"""Miko data contracts over Tulpa's transactional source and history store."""
import json
import time
import uuid
from datetime import datetime

from miko.storage.search import terms, fts_query


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class StructuredMemory:
    def __init__(self, projection):
        self.projection = projection
        with projection.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS memory_items (
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, peer TEXT NOT NULL,
                category TEXT NOT NULL, topic TEXT NOT NULL, automatic TEXT NOT NULL,
                manual TEXT NOT NULL DEFAULT '', scope_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, valid INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL,
                UNIQUE(subject,peer,category,topic));
              CREATE TABLE IF NOT EXISTS memory_days (
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, peer TEXT NOT NULL,
                day TEXT NOT NULL, text TEXT NOT NULL, scope_id INTEGER NOT NULL,
                valid INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS memory_sources (
                item_id TEXT NOT NULL, message_id INTEGER NOT NULL,
                PRIMARY KEY(item_id,message_id));
              CREATE TABLE IF NOT EXISTS memory_event_people (
                event_id TEXT NOT NULL, subject TEXT NOT NULL,
                PRIMARY KEY(event_id,subject));
              CREATE TABLE IF NOT EXISTS memory_audit (
                id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, kind TEXT NOT NULL,
                snapshot TEXT NOT NULL, created_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS memory_review_hidden (message_id TEXT PRIMARY KEY, deleted_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS memory_annotations (
                id TEXT PRIMARY KEY, message_id TEXT NOT NULL, peer TEXT NOT NULL,
                note TEXT NOT NULL, scope TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                revision INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL,
                UNIQUE(message_id));
              CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item_id UNINDEXED,tokens);
            ''')
            for operation in ('DELETE', 'UPDATE'):
                condition = '' if operation == 'DELETE' else 'WHEN OLD.content IS NOT NEW.content OR OLD.timestamp IS NOT NEW.timestamp OR OLD.sender_id IS NOT NEW.sender_id'
                db.executescript(f'''CREATE TRIGGER IF NOT EXISTS structured_source_{operation.lower()}
                  BEFORE {operation} ON messages {condition} BEGIN
                    UPDATE memory_items SET valid=0 WHERE id IN
                      (SELECT item_id FROM memory_sources WHERE message_id=OLD.id);
                    UPDATE memory_days SET valid=0 WHERE id IN
                      (SELECT item_id FROM memory_sources WHERE message_id=OLD.id);
                  END;''')

    def _sources(self, db, item_id):
        return [dict(r) for r in db.execute('''SELECT m.id,m.original_id,m.author,m.sender_id,
          m.content,m.timestamp FROM memory_sources s JOIN messages m ON m.id=s.message_id
          WHERE s.item_id=? ORDER BY m.timestamp,m.id''', (item_id,))]

    def _index(self, db, item_id, text):
        db.execute('DELETE FROM memory_fts WHERE item_id=?', (item_id,))
        db.execute('INSERT INTO memory_fts VALUES(?,?)', (item_id, ' '.join(sorted(terms(text)))))

    def _audit(self, db, row, kind):
        value = dict(row)
        value['sources'] = [r['id'] for r in self._sources(db, value['id'])]
        db.execute('INSERT INTO memory_audit(item_id,kind,snapshot,created_at) VALUES(?,?,?,?)',
                   (value['id'], kind, encode(value), time.time_ns() // 1_000_000))

    def items(self, query='', subject='', peer=None, category='', active=False):
        clauses, args = [], []
        for column, value in (('subject', subject), ('category', category)):
            if value:
                clauses.append(f'i.{column}=?'); args.append(value)
        if peer is not None:
            clauses.append("i.peer IN ('',?)"); args.append(peer)
        if active:
            clauses.append("i.enabled=1 AND (i.valid=1 OR i.manual!='')")
        match = fts_query(query)
        join = ''
        if query.strip():
            if not match:
                return []
            join = ' JOIN memory_fts f ON f.item_id=i.id'
            clauses.append('memory_fts MATCH ?'); args.append(match)
        with self.projection.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT i.* FROM memory_items i' + join +
                (' WHERE ' + ' AND '.join(clauses) if clauses else '') +
                ' ORDER BY i.updated_at DESC,i.id LIMIT 300', args)]
        for r in rows:
            r['content'] = r['manual'] or r['automatic']
        return rows

    def detail(self, item_id):
        with self.projection.connect() as db:
            row = db.execute('SELECT * FROM memory_items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise ValueError('找不到这条记忆')
            value = dict(row)
            value['sources'] = self._sources(db, item_id)
            value['history'] = [dict(r) for r in db.execute(
                'SELECT * FROM memory_audit WHERE item_id=? ORDER BY id DESC LIMIT 30', (item_id,))]
        value['content'] = value['manual'] or value['automatic']
        return value

    def organize(self, subject, peer, category, proposal, revisions):
        if not proposal or any(not r.get('sub_topic') or not r.get('memo') for r in proposal):
            raise ValueError('分类整理结果为空或不完整')
        if len({r['sub_topic'] for r in proposal})!=len(proposal):
            raise ValueError('分类整理结果包含重复子主题')
        with self.projection.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute('SELECT * FROM memory_items WHERE subject=? AND peer=? AND category=? AND enabled=1',(subject,peer,category)).fetchall()
            if {r['id']:r['revision'] for r in rows}!=revisions:
                raise ValueError('分类资料已经变化，请重新生成整理方案')
            if not rows:
                raise ValueError('这个分类尚无条目')
            sources=set(mid['id'] for r in rows for mid in self._sources(db,r['id']))
            for r in rows:
                self._audit(db,r,'before_organize')
                db.execute('UPDATE memory_items SET enabled=0,revision=revision+1 WHERE id=?',(r['id'],))
            plans=[]
            for r in proposal:
                old=db.execute('SELECT * FROM memory_items WHERE subject=? AND peer=? AND category=? AND topic=?',
                               (subject,peer,category,r['sub_topic'])).fetchone()
                plans.append({'subject':subject,'peer':peer,'category':category,'topic':r['sub_topic'],
                    'content':r['memo'],'expected_revision':old['revision'] if old else 0,'sources':list(sources)})
            self.commit(db,rows[0]['scope_id'],{'structured':plans})
            for p in plans:
                db.execute('UPDATE memory_items SET enabled=1,manual=? WHERE subject=? AND peer=? AND category=? AND topic=?',
                           (p['content'],subject,peer,category,p['topic']))
                row=db.execute('SELECT * FROM memory_items WHERE subject=? AND peer=? AND category=? AND topic=?',
                               (subject,peer,category,p['topic'])).fetchone()
                self._index(db,row['id'],f'{category} {p["topic"]} {p["content"]}')
                self._audit(db,row,'organize')
        return self.items(subject=subject,category=category)

    def commit(self, db, sid, result):
        """Invoked inside Tulpa's checked, optimistic batch commit transaction."""
        stamp = time.time_ns() // 1_000_000
        for item in result.get('structured', []):
            key = tuple(item[k] for k in ('subject', 'peer', 'category', 'topic'))
            old = db.execute('SELECT * FROM memory_items WHERE subject=? AND peer=? AND category=? AND topic=?', key).fetchone()
            if (old['revision'] if old else 0) != item['expected_revision']:
                raise ValueError('人物资料已更新，本批稍后重新整理')
            if old and old['valid']:
                previous_time=db.execute('SELECT max(m.timestamp) FROM memory_sources s JOIN messages m ON m.id=s.message_id WHERE s.item_id=?',(old['id'],)).fetchone()[0] or 0
                current_time=max((db.execute('SELECT timestamp FROM messages WHERE id=?',(mid,)).fetchone()[0] for mid in item['sources']),default=0)
                if current_time < previous_time:
                    continue
            if old and old['valid'] and old['automatic'] == item['content']:
                continue
            item_id = old['id'] if old else uuid.uuid4().hex
            if old:
                self._audit(db, old, 'before_automatic')
                if not old['valid']:
                    db.execute('DELETE FROM memory_sources WHERE item_id=?',(item_id,))
                db.execute('UPDATE memory_items SET automatic=?,valid=1,revision=revision+1,updated_at=?,scope_id=? WHERE id=?',
                           (item['content'], stamp, sid, item_id))
            else:
                db.execute('''INSERT INTO memory_items(id,subject,peer,category,topic,automatic,scope_id,updated_at)
                  VALUES(?,?,?,?,?,?,?,?)''', (item_id, *key, item['content'], sid, stamp))
            db.executemany('INSERT OR IGNORE INTO memory_sources VALUES(?,?)',
                           [(item_id, mid) for mid in item['sources']])
            fresh = db.execute('SELECT * FROM memory_items WHERE id=?', (item_id,)).fetchone()
            self._index(db, item_id, ' '.join(key) + ' ' + (fresh['manual'] or fresh['automatic']))
            self._audit(db, fresh, 'automatic')
        for event in result.get('days', []):
            event_id = f'day:{sid}:{event["subject"]}:{event["through"]}'
            db.execute('INSERT OR REPLACE INTO memory_days VALUES(?,?,?,?,?,?,1,?)',
                       (event_id, event['subject'], event['peer'], event['day'], event['text'], sid, stamp))
            db.executemany('INSERT OR IGNORE INTO memory_sources VALUES(?,?)',
                           [(event_id, mid) for mid in event['sources']])
            self._index(db, event_id, event['text'])
            db.execute('DELETE FROM memory_event_people WHERE event_id=?', (event_id,))
            db.executemany('INSERT INTO memory_event_people VALUES(?,?)',
                           [(event_id, person) for person in event.get('subjects', [event['subject']])])

    def edit(self, item_id, text, revision, enabled=True):
        if len(text) > 6000:
            raise ValueError('单条人工修正最多 6000 字')
        with self.projection.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM memory_items WHERE id=?', (item_id,)).fetchone()
            if not row or row['revision'] != revision:
                raise ValueError('记忆已更新，请刷新后保存')
            self._audit(db, row, 'before_manual')
            db.execute('UPDATE memory_items SET manual=?,enabled=?,revision=revision+1,updated_at=? WHERE id=?',
                       (text.strip(), int(enabled), time.time_ns() // 1_000_000, item_id))
            db.execute('UPDATE tulpa_scopes SET revision=revision+1 WHERE id=?', (row['scope_id'],))
            fresh = db.execute('SELECT * FROM memory_items WHERE id=?', (item_id,)).fetchone()
            self._index(db, item_id, f'{row["category"]} {row["topic"]} {text.strip() or row["automatic"]}')
            self._audit(db, fresh, 'manual')
        return self.detail(item_id)

    def restore(self, item_id, history_id, revision):
        with self.projection.connect() as db:
            history = db.execute('SELECT snapshot FROM memory_audit WHERE id=? AND item_id=?', (history_id, item_id)).fetchone()
            if not history:
                raise ValueError('找不到这份历史版本')
            snapshot = json.loads(history[0])
        # Restoration is an explicit user correction, so future automatic merges
        # retain the restored content until the user clears the correction.
        return self.edit(item_id, snapshot['manual'] or snapshot['automatic'], revision, bool(snapshot['enabled']))

    def days(self, day='', subject='', peer=None, query=''):
        clauses, args = ['d.valid=1'], []
        for column, value in (('day', day),):
            if value:
                clauses.append(f'd.{column}=?'); args.append(value)
        if subject:
            clauses.append('(d.subject=? OR EXISTS (SELECT 1 FROM memory_event_people p WHERE p.event_id=d.id AND p.subject=?))')
            args.extend([subject, subject])
        if peer is not None:
            clauses.append("d.peer IN ('',?)"); args.append(peer)
        join = ''
        if query.strip():
            match = fts_query(query)
            if not match:
                return []
            join = ' JOIN memory_fts ON memory_fts.item_id=d.id'
            clauses.append('memory_fts MATCH ?'); args.append(match)
        with self.projection.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT d.* FROM memory_days d' + join +
                ' WHERE ' + ' AND '.join(clauses) + ' ORDER BY d.day DESC,d.updated_at DESC LIMIT 100', args)]
            for r in rows:
                r['sources'] = self._sources(db, r['id'])
                r['subjects'] = [v[0] for v in db.execute(
                    'SELECT subject FROM memory_event_people WHERE event_id=? ORDER BY subject', (r['id'],))] or [r['subject']]
        return rows

    def review(self, day=''):
        day = day or datetime.now().astimezone().date().isoformat()
        start = int(datetime.fromisoformat(day).astimezone().timestamp() * 1000)
        with self.projection.source.connect() as db:
            rows = [dict(r) for r in db.execute('''SELECT m.id,m.text,m.created_at,m.conversation_id,c.title,c.source
              FROM messages m JOIN conversations c ON c.id=m.conversation_id
              WHERE m.author_id='miko' AND m.status='completed' AND m.created_at>=? AND m.created_at<?
              ORDER BY m.created_at DESC''', (start, start + 86400000))]
            for row in rows:
                row['context'] = [dict(r) for r in db.execute('''SELECT m.text,p.display_name AS author
                    FROM messages m JOIN people p ON p.id=m.author_id
                    WHERE m.conversation_id=? AND m.author_id!='miko' AND m.sequence <
                      (SELECT sequence FROM messages WHERE id=?)
                    ORDER BY m.sequence DESC LIMIT 2''', (row['conversation_id'], row['id']))][::-1]
        with self.projection.connect() as db:
            hidden = {r[0] for r in db.execute('SELECT message_id FROM memory_review_hidden')}
            rows = [r for r in rows if r['id'] not in hidden]
            notes = {r['message_id']: dict(r) for r in db.execute('SELECT * FROM memory_annotations')}
            for note in notes.values():
                note['history']=[dict(r) for r in db.execute("SELECT snapshot,created_at FROM memory_audit WHERE item_id=? AND kind='annotation' ORDER BY id DESC LIMIT 20",(note['id'],))]
        for r in rows:
            r['annotation'] = notes.get(r['id'])
        return {'day': day, 'replies': rows}

    def delete_review(self, message_id):
        with self.projection.source.connect() as db:
            if not db.execute("SELECT 1 FROM messages WHERE id=? AND author_id='miko' AND status='completed'", (message_id,)).fetchone():
                raise ValueError('找不到这条回复')
        with self.projection.connect() as db:
            db.execute('INSERT OR REPLACE INTO memory_review_hidden VALUES(?,?)', (message_id, time.time_ns() // 1_000_000))
            db.execute('DELETE FROM memory_audit WHERE item_id IN (SELECT id FROM memory_annotations WHERE message_id=?)', (message_id,))
            db.execute('DELETE FROM memory_annotations WHERE message_id=?', (message_id,))
        return {'deleted': True}

    def annotate(self, message_id, note, scope, revision, enabled=True):
        if scope not in ('example', 'friend', 'global') or not note.strip() or len(note) > 2000:
            raise ValueError('请填写批注并选择适用范围')
        with self.projection.source.connect() as source:
            message = source.execute("SELECT * FROM messages WHERE id=? AND author_id='miko' AND status='completed'", (message_id,)).fetchone()
        if not message:
            raise ValueError('只能批注已完成的 Miko 回复')
        peer = message['conversation_id']
        with self.projection.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM memory_review_hidden WHERE message_id=?', (message_id,)).fetchone():
                raise ValueError('该回顾已删除')
            old = db.execute('SELECT * FROM memory_annotations WHERE message_id=?', (message_id,)).fetchone()
            if (old['revision'] if old else 0) != revision:
                raise ValueError('批注已变化，请刷新')
            item_id = old['id'] if old else uuid.uuid4().hex
            if old:
                self._audit(db, old, 'before_annotation')
            db.execute('''INSERT INTO memory_annotations VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(message_id) DO UPDATE SET note=excluded.note,scope=excluded.scope,
              enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at''',
              (item_id, message_id, peer, note.strip(), scope, int(enabled), revision+1, time.time_ns()//1_000_000))
            result = dict(db.execute('SELECT * FROM memory_annotations WHERE message_id=?', (message_id,)).fetchone())
            self._audit(db, result, 'annotation')
        return result

    def feedback_candidates(self, peer):
        with self.projection.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM memory_annotations WHERE enabled=1 AND (scope IN ('global','example') OR peer=?) ORDER BY updated_at DESC", (peer,))]
        result = []
        with self.projection.source.connect() as source:
            for r in rows:
                msg = source.execute('SELECT * FROM messages WHERE id=? AND status=?', (r['message_id'], 'completed')).fetchone()
                if not msg:
                    continue
                preceding = source.execute("SELECT text FROM messages WHERE conversation_id=? AND sequence<? ORDER BY sequence DESC LIMIT 2", (msg['conversation_id'], msg['sequence'])).fetchall()
                result.append({'reply': msg['text'], 'feedback': r['note'], 'scope': r['scope'],
                    'context': '\n'.join(v['text'] for v in reversed(preceding))})
        return result

    def feedback(self, query, peer):
        # Without semantic retrieval, only explicitly scoped feedback is injected.
        return [r for r in self.feedback_candidates(peer) if r['scope'] != 'example'][:8]
