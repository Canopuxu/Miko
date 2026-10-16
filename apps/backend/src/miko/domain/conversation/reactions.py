"""Attach Tulpa reactions to Miko's enabled group conversations and send queue."""
import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace
import uuid

from miko.adapters.qq.extras import SyncQq
from miko.adapters.qq.tulpa_extras.mcp_actions import Receipts, dump


class GroupReactions:
    def __init__(self, qq):
        from miko.adapters.qq.tulpa_extras.mcp_reactions import ChatReactions
        self.qq = qq
        self.access = qq.stickers.access
        self.client = SyncQq(qq)
        self.actions = Receipts(self.access, lambda: self.client)
        self.actions.recover()
        self.receiver = SimpleNamespace(status=lambda: qq.receiver.status() if qq.receiver else {},
            matches_client=lambda client: client is self.client)
        self.dispatch_guard = lambda: None
        self.turns = SimpleNamespace(before_dispatch=lambda: self.dispatch_guard())
        self.core = ChatReactions(self)
        self.lock = asyncio.Lock()
        self.sessions = {}
        self.events = {}

    def reset(self):
        self.core.reset_connection()
        self.sessions.clear()
        with self.access.connect() as db:
            db.execute('UPDATE chat_sessions SET active=0')

    def session(self, key):
        if key not in self.sessions:
            cfg = self.qq.store.friend(key)
            if not cfg or cfg['chat_type'] != 2:
                raise ValueError('消息表情回应仅用于已开启的群聊')
            sid = uuid.uuid4().hex
            grant = dict(id=hashlib.sha256((':'.join(key)).encode()).hexdigest(), scope=dict(chat_reactions=True))
            with self.access.connect() as db:
                db.execute('INSERT INTO chat_sessions(id,grant_id,conversation_id,created) VALUES(?,?,?,?)',
                    (sid, grant['id'], key[0] + ':group:' + key[1][6:], self.qq.clock()))
            self.sessions[key] = (grant, sid)
        return self.sessions[key]

    def validate(self, sid, gid):
        with self.access.connect() as db:
            row = db.execute('SELECT * FROM chat_sessions WHERE id=? AND grant_id=? AND active=1', (sid, gid)).fetchone()
        if not row:
            raise ValueError('群聊接话会话已失效')
        row = dict(row)
        account, _, group = row['conversation_id'].split(':')
        cfg = self.qq.store.friend((account, 'group-' + group))
        if not cfg or not cfg['enabled'] or cfg['blocked'] or self.qq.halted:
            raise ValueError('群聊接话已暂停')
        return dict(id=gid, scope=dict(chat_reactions=True)), row

    def live_target(self, grant, cid):
        account, kind, group = cid.split(':')
        cfg = self.qq.store.friend((account, 'group-' + group))
        if kind != 'group' or not cfg or not cfg['enabled']:
            raise ValueError('群聊接话已关闭')
        return dict(platform='qq', conversation_id=cid, conversation=cfg['name'])

    def observe(self, key, mid):
        grant, sid = self.session(key)
        event = self.qq.store.get_event(key, mid)
        payload = json.loads(event['payload'])
        if event['origin'] != 'friend' or event['recalled']:
            return
        body = dict(source='onebot_websocket', reaction_epoch=self.core.epoch,
            conversation_id=key[0] + ':group:' + key[1][6:], onebot_message_id=str(mid),
            sender_id=payload['sender_id'], timestamp=int(event['timestamp'] * 1000),
            content=event['text'], is_self=False, recalled=False)
        with self.access.connect() as db:
            db.execute('INSERT OR IGNORE INTO chat_inbox(session_id,message_id,payload) VALUES(?,?,?)', (sid, str(mid), dump(body)))
            db.execute('DELETE FROM chat_inbox WHERE session_id=? AND id NOT IN (SELECT id FROM chat_inbox WHERE session_id=? ORDER BY id DESC LIMIT 1000)', (sid, sid))

    def response_target(self, row, eid):
        with self.access.connect() as db:
            inbox = db.execute('SELECT payload FROM chat_inbox WHERE id=? AND session_id=? AND delivered=1', (eid, row['id'])).fetchone()
        if not inbox:
            raise ValueError('目标不属于当前群聊的已读消息')
        body = json.loads(inbox['payload'])
        account, _, group = row['conversation_id'].split(':')
        event = self.qq.store.get_event((account, 'group-' + group), int(body['onebot_message_id']))
        if not event or event['recalled']:
            raise ValueError('原消息已失效')
        return {k: body[k] for k in ('conversation_id', 'onebot_message_id', 'sender_id', 'timestamp')}

    def catalog(self, key, records):
        if not key[1].startswith('group-') or not self.qq.client:
            return [], []
        capabilities = getattr(self.qq.client, 'capabilities', {})
        if capabilities.get('message_reactions') is not True:
            return [], []
        _, sid = self.session(key)
        mids = {str(r['message_id']) for r in records if r['author']['kind'] == 'contact'}
        with self.access.connect() as db:
            targets = [dict(r) for r in db.execute('SELECT message_id,payload FROM chat_inbox WHERE session_id=? ORDER BY id DESC LIMIT 40', (sid,))
                       if r['message_id'] in mids and json.loads(r['payload']).get('reaction_epoch') == self.core.epoch]
        if not targets:
            return [], []
        selected = {'unicode_thumbsup', 'unicode_muscle', 'unicode_question', 'unicode_loud_cry'}
        return ([dict(reaction_id=c['id'], name=c['name'], hint=c['hint']) for c in self.core.policy()['candidates'] if c['id'] in selected and c['executable']],
                [int(r['message_id']) for r in targets])

    async def apply(self, key, bubble, operation, guard):
        self.client.loop = asyncio.get_running_loop()
        grant, sid = self.session(key)
        with self.access.connect() as db:
            target = db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?', (sid, str(bubble['message_id']))).fetchone()
        if not target:
            raise ValueError('无法回应当前连接之前的消息')
        args = dict(session_id=sid, event_id=target['id'], reaction_id=bubble['reaction_id'],
                    operation=bubble['operation'], idempotency_key=operation)
        async with self.lock:
            self.dispatch_guard = guard
            try:
                return await asyncio.to_thread(self.core.request, grant, args, threading.Event())
            finally:
                self.dispatch_guard = lambda: None

    def states(self, key):
        if key not in self.sessions:
            return []
        grant, sid = self.sessions[key]
        with self.access.connect() as db:
            rows = db.execute("SELECT params,target,result,state FROM operations WHERE grant_id=? AND kind='reaction' ORDER BY created DESC LIMIT 40", (grant['id'],)).fetchall()
        states = {}
        for row in rows:
            target, params, result = (json.loads(row[k]) for k in ('target', 'params', 'result'))
            mid = int(target['onebot_message_id'])
            unique = (mid, params['reaction_id'])
            if unique not in states:
                states[unique] = dict(message_id=mid, reaction_id=params['reaction_id'],
                    name=result.get('reaction', {}).get('name', params['reaction_id']),
                    active=row['state'] == 'SUCCEEDED' and params['operation'] == 'add')
        return [row for row in states.values() if row['active']]
