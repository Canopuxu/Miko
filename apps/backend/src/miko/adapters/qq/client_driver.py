"""QQ client IPC bridge to the existing Miko OneBot transport (experimental)."""
import asyncio
import base64
import json
from pathlib import Path
import sqlite3

import httpx

from .media import image_info


class NativeClientDriver:
    def __init__(self, config: dict, database: Path):
        self.account = str(config['account'])
        self.receiving = None
        self.on_connected = None
        self.event_session = None
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{int(config['port'])}",
            headers={'Authorization': 'Bearer ' + config['token']}, trust_env=False, timeout=25)
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
            native_id TEXT NOT NULL, peer TEXT NOT NULL, record TEXT NOT NULL,
            emitted INTEGER NOT NULL DEFAULT 0, UNIQUE(account,native_id))''')
        # SnowLuma uses signed 32-bit IDs. Allocate new native-bridge IDs above
        # that range so existing Miko chat history cannot collide after switching.
        with self.db:
            if not self.db.execute("SELECT 1 FROM sqlite_sequence WHERE name='messages'").fetchone():
                self.db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('messages',2147483647)")
            else:
                self.db.execute("UPDATE sqlite_sequence SET seq=max(seq,2147483647) WHERE name='messages'")

    async def rpc(self, action, payload=None):
        response = await self.http.post('/rpc', json={'action': action, 'payload': payload or {}})
        response.raise_for_status()
        return response.json()['result']

    def remember(self, record):
        peer = str(record['peerUin'])
        if record['chatType'] != 1 or not peer.isdigit():
            raise ValueError('Expected private QQ message')
        with self.db:
            self.db.execute('''INSERT INTO messages(account,native_id,peer,record) VALUES(?,?,?,?)
                ON CONFLICT(account,native_id) DO UPDATE SET record=excluded.record''',
                (self.account, record['msgId'], peer, json.dumps(record, ensure_ascii=False)))
        return self.db.execute('SELECT id FROM messages WHERE account=? AND native_id=?',
            (self.account, record['msgId'])).fetchone()[0]

    def lookup(self, mid):
        row = self.db.execute('SELECT native_id,peer,record FROM messages WHERE account=? AND id=?',
            (self.account, int(mid))).fetchone()
        if not row:
            raise ValueError('Unknown bridge message ID')
        return row[0], row[1], json.loads(row[2])

    def quote_reference(self, record, element):
        quote = element['replyElement']
        peer = str(record['peerUin'])
        sender = str(quote.get('senderUid', ''))
        if sender not in (self.account, peer):
            raise ValueError('Unknown quoted author')
        seq, stamp = str(quote.get('replayMsgSeq', '')), str(quote.get('replyMsgTime', ''))
        # QQ commonly sends replayMsgId=0. Match an already received original
        # by conversation, sequence, author and time, never sequence alone.
        rows = self.db.execute('''SELECT id,record FROM messages WHERE account=? AND peer=?
            AND json_extract(record,'$.msgSeq')=?''', (self.account, peer, seq)).fetchall()
        unavailable = bool(quote.get('sourceMsgExpired') or quote.get('replyMsgRevokeType'))
        if not unavailable:
            for mid, raw in rows:
                original = json.loads(raw)
                if str(original['senderUin']) == sender and str(original['msgTime']) == stamp:
                    return mid
        # Preserve the quote preview QQ actually supplied when the original
        # predates this bridge. This is a snapshot, not a fabricated native ID.
        text = quote.get('sourceMsgText') or ''.join(
            item.get('textElemContent') or ('[图片]' if item.get('picElem') else '[表情]')
            for item in quote.get('sourceMsgTextElems', []))
        snapshot = dict(chatType=1, peerUin=peer, senderUin=sender, msgTime=stamp or '0',
            msgId=f"quote:{record['msgId']}:{element['elementId']}", quote_snapshot=True,
            recalled=unavailable, elements=[{'textElement': {'content': text}}])
        return self.remember(snapshot)

    async def event(self, record, *, images=False):
        mid = self.remember(record)
        sender = str(record['senderUin'])
        peer = str(record['peerUin'])
        if sender not in (self.account, peer):
            raise ValueError('Message author mismatch')
        segments = []
        for element in record.get('elements', []):
            if element.get('textElement'):
                segments.append({'type': 'text', 'data': {'text': element['textElement']['content']}})
            elif element.get('replyElement'):
                try:
                    segments.append({'type': 'reply', 'data': {'id': str(self.quote_reference(record, element))}})
                except ValueError:
                    segments.append({'type': 'text', 'data': {'text': '[引用消息暂不可用]'}})
            elif element.get('faceElement'):
                segments.append({'type': 'face', 'data': {'id': str(element['faceElement']['faceIndex'])}})
            elif element.get('picElement'):
                data = {'file': element['picElement']['fileName']}
                if images:
                    result = await self.rpc('read_image', {'user_id': peer, 'native_id': record['msgId'],
                        'element_id': element['elementId']})
                    raw = base64.b64decode(result['base64'], validate=True)
                    image_info(raw)
                    data['file'] = 'base64://' + result['base64']
                segments.append({'type': 'image', 'data': data})
        return {'post_type': 'message_sent' if sender == self.account else 'message',
            'message_type': 'private', 'sub_type': 'friend', 'self_id': int(self.account),
            'user_id': int(peer), 'target_id': int(peer), 'message_id': mid,
            'time': int(record['msgTime']), 'recalled': bool(record.get('recalled')), 'sender': {'user_id': int(sender),
                'nickname': record.get('sendNickName', '')}, 'message': segments}

    async def call(self, action, payload):
        if self.receiving is False and action == 'send_private_msg':
            raise ConnectionError('QQ event connection unavailable')
        if action in ('get_login_info', 'get_friend_list', 'fetch_sys_faces'):
            return await self.rpc(action)
        if action == 'set_qq_avatar':
            path = Path(payload['file'])
            if path.stat().st_size > 6*1024*1024:
                raise ValueError('Avatar exceeds image size limit')
            raw = path.read_bytes()
            info = image_info(raw)
            extension = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}[info['format']]
            result = await self.rpc('set_avatar', {'base64': base64.b64encode(raw).decode(), 'extension': extension})
            if result.get('updated') is not True:
                raise ValueError('Avatar update not confirmed')
            return None
        if action == 'get_msg':
            native_id, peer, stored = self.lookup(payload['message_id'])
            if stored.get('quote_snapshot'):
                return await self.event(stored)
            record = await self.rpc('get_native_message', {'user_id': peer, 'native_id': native_id})
            return await self.event(record, images=True)
        if action == 'send_private_msg':
            elements = []
            for segment in payload['message']:
                data = segment['data']
                if segment['type'] == 'text':
                    elements.append({'elementType': 1, 'elementId': '',
                        'textElement': {'content': data['text'], 'atType': 0}})
                elif segment['type'] == 'face':
                    elements.append({'elementType': 6, 'elementId': '',
                        'faceElement': {'faceIndex': int(data['id']), 'faceType': 1}})
                elif segment['type'] == 'image':
                    file = data.get('file', '')
                    if not file.startswith('base64://') or len(file) > 8*1024*1024 + 9:
                        raise ValueError('Expected bounded image bytes')
                    raw = base64.b64decode(file[9:], validate=True)
                    info = image_info(raw)
                    extension = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif', 'WEBP': 'webp'}[info['format']]
                    elements.extend(await self.rpc('prepare_image', {'base64': file[9:], 'extension': extension}))
                elif segment['type'] == 'reply':
                    native_id, peer, _ = self.lookup(data['id'])
                    if str(payload['user_id']) != peer:
                        raise ValueError('Quote belongs to another friend')
                    elements.extend(await self.rpc('prepare_reply', {'user_id': peer, 'native_id': native_id}))
                else:
                    raise ValueError('This message type is not enabled in the client bridge yet')
            if not elements:
                raise ValueError('Empty message')
            record = await self.rpc('send_native', {'user_id': payload['user_id'], 'elements': elements})
            if record.get('sendStatus') != 2:
                raise ValueError('No successful send receipt')
            return {'message_id': self.remember(record)}
        raise ValueError('Unsupported client bridge action')

    async def listen(self, publish, after=None):
        state = await self.rpc('diagnostics')
        session = state['session']
        self.event_session = session
        cursor = state['sequence'] if after is None else after
        failures = 0
        while True:
            self.receiving = False
            try:
                async for native in self._events(cursor, session):
                    await self._publish_native(native, publish)
                    cursor = native['sequence']
                    failures = 0
                raise httpx.ReadError('QQ event stream closed')
            except (httpx.TransportError, OSError):
                self.receiving = False
                failures += 1
                if failures >= 3:
                    raise
                await asyncio.sleep(1)
            finally:
                self.receiving = False

    async def _events(self, after, session):
        timeout = httpx.Timeout(45, connect=3)
        async with self.http.stream('GET', f'/events?after={after}&session={session}', timeout=timeout) as response:
            # A missing replay range or replaced QQ session must not silently
            # skip messages. Surface it instead of starting from a new cursor.
            response.raise_for_status()
            self.receiving = True
            if self.on_connected:
                await self.on_connected()
            async for line in response.aiter_lines():
                if line:
                    yield json.loads(line)

    async def _publish_native(self, native, publish):
        if native['name'] == 'bridge/heartbeat':
            return
        for record in native['payload'].get('msgList', []):
            if record.get('chatType') != 1:
                continue
            if str(record['senderUin']) == self.account and record['sendStatus'] != 2:
                continue
            event = await self.event(record)
            emitted = self.db.execute('SELECT emitted FROM messages WHERE id=?',
                (event['message_id'],)).fetchone()[0]
            if emitted:
                continue
            await publish(event)
            with self.db:
                self.db.execute('UPDATE messages SET emitted=1 WHERE id=?', (event['message_id'],))

    async def close(self):
        await self.http.aclose()
        self.db.close()
