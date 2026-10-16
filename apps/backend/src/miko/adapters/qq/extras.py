"""Small host adapters for Tulpa's library and reaction modules."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .onebot import SendError, SendUncertain
from .tulpa_extras.onebot import OneBotError, OneBotUncertain


class ExtrasAccess:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY,grant_id TEXT,
                    conversation_id TEXT,active INTEGER DEFAULT 1,created REAL,reaction_seen INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS chat_inbox(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,
                    message_id TEXT,payload TEXT,delivered INTEGER DEFAULT 1,UNIQUE(session_id,message_id));
                CREATE TABLE IF NOT EXISTS chat_media_assets(id TEXT PRIMARY KEY,session_id TEXT,
                    origin TEXT,ref TEXT,digest TEXT DEFAULT '',seen INTEGER DEFAULT 0,description TEXT DEFAULT '',updated REAL);
                CREATE TABLE IF NOT EXISTS chat_sticker_notes(grant_id TEXT,account TEXT,digest TEXT,
                    description TEXT,tags TEXT,updated REAL,PRIMARY KEY(grant_id,account,digest));
                CREATE TABLE IF NOT EXISTS calls(grant_id TEXT,tool TEXT,at REAL);
            ''')

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()


class SyncQq:
    def __init__(self, qq):
        self.qq = qq
        self.loop = None
        self.transport = self
        self.url = qq.client.config.http_url if qq.client and hasattr(qq.client, 'config') else 'http://127.0.0.1:9'
        self.token = ''  # Calls use the existing Miko client; credentials are never copied.

    def run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=35)

    def login(self):
        return self.run(self.qq.client.login())

    def target(self, account, friend):
        return self.run(self.qq.client.target(account, friend))

    def call(self, action, payload, *, approved=False):
        try:
            if action == 'get_msg':
                return self.run(self.qq.client.call(action, payload))
            if action == 'set_msg_emoji_like' and approved:
                return self.run(self.qq.client.call(action, payload, sending=True))
            raise OneBotError('操作不在本次适配范围内')
        except SendUncertain as exc:
            raise OneBotUncertain(str(exc)) from None
        except (SendError, TimeoutError) as exc:
            if action == 'set_msg_emoji_like':
                raise OneBotUncertain(str(exc)) from None
            raise OneBotError(str(exc)) from None


def account_scope(account):
    if not account.isascii() or not account.isdigit():
        raise ValueError('QQ 账号无效')
    grant = dict(id='miko-stickers-' + account, scope=dict(account=account))
    row = dict(id=hashlib.sha256(('miko-stickers:' + account).encode()).hexdigest()[:32],
               conversation_id=account + ':library')
    return grant, row
