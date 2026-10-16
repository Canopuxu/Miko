"""Owner-reviewed setting entries and scoped relationship context.

Uses Tulpa's bounded Chinese FTS matching, not owner speech imitation.
"""
from datetime import datetime
import json
import uuid

from miko.storage.profile_categories import ProfileCategories, paths
from miko.storage.chat import now_ms
from miko.storage.search import fts_query, terms


class ProfileStore:
    def __init__(self, chat):
        self.chat = chat

    def get(self, entry_id):
        with self.chat.connect() as db:
            row = db.execute('SELECT * FROM profile_entries WHERE id=?', (entry_id,)).fetchone()
            return dict(row) if row else None

    def save(self, value, entry_id=None):
        if value['kind'] in ('overview', 'fact'):
            value = {**value, 'enabled': True}
        if entry_id and not self.get(entry_id):
            raise ValueError('找不到这条资料')
        if value['account'] or value['friend']:
            with self.chat.connect() as db:
                if not db.execute('SELECT 1 FROM qq_friends WHERE account=? AND friend=?',
                                  (value['account'], value['friend'])).fetchone():
                    raise ValueError('找不到这位好友')
        entry_id = entry_id or uuid.uuid4().hex
        stamp = now_ms()
        with self.chat.connect() as db:
            self._write(db, value, entry_id, stamp)
        return self.get(entry_id)

    def _write(self, db, value, entry_id, stamp):
        fields = ('subject', 'kind', 'category', 'title', 'content', 'keywords', 'account', 'friend', 'as_of', 'enabled')
        values = [value[name] for name in fields]
        db.execute(f"""INSERT INTO profile_entries(id,{','.join(fields)},created_at,updated_at)
                VALUES ({','.join('?' for _ in range(len(fields)+3))}) ON CONFLICT(id) DO UPDATE SET
                {','.join(name+'=excluded.'+name for name in fields)},updated_at=excluded.updated_at""",
                (entry_id, *values, stamp, stamp))
        db.execute('DELETE FROM profile_fts WHERE entry_id=?', (entry_id,))
        indexed = ' '.join(sorted(terms(' '.join(value[name] for name in ('title', 'category', 'keywords', 'content')), limit=None)))
        db.execute('INSERT INTO profile_fts(entry_id,tokens) VALUES (?,?)', (entry_id, indexed))

    def portable_entries(self, subject):
        with self.chat.connect() as db:
            return [dict(r) for r in db.execute("""SELECT kind,category,title,content,keywords FROM profile_entries
                WHERE subject=? AND kind IN ('fact','overview') AND account='' AND friend=''
                ORDER BY created_at,rowid""", (subject,))]

    def replace_portable(self, subject, name, categories, entries):
        stamp = now_ms()
        with self.chat.connect() as db:
            db.execute("""DELETE FROM profile_fts WHERE entry_id IN (SELECT id FROM profile_entries
                WHERE subject=? AND kind IN ('fact','overview') AND account='' AND friend='')""", (subject,))
            db.execute("""DELETE FROM profile_entries WHERE subject=? AND kind IN ('fact','overview')
                AND account='' AND friend=''""", (subject,))
            for entry in entries:
                self._write(db, dict(entry, subject=subject, account='', friend='', as_of=None, enabled=True),
                            uuid.uuid4().hex, stamp)
            db.execute('UPDATE people SET display_name=? WHERE id=?', (name, subject))
            db.execute('''INSERT INTO profile_library(subject,categories) VALUES(?,?)
                ON CONFLICT(subject) DO UPDATE SET categories=excluded.categories''',
                       (subject, json.dumps(categories, ensure_ascii=False)))

    def delete(self, entry_id):
        with self.chat.connect() as db:
            if not db.execute('DELETE FROM profile_entries WHERE id=?', (entry_id,)).rowcount:
                raise ValueError('找不到这条资料')
            db.execute('DELETE FROM profile_fts WHERE entry_id=?', (entry_id,))

    def list(self, query='', subject='', kind='', category='', key=None, active=False):
        where, args = [], []
        for field, value in (('subject', subject), ('kind', kind), ('category', category)):
            if value:
                where.append(f'p.{field}=?'); args.append(value)
        if key:
            where.append("((p.account='' AND p.friend='') OR (p.account=? AND p.friend=?))")
            args.extend(key)
        if active:
            where.append('p.enabled=1')
        match = fts_query(query)
        if query.strip() and not match:
            return []
        join, order = '', 'p.updated_at DESC, p.rowid DESC'
        if match:
            join = ' JOIN profile_fts ON profile_fts.entry_id=p.id'
            where.append('profile_fts MATCH ?'); args.append(match)
            order = 'bm25(profile_fts), p.updated_at DESC'
        with self.chat.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT p.* FROM profile_entries p' + join
                + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY ' + order + ' LIMIT 200', args)]
        library = ProfileCategories(self.chat)
        pinned = {}
        for who in {r['subject'] for r in rows}:
            tree = library.get(who)
            labels = paths(tree)
            pinned[who] = {labels[node['id']] for root in tree for node in [root, *root['children']] if node.get('pinned')}
        return [{**r, 'pinned': r['kind'] in ('overview', 'fact') and r['category'] in pinned[r['subject']]} for r in rows]

    def relationships(self, query, key):
        with self.chat.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM qq_friends')]
        selected = [r for r in rows if (r['account'], r['friend']) == key] if key else [
            r for r in rows if r['friend'] in query or (r['name'] and r['name'] in query)]
        return [{'account': r['account'], 'friend': r['friend'], 'name': r['name'],
                 'owner_relationship': r['owner_relationship'], 'miko_relationship': r['miko_relationship'],
                 'miko_introduced': bool(r['miko_introduced']), 'intro_receipt_id': r['intro_receipt_id']}
                for r in selected[:8]]

    def context(self, query, key=None):
        # Only explicitly pinned profiles are always supplied.
        pinned = [r for r in self.list(key=key, active=True) if r.get('pinned')]
        recent = sorted(self.list(kind='recent', key=key, active=True),
                        key=lambda r: (r['as_of'] or '', r['updated_at']), reverse=True)[:5]
        matches = [r for r in self.list(query, key=key, active=True) if r['kind'] != 'recent'][:12]
        rows, used, remaining = [], set(), 20000
        for row in pinned + recent + matches:
            if row['id'] in used or (not row.get('pinned') and len(row['content']) > remaining):
                continue
            used.add(row['id']); remaining -= len(row['content'])
            rows.append({name: row[name] for name in ('id', 'subject', 'kind', 'category', 'title', 'content',
                                                    'account', 'friend', 'as_of', 'source', 'pinned')})
        return {'current_time': datetime.now().astimezone().isoformat(timespec='minutes'),
                'role_material': rows, 'friend_relationships': self.relationships(query, key)}
