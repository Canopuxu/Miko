"""Local sticker originals/notes adapted from Tulpa ChatMedia and StickerLibrary (MIT)."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import time

from PIL import Image, UnidentifiedImageError
from miko.storage.search import terms


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

    def path(self, account, digest):
        if not account.isascii() or not account.isdigit() or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('表情编号无效')
        return self.root / account / digest

    def list(self, account):
        with self.store.chat.connect() as db:
            return [dict(r, sticker_id=r['digest'], info=json.loads(r['info'])) for r in db.execute(
                'SELECT * FROM qq_stickers WHERE account=? ORDER BY updated_at DESC', (account,))]

    def add(self, account, raw, description):
        description = ' '.join(description.split())[:200]
        if not description:
            raise ValueError('请填写表情含义和适用场景')
        info, digest = image_info(raw), hashlib.sha256(raw).hexdigest()
        path = self.path(account, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)  # Original animation bytes, rather than a sampled frame.
        with self.store.chat.connect() as db:
            db.execute('''INSERT INTO qq_stickers(account,digest,description,info,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(account,digest) DO UPDATE SET description=excluded.description,
                    info=excluded.info,enabled=1,updated_at=excluded.updated_at''',
                (account, digest, description, json.dumps(info), time.time()))
        return digest

    def edit(self, account, digest, description, enabled):
        self.path(account, digest)
        description = ' '.join(description.split())[:200]
        if not description:
            raise ValueError('请填写表情含义和适用场景')
        with self.store.chat.connect() as db:
            if not db.execute('UPDATE qq_stickers SET description=?,enabled=?,updated_at=? WHERE account=? AND digest=?',
                              (description, int(enabled), time.time(), account, digest)).rowcount:
                raise ValueError('找不到这个表情')

    def read(self, account, digest, *, require_enabled=True):
        with self.store.chat.connect() as db:
            row = db.execute('SELECT * FROM qq_stickers WHERE account=? AND digest=?', (account, digest)).fetchone()
        if not row or (require_enabled and not row['enabled']):
            raise ValueError('表情不存在或已停用')
        path = self.path(account, digest)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('表情原文件不存在或过大')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('表情原文件校验失败')
        return raw, json.loads(row['info'])

    def candidates(self, account, text):
        query = terms(text)
        rows = sorted((r for r in self.list(account) if r['enabled']),
                      key=lambda r: (len(query & terms(r['description'])), r['use_count'], r['updated_at']), reverse=True)
        result = []
        for row in rows:
            try:
                self.read(account, row['digest'])
            except (OSError, ValueError):
                continue
            result.append({'sticker_id': row['digest'], 'description': row['description']})
            if len(result) == 8:
                break
        return result

    def segment(self, account, digest):
        raw, _ = self.read(account, digest)
        return {'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(raw).decode('ascii')}}

    def used(self, account, digest):
        with self.store.chat.connect() as db:
            db.execute('UPDATE qq_stickers SET use_count=use_count+1 WHERE account=? AND digest=?', (account, digest))
