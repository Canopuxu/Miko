"""Miko storage and conversation scope for Tulpa's read-only chat tools."""
from contextlib import contextmanager
from datetime import datetime
import json
import sqlite3
import threading
from zoneinfo import ZoneInfo

from miko.settings import REPO_ROOT
from miko.storage.search import keywords, terms
from .tulpa_upstream.read_tools import ReadTools, ContextStore
from .tulpa_upstream.retrieval import Plan


CONTRACT = REPO_ROOT / 'config/defaults/history-tools.json'


class ReadStore(ContextStore):
    def __init__(self, memory, conversation_id):
        self.projection = memory.social.store
        self.chat = memory.chat.store
        self.conversation_id = conversation_id

    @contextmanager
    def connect(self):
        with self.projection.connect() as db:
            db.execute('ATTACH DATABASE ? AS original', (str(self.chat.path),))
            db.create_function('miko_current_conversation', 0, lambda: self.conversation_id)
            # Keep Tulpa's numeric IDs and FTS; expose only live original records
            # from this exact conversation. No second copy of message bodies.
            db.executescript('''CREATE TEMP VIEW messages AS
              SELECT p.id,p.original_id,o.source AS platform,o.conversation_id,
                p.conversation,p.conversation_type,p.sender_id,p.timestamp,p.content,
                p.is_self,p.reply_to,n.display_name AS sender,'[]' AS media_json
              FROM main.messages p JOIN original.messages o ON o.id=p.original_id
              JOIN original.people n ON n.id=p.sender_id
              WHERE p.conversation_id LIKE 'miko|%' AND o.status='completed'
                AND o.conversation_id=miko_current_conversation()
                AND NOT EXISTS(SELECT 1 FROM original.qq_events e
                  WHERE o.id='qq:'||e.account||':'||e.friend||':'||e.message_id AND e.recalled=1);
            ''')
            yield db


