"""Private-message scheduler adapted from Tulpa's local QQ loop."""
import asyncio
from dataclasses import dataclass
import logging
import sqlite3
import time

from miko.adapters.qq.onebot import EventReceiver, OneBot, SendError, SendUncertain
from miko.storage.qq import QqStore


class StaleReply(ValueError):
    pass


@dataclass
class Worker:
    version: int
    run_id: str
    task: asyncio.Task | None = None
    turn: object | None = None
    phase: str = 'generating'


class QqService:
    wait_seconds = 120
    quiet_seconds = 2
    collect_seconds = 8
    max_reply_chars = 80

    def __init__(self, chat, config, *, client=None, clock=time.time):
        self.chat, self.clock = chat, clock
        self.store = QqStore(chat.store)
        self.store.recover()
        self.client = client or (OneBot(config) if config else None)
        self.receiver = EventReceiver(self.client, self.receive, self.source_changed) if self.client else None
        if config:
            for friend in config.friends:
                self.store.add_friend(config.account, friend['id'], friend.get('name') or friend['id'])
        self.workers = {}
        self.versions = {}
        self.storage_faults = {}
        self.epoch = self.clock()
        self.loop_task = None
        self.halted = False

    def start(self):
        self.halted = False
        if self.receiver:
            self.receiver.start()
        self.loop_task = asyncio.create_task(self.loop())

    async def close(self):
        self.halted = True
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        if self.receiver:
            await self.receiver.close()
        for key in list(self.workers):
            await self.invalidate(key)
        await asyncio.gather(*(w.task for w in list(self.workers.values())), return_exceptions=True)

    def status(self):
        receiver = self.receiver.status() if self.receiver else {'state': 'unconfigured', 'account': '', 'note': '尚未配置 QQ 连接'}
        friends = []
        for friend in self.store.friends():
            key = (friend['account'], friend['friend'])
            friend['state'] = ('disabled' if not friend['enabled'] else 'blocked' if friend['blocked']
                else self.workers[key].phase if key in self.workers else 'waiting' if friend['due_at']
                else 'chatting' if friend['mode'] == 'miko' else 'listening')
            if key in self.storage_faults:
                friend.update(state='blocked', blocked=1, note=self.storage_faults[key])
            friends.append(friend)
        return {'receiver': receiver, 'friends': friends, 'runs': self.store.runs(),
                'wait_seconds': self.wait_seconds, 'max_reply_chars': self.max_reply_chars}

    async def configure(self, key, enabled):
        if not self.store.friend(key):
            raise ValueError('找不到这位好友')
        if enabled:
            if not self.client or not self.receiver or self.receiver.status()['state'] != 'connected':
                raise ValueError('QQ 事件通道尚未就绪，请先检查连接')
            if self.receiver.status()['account'] != key[0]:
                raise ValueError('QQ 接收账号与好友配置不一致')
            await self.client.target(*key)
        await self.invalidate(key)
        self.store.update_friend(key, enabled=int(enabled), blocked=0, mode='owner',
            note='等待新私聊消息' if enabled else '已关闭', activated_at=self.clock(),
            pending_since=None, pending_last=None, due_at=None)
        self.storage_faults.pop(key, None)
        return self.status()

    async def invalidate(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1
        worker = self.workers.get(key)
        if worker and worker.phase == 'generating' and worker.turn:
            await self.chat.cancel(worker.turn)

    async def source_changed(self, state):
        if state == 'connected':
            self.epoch = self.clock()
        for friend in self.store.friends():
            key = (friend['account'], friend['friend'])
            await self.invalidate(key)
            self.store.update_friend(key, mode='owner', pending_since=None, pending_last=None, due_at=None)

    async def manual(self, key):
        await self.invalidate(key)
        self.store.update_friend(key, mode='owner', note='你正在主导；后续新消息将重新等待两分钟',
            pending_since=None, pending_last=None, due_at=None)

    async def receive(self, event):
        post = event.get('post_type')
        if post not in ('message', 'message_sent') or event.get('message_type') != 'private':
            return
        account, uid = str(event.get('self_id', '')), str(event.get('user_id', ''))
        is_self = post == 'message_sent' or uid == account
        friend = str(event.get('target_id', '')) if is_self else uid
        key = (account, friend)
        cfg = self.store.friend(key)
        stamp, mid = event.get('time'), event.get('message_id')
        if (not cfg or type(stamp) not in (int, float) or type(mid) is not int
                or stamp < int(self.epoch) or stamp > self.clock() + 10):
            return
        parts, text_only = [], True
        segments = event.get('message')
        if isinstance(segments, str):
            parts = [segments]
        elif isinstance(segments, list):
            for segment in segments:
                kind = segment.get('type')
                data = segment.get('data') or {}
                if kind == 'text':
                    parts.append(str(data.get('text', '')))
                else:
                    parts.append('[' + {'image': '图片', 'record': '语音', 'face': '表情', 'reply': '引用'}.get(kind, '非文本消息') + ']')
                    text_only = False
        if not parts:
            return
        text = ''.join(parts)
        known = is_self and self.store.receipt(key, mid)
        sending = key in self.workers and self.workers[key].phase == 'sending'
        origin = 'miko' if known else 'pending' if is_self and sending else 'unknown' if is_self and cfg['blocked'] else 'manual' if is_self else 'friend'
        if not self.store.event(key, mid, text, stamp, origin):
            return
        if origin in ('pending', 'unknown'):
            return
        author = 'miko' if known else 'owner' if is_self else f'qq:{friend}'
        self.store.record(key, mid, author)
        if is_self:
            if not known:
                await self.manual(key)
            return
        await self.invalidate(key)
        if not cfg['enabled'] or cfg['blocked'] or key in self.storage_faults or stamp < int(cfg['activated_at']):
            return
        if not text_only:
            self.store.update_friend(key, pending_since=None, pending_last=None, due_at=None,
                note='收到非纯文本消息，本阶段仅保存记录')
            return
        now = self.clock()
        first = cfg['pending_since'] if cfg['pending_since'] is not None else now
        batch_due = min(now + self.quiet_seconds, first + self.collect_seconds)
        due = batch_due if cfg['mode'] == 'miko' else max(first + self.wait_seconds, batch_due)
        self.store.update_friend(key, pending_since=first, pending_last=now, due_at=due,
            note='等待你回复' if cfg['mode'] == 'owner' else '正在合并连续消息')

    def check(self, key, worker):
        cfg = self.store.friend(key)
        receiver = self.receiver.status() if self.receiver else {}
        if (self.halted or not cfg or not cfg['enabled'] or cfg['blocked'] or key in self.storage_faults
            or self.versions.get(key, 0) != worker.version or receiver.get('state') != 'connected'
            or receiver.get('account') != key[0]):
            raise StaleReply('上下文或接话状态已变化，未发送旧回复')

    async def tick(self):
        if not self.receiver or self.receiver.status()['state'] != 'connected':
            return
        for friend in self.store.friends():
            key = (friend['account'], friend['friend'])
            if (friend['enabled'] and not friend['blocked'] and key not in self.storage_faults and friend['due_at'] is not None
                    and friend['due_at'] <= self.clock() and key not in self.workers):
                worker = Worker(self.versions.get(key, 0), self.store.new_run(key, self.clock()))
                self.store.update_friend(key, due_at=None, mode='miko', pending_since=None, pending_last=None)
                self.workers[key] = worker
                worker.task = asyncio.create_task(self.reply(key, worker))

    async def loop(self):
        while True:
            await self.tick()
            await asyncio.sleep(.2)

    async def settle_echoes(self, key, *, uncertain=False):
        manual = False
        for echo in self.store.echoes(key):
            mid = echo['message_id']
            known = self.store.receipt(key, mid)
            origin = 'miko' if known else 'unknown' if uncertain else 'manual'
            self.store.origin(key, mid, origin)
            if origin != 'unknown':
                self.store.record(key, mid, 'miko' if known else 'owner')
            manual |= origin == 'manual'
        if manual:
            await self.manual(key)

    async def reply(self, key, worker):
        uncertain = False
        try:
            self.check(key, worker)
            cfg = self.store.friend(key)
            worker.turn = self.chat.begin_qq(cfg['conversation_id'], self.store.context(key))
            await worker.turn.task
            self.check(key, worker)
            text = worker.turn.text.strip()
            if worker.turn.status != 'completed' or not text or len(text) > self.max_reply_chars:
                raise ValueError(worker.turn.error or '本轮未生成可发送的 80 字以内回复')
            self.store.update_run(worker.run_id, text=text)

            def guard():
                self.check(key, worker)
                self.store.update_run(worker.run_id, state='sending')
                worker.phase = 'sending'

            receipt = await self.client.send(*key, text, guard)
            # Persist the receipt before accepting its outgoing event as Miko's.
            self.store.update_run(worker.run_id, state='sent', receipt_id=receipt['message_id'])
            self.store.event(key, receipt['message_id'], text, receipt['timestamp'], 'miko')
            self.store.origin(key, receipt['message_id'], 'miko')
            self.store.record(key, receipt['message_id'], 'miko')
            if self.versions.get(key, 0) == worker.version:
                self.store.update_friend(key, mode='miko', note='Miko 正在接话',
                    pending_since=None, pending_last=None, due_at=None)
            await self.settle_echoes(key)
        except StaleReply as exc:
            self.fail_run(key, worker, 'stale', str(exc))
        except SendUncertain as exc:
            uncertain = True
            self.fail_run(key, worker, 'unknown', str(exc))
        except Exception as exc:
            uncertain = worker.phase == 'sending'
            self.fail_run(key, worker, 'unknown' if uncertain else 'error',
                '发送结果未知，请到 QQ 核对；不会重发' if uncertain else
                str(exc) if isinstance(exc, (ValueError, SendError)) else '本轮生成未完成')
        finally:
            try:
                await self.settle_echoes(key, uncertain=uncertain)
            except sqlite3.Error:
                self.storage_faults[key] = '消息保存失败，请检查本机存储并到 QQ 核对后恢复'
                logging.getLogger(__name__).exception('Unable to save QQ message origin')
            finally:
                self.workers.pop(key, None)

    def fail_run(self, key, worker, state, error):
        try:
            self.store.update_run(worker.run_id, state=state, error=error)
            if state == 'unknown':
                self.store.update_friend(key, blocked=1, note=error, due_at=None,
                    pending_since=None, pending_last=None)
            elif state == 'error' and self.versions.get(key, 0) == worker.version:
                self.store.update_friend(key, mode='owner', pending_since=None, pending_last=None, due_at=None)
        except sqlite3.Error:
            self.storage_faults[key] = '回复记录保存失败，请检查本机存储并到 QQ 核对后恢复'
            logging.getLogger(__name__).exception('Unable to save QQ reply outcome')
