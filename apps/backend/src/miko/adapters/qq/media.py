"""Local sticker originals/notes adapted from Tulpa ChatMedia and StickerLibrary (MIT)."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace

from PIL import Image, UnidentifiedImageError
from .extras import ExtrasAccess, account_scope
from .tulpa_extras.mcp_sticker_library import StickerLibrary as TulpaLibrary


MAX_BYTES = 6 * 1024 * 1024


def image_info(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('图片为空或超过单张 6 MiB 上限')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            frames = getattr(im, 'n_frames', 1)
            if im.format not in ('PNG', 'JPEG', 'GIF', 'WEBP'):
                raise ValueError('支持 PNG、JPEG、GIF 和 WEBP')
            if frames > 300 or im.width * im.height * frames > 80000000 or im.width * im.height > 12000000:
                raise ValueError('图片尺寸或动画帧数过大')
            info = {'format': im.format, 'dimensions': list(im.size), 'total_frames': frames}
            im.verify()
            return info
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, EOFError):
        raise ValueError('图片损坏或不完整') from None


class StickerLibrary:
    def __init__(self, store, root: Path):
        self.store, self.root = store, root
        root.mkdir(parents=True, exist_ok=True)
        self.access = ExtrasAccess(root.parent / 'extras.sqlite3')
        self.chat = self
        self.qq = None
        self.receiver = SimpleNamespace(status=lambda: self.qq.receiver.status() if self.qq and self.qq.receiver else {})
        self.library = TulpaLibrary(self)
        with self.access.connect() as db:
            db.execute("UPDATE sticker_library SET last_state='UNKNOWN' WHERE last_state='EXECUTING'")
        # Retain the existing UI metadata while moving originals into the upstream library.
        with store.chat.connect() as db:
            legacy = [dict(r) for r in db.execute('SELECT * FROM qq_stickers')]
        for row in legacy:
            grant, context = account_scope(row['account'])
            if self.library.find(grant, context, row['digest']):
                continue
            old = root / row['account'] / row['digest']
            if old.is_file():
                raw = old.read_bytes()
                if hashlib.sha256(raw).hexdigest() == row['digest']:
                    self._retain(row['account'], raw, dict(description=row['description']), row['info'])
                    with self.access.connect() as db:
                        db.execute('UPDATE sticker_library SET ready=?,use_count=? WHERE grant_id=? AND account=? AND digest=?',
                            (row['enabled'], row['use_count'], grant['id'], row['account'], row['digest']))

    def bind(self, qq):
        self.qq = qq

    def live_target(self, grant, cid):
        if cid != grant['scope']['account'] + ':library':
            raise ValueError('表情来源不属于这个账号')

    def guard(self, grant, sid, name, cancel):
        _, context = account_scope(grant['scope']['account'])
        if sid != context['id'] or cancel.is_set():
            raise ValueError('表情会话已经失效')

    def cached(self, digest):
        return None  # All Miko imports are pinned before a note is recorded.

    def _digest(self, account, value):
        grant, row = account_scope(account)
        if re.fullmatch('[a-f0-9]{64}', value):
            return value
        if re.fullmatch('[a-f0-9]{32}', value):
            with self.access.connect() as db:
                found = db.execute('SELECT digest FROM chat_media_assets WHERE id=? AND session_id=? AND seen=1',
                                   (value, row['id'])).fetchone()
            if found:
                return found['digest']
        raise ValueError('表情编号无效')

    def path(self, account, digest):
        grant, _ = account_scope(account)
        return self.library.path(grant['id'], account, self._digest(account, digest))

    def list(self, account):
        grant, _ = account_scope(account)
        with self.access.connect() as db:
            knowledge = {r['digest']: dict(r) for r in db.execute(
                'SELECT * FROM sticker_library WHERE grant_id=? AND account=?', (grant['id'], account))}
        with self.store.chat.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM qq_stickers WHERE account=? ORDER BY updated_at DESC', (account,))]
        return [dict(r, sticker_id=r['digest'], info=json.loads(r['info']),
            semantic=json.loads(knowledge.get(r['digest'], {}).get('semantic', '{}')),
            last_state=knowledge.get(r['digest'], {}).get('last_state', '')) for r in rows]

    def _retain(self, account, raw, fields, info):
        grant, row = account_scope(account)
        digest = hashlib.sha256(raw).hexdigest()
        asset = dict(ref='local:' + digest, origin='local', digest=digest)
        if not self.library.remember(grant, row, asset, raw, json.loads(info) if isinstance(info, str) else info):
            raise ValueError('表情库已达数量上限')
        item = self.library.find(grant, row, digest)
        result = self.library.pin(grant, row, item, raw)
        if result['state'] != 'SAVED':
            raise ValueError('本地表情库空间已满')
        self.library.note(grant, row, asset, fields)
        return digest

    def add(self, account, raw, description, *, semantic=None):
        description = ' '.join(description.split())[:200]
        if not description:
            raise ValueError('请填写表情含义和适用场景')
        info, digest = image_info(raw), hashlib.sha256(raw).hexdigest()
        self._retain(account, raw, {**(semantic or {}), 'description': description}, info)
        with self.store.chat.connect() as db:
            db.execute('''INSERT INTO qq_stickers(account,digest,description,info,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(account,digest) DO UPDATE SET description=excluded.description,
                    info=excluded.info,enabled=1,updated_at=excluded.updated_at''',
                (account, digest, description, json.dumps(info), time.time()))
        return digest

    def edit(self, account, digest, description, enabled, *, semantic=None):
        digest = self._digest(account, digest)
        description = ' '.join(description.split())[:200]
        if not description:
            raise ValueError('请填写表情含义和适用场景')
        grant, row = account_scope(account)
        item = self.library.find(grant, row, digest)
        if not item:
            raise ValueError('找不到这个表情')
        fields = json.loads(item['semantic'])
        fields.update(semantic or {})
        fields.update(description=description, uncertainty='')
        self.library.note(grant, row, dict(digest=digest), fields)
        with self.access.connect() as db:
            db.execute('UPDATE sticker_library SET ready=? WHERE grant_id=? AND account=? AND digest=?',
                       (int(enabled), grant['id'], account, digest))
        with self.store.chat.connect() as db:
            if not db.execute('UPDATE qq_stickers SET description=?,enabled=?,updated_at=? WHERE account=? AND digest=?',
                              (description, int(enabled), time.time(), account, digest)).rowcount:
                raise ValueError('找不到这个表情')

    def read(self, account, digest, *, require_enabled=True):
        digest = self._digest(account, digest)
        with self.store.chat.connect() as db:
            row = db.execute('SELECT * FROM qq_stickers WHERE account=? AND digest=?', (account, digest)).fetchone()
        if not row or (require_enabled and not row['enabled']):
            raise ValueError('表情不存在或已停用')
        grant, context = account_scope(account)
        raw, state = self.library.verified_bytes(grant, context, self.library.find(grant, context, digest))
        if raw is None:
            raise ValueError('表情原文件不可用或校验失败')
        return raw, json.loads(row['info'])

    def candidates(self, account, text):
        grant, row = account_scope(account)
        rows, _ = self.library.candidates(grant, row, [dict(content=text, is_self=False)])
        return [dict(sticker_id=r['sticker_id'], description=r['model_note'], tags=r['tags'],
                     emotion=r['emotion'], usage=r['usage'], avoid=r['avoid']) for r in rows]

    def segment(self, account, digest):
        raw, _ = self.read(account, digest)
        return {'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(raw).decode('ascii')}}

    def attempt(self, account, digest, operation):
        grant, row = account_scope(account)
        digest = self._digest(account, digest)
        with self.access.connect() as db:
            self.library.record_attempt(db, grant, row, digest, operation)

    def outcome(self, account, digest, operation, state):
        grant, row = account_scope(account)
        digest = self._digest(account, digest)
        with self.access.connect() as db:
            self.library.record_result(db, grant, row, digest, operation, state)

    def used(self, account, digest, operation=None):
        digest = self._digest(account, digest)
        self.outcome(account, digest, operation or str(time.time_ns()), 'SUCCEEDED')
        grant, row = account_scope(account)
        item = self.library.find(grant, row, digest)
        with self.store.chat.connect() as db:
            db.execute('UPDATE qq_stickers SET use_count=? WHERE account=? AND digest=?', (item['use_count'], account, digest))

    def delete(self, account, digest):
        digest = self._digest(account, digest)
        path = self.path(account, digest)
        grant, _ = account_scope(account)
        with self.access.connect() as db:
            for table in ('sticker_library', 'sticker_terms', 'sticker_collections', 'sticker_usage', 'chat_sticker_notes'):
                db.execute(f'DELETE FROM {table} WHERE grant_id=? AND account=? AND digest=?', (grant['id'], account, digest))
        with self.store.chat.connect() as db:
            db.execute('DELETE FROM qq_stickers WHERE account=? AND digest=?', (account, digest))
        path.unlink(missing_ok=True)