class HistoryTools(ReadTools):
    def __init__(self, memory, conversation_id):
        self.memory = memory
        self.store = ReadStore(memory, conversation_id)
        self.qq = conversation_id.startswith('qq:')
        with self.store.connect() as db:
            source = db.execute('SELECT platform FROM messages WHERE conversation_id=? LIMIT 1',
                                (conversation_id,)).fetchone()
            snapshot = db.execute('SELECT coalesce(max(id),0) FROM messages').fetchone()[0]
        platform = source[0] if source else ('qq' if conversation_id.startswith('qq:') else 'web')
        self.plan = Plan(platforms=[platform], conversations=[json.dumps([platform, conversation_id])],
                         snapshot_max_id=snapshot)
        self.max_messages, self.max_chars, self.max_calls = 100, 24000, 10
        self.messages, self.originals, self.contexts = {}, {}, {}
        self.used_chars = self.calls = 0
        self.incomplete = False
        self.context_requirements = {}
        self.lock = threading.RLock()
        self.contract = json.loads(CONTRACT.read_text('utf-8'))
        text = {'type': 'string'}
        page = {'type': 'integer', 'minimum': 1, 'maximum': 50}
        offset = {'type': 'integer', 'minimum': 0}
        mid = {'type': 'integer'}
        dates = {'start': text, 'end': text}
        specs = {
            'search_messages': ({'query': text, 'offset': offset, 'limit': page, **dates}, ['query']),
            'get_context': ({'message_id': mid, 'radius': {'type': 'integer', 'minimum': 0, 'maximum': 8}}, ['message_id']),
            'read_conversation': ({'order': {'type': 'string', 'enum': ['oldest', 'newest']},
                'limit': page, 'offset': offset, 'snapshot_max_id': {'type': 'integer', 'minimum': 0}, **dates}, []),
            'read_message': ({'message_id': mid, 'offset': offset,
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8000}}, ['message_id']),
        }
        self.schemas = [dict(name=name, description=self.contract['tools'][name], parameters={
            'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False})
            for name, (properties, required) in specs.items()]

    @property
    def schemas_for_model(self):
        return [{'type': 'function', 'function': row} for row in self.schemas]

    def _admit(self, rows, *, char_limit=None):
        admitted = []
        for row in rows:
            row = dict(row)
            mid = row['id']
            if mid in self.messages:
                admitted.append(self.messages[mid])
                continue
            entry = {k: row[k] for k in ('id', 'original_id', 'platform', 'conversation_id', 'conversation',
                                         'conversation_type', 'sender', 'sender_id', 'is_self')}
            entry.update(time=datetime.fromtimestamp(row['timestamp'] / 1000, ZoneInfo('Asia/Shanghai')).isoformat(),
                         content=row['content'][:2000], truncated=len(row['content']) > 2000)
            size = len(json.dumps(entry, ensure_ascii=False))
            ceiling = self.max_chars if char_limit is None else min(self.max_chars, char_limit)
            if len(self.messages) >= self.max_messages or self.used_chars + size > ceiling:
                self.incomplete = True
                continue
            self.messages[mid], self.originals[mid] = entry, row
            self.used_chars += size
            self.incomplete |= entry['truncated']
            admitted.append(entry)
        return admitted

    def _page(self, rows, total, offset):
        selected = self._admit(rows)
        more = offset + len(rows) < total
        return dict(messages=selected, match_count=total, has_more=more,
                    next_offset=offset + len(rows) if more else None, budget_limited=len(selected) < len(rows))

    def _read_message(self, args):
        where, values = self._scope({})
        with self.store.connect() as db:
            row = db.execute(f'SELECT * FROM messages m WHERE {where} AND m.id=?',
                             [*values, args['message_id']]).fetchone()
        if row is None:
            raise ValueError('message_out_of_scope')
        offset, limit = args.get('offset', 0), args.get('limit', 4000)
        content = row['content']
        piece = content[offset:offset + limit]
        if self.used_chars + len(piece) > self.max_chars:
            return {'error': 'reading_budget_exhausted'}
        self.used_chars += len(piece)
        self.originals[row['id']] = dict(row)
        return dict(message_id=row['id'], content=piece, offset=offset,
                    has_more=offset + limit < len(content),
                    next_offset=offset + limit if offset + limit < len(content) else None)

    def execute(self, name, args):
        with self.lock:
            self.calls += 1
            if self.calls > self.max_calls:
                return {'error': 'reading_budget_exhausted'}
            try:
                self._validate(name, args)
                if self.qq and 'message_id' in args:
                    with self.store.connect() as db:
                        row = db.execute('SELECT id FROM messages WHERE original_id=?',
                            (self.store.conversation_id + ':' + str(args['message_id']),)).fetchone()
                    if row is None:
                        raise ValueError('message_out_of_scope')
                    args = {**args, 'message_id': row['id']}
                value = getattr(self, '_' + name)(args)
                return self.present(value)
            except (ValueError, TypeError, KeyError, sqlite3.DatabaseError):
                return {'error': 'invalid_arguments_or_message_out_of_scope'}

    def present(self, value):
        """Keep QQ's real message IDs consistent with existing reply/quote fields."""
        def native(mid):
            row = self.originals.get(mid)
            return int(row['original_id'].rsplit(':', 1)[1]) if self.qq and row else mid
        def walk(item, key=''):
            if isinstance(item, dict):
                return {k: walk(v, k) for k, v in item.items()}
            if isinstance(item, list):
                return [walk(v, key) for v in item]
            if self.qq and key in ('id', 'message_id', 'anchor_id', 'hit_ids', 'message_ids', 'needs_context_ids'):
                return native(item)
            return item
        return self.memory.chat.store.identity().material(walk(value))

    def quote_records(self):
        if not self.qq:
            return []
        return [{'message_id': int(row['original_id'].rsplit(':', 1)[1])}
                for row in self.originals.values()]

    def seed(self, context, query):
        """Attach short source windows for the memories actually selected this turn."""
        wordset = terms(' '.join(keywords(query)), limit=None)
        anchors = []
        for group in ('people_memory', 'dated_experiences'):
            for item in context.get(group, [])[:4]:
                with self.memory.social.store.connect() as db:
                    sources = self.memory.structured._sources(db, item['id']) if item.get('id') else []
                wanted = wordset | terms(' '.join(str(item.get(k, '')) for k in ('topic', 'content', 'text')))
                ranked = sorted(sources, key=lambda row: len(wanted & terms(row['content'])), reverse=True)
                anchors.extend(row['id'] for row in ranked[:2] if wanted & terms(row['content']))
        for mid in dict.fromkeys(anchors):
            try:
                self._get_context({'message_id': mid, 'radius': 2})
            except ValueError:
                continue
            if len(self.messages) >= 12:
                break
        # Raw dialogue is searchable before automatic memory extraction has
        # produced an item. Reuse Tulpa's search-and-window path in that case.
        search = ' '.join(keywords(query))
        if search and len(self.messages) < 12:
            self.execute('search_messages', {'query': search, 'limit': 3})
        # Automatic reads share the same admission budget, but leave the full
        # tool-call allowance available for the model's deliberate followups.
        self.calls = 0
        return self.present({'messages': sorted(self.messages.values(), key=lambda row: (row['time'], row['id']))})['messages']
