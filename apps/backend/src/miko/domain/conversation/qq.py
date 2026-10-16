"""Private-message scheduler adapted from Tulpa's local QQ loop."""
from miko.adapters.qq.images import attach_images
from miko.adapters.qq.avatar import ReplyAvatar

import asyncio
from dataclasses import dataclass
import logging
import json
import re
from pathlib import Path
import sqlite3
import time

from miko.adapters.qq.onebot import EventReceiver, OneBot, SendError, SendUncertain
from miko.adapters.qq.native import NativeQq, create_client, create_receiver
from miko.storage.qq import QqStore
from miko.adapters.qq.messages import FACES, parse_message, parse_bubbles, bubble_delay, update_face_catalog
from miko.adapters.qq.media import StickerLibrary
from miko.domain.conversation.context_selection import pending_contacts


class StaleReply(ValueError):
    pass


@dataclass
class Worker:
    version: int
    run_id: str
    task: asyncio.Task | None = None
    turn: object | None = None
    phase: str = 'generating'
    position: int | None = None
    previous_mode: str = 'owner'


class QqService:
    wait_seconds = 120
    quiet_seconds = 2
    collect_seconds = 8
    max_reply_chars = 100

    def __init__(self, chat, config, *, client=None, clock=time.time):
        self.chat, self.clock = chat, clock
        self.store = QqStore(chat.store)
        self.store.recover()
        local_root = getattr(getattr(chat, 'settings', None), 'local_root', chat.store.path.parent)
        self.stickers = StickerLibrary(self.store, Path(local_root) / 'data/qq/stickers')
        self.client = client or (create_client(config, Path(local_root)) if config else None)
        self.avatar = (ReplyAvatar(self.client, config.miko_avatar_file, config.owner_avatar_file)
                       if config and config.miko_avatar_file and config.owner_avatar_file else None)
        self.receiver = create_receiver(self.client, self.receive, self.source_changed) if self.client else None
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
        self.sync_groups()
        if self.receiver:
            self.receiver.start()
        self.loop_task = asyncio.create_task(self.loop())

    def sync_groups(self):
        if isinstance(self.client, NativeQq):
            self.client.driver.group_ids = {r['friend'][6:] for r in self.store.friends()
                if r['chat_type'] == 2 and r['enabled'] and r['account'] == self.client.config.account}

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
        if self.avatar:
            await self.avatar.close()
        if isinstance(self.client, NativeQq):
            await self.client.close()

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
        return {'receiver': receiver, 'friends': friends, 'runs': self.store.runs(), 'faces': FACES,
                'wait_seconds': self.wait_seconds, 'max_reply_chars': self.max_reply_chars,
                'character_name': self.chat.store.character_name(), 'owner_name': self.chat.store.person_name('owner')}

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
        self.sync_groups()
        return self.status()

    async def invalidate(self, key):
        self.versions[key] = self.versions.get(key, 0) + 1
        worker = self.workers.get(key)
        if worker and worker.phase == 'generating' and worker.turn:
            await self.chat.cancel(worker.turn)

    async def source_changed(self, state):
        if state == 'connected':
            self.epoch = self.clock()
            if isinstance(self.client, OneBot):
                try:
                    update_face_catalog(await self.client.faces())
                except (SendError, ValueError, KeyError, TypeError):
                    logging.getLogger(__name__).warning('Unable to refresh QQ face directory; retaining current candidates')
        for friend in self.store.friends():
            key = (friend['account'], friend['friend'])
            await self.invalidate(key)
            self.store.update_friend(key, mode='owner', pending_since=None, pending_last=None, due_at=None)

    async def manual(self, key):
        await self.invalidate(key)
        if self.avatar:
            worker = self.workers.get(key)
            await self.avatar.interrupt(worker.run_id if worker else None)
        self.store.update_friend(key, mode='owner', note='你正在主导；后续新消息将重新等待两分钟',
            pending_since=None, pending_last=None, due_at=None)

    async def receive(self, event):
        post = event.get('post_type')
        if post == 'notice' and event.get('notice_type') == 'friend_recall':
            key = (str(event.get('self_id', '')), str(event.get('user_id', '')))
            if self.store.friend(key) and type(event.get('message_id')) is int:
                self.store.recall(key, event['message_id'])
                await self.invalidate(key)
            return
        if post not in ('message', 'message_sent') or event.get('message_type') not in ('private', 'group'):
            return
        account, uid = str(event.get('self_id', '')), str(event.get('user_id', ''))
        group = event.get('message_type') == 'group'
        is_self = post == 'message_sent' or uid == account
        friend = 'group-' + str(event.get('group_id', '')) if group else str(event.get('target_id', '')) if is_self else uid
        key = (account, friend)
        cfg = self.store.friend(key)
        stamp, mid = event.get('time'), event.get('message_id')
        if (not cfg or type(stamp) not in (int, float) or type(mid) is not int
                or stamp < int(self.epoch) or stamp > self.clock() + 10):
            return
        if group and not cfg['enabled']:
            return
        payload = parse_message(event.get('message'))
        text = payload['text']
        if not text:
            return
        if group:
            sender = event.get('sender', {})
            name = sender.get('card') or sender.get('nickname') or uid
            payload.update(sender_id=uid, sender_name=name, sender_uid=sender.get('uid', ''))
            self.store.member(uid, sender.get('nickname') or uid)
        known = is_self and self.store.receipt(key, mid)
        sending = key in self.workers and self.workers[key].phase == 'sending'
        origin = 'miko' if known else 'pending' if is_self and sending else 'unknown' if is_self and cfg['blocked'] else 'manual' if is_self else 'friend'
        if not self.store.event(key, mid, text, stamp, origin, payload):
            return
        if origin in ('pending', 'unknown'):
            return
        author = 'miko' if known else 'owner' if is_self else f'qq:{uid}'
        self.store.record(key, mid, author)
        if is_self:
            if not known:
                await self.manual(key)
            return
        if not group:
            await self.invalidate(key)
        if not cfg['enabled'] or cfg['blocked'] or key in self.storage_faults or stamp < int(cfg['activated_at']):
            return
        now = self.clock()
        first = cfg['pending_since'] if cfg['pending_since'] is not None else now
        batch_due = min(now + self.quiet_seconds, first + self.collect_seconds)
        character = self.chat.store.character_name()
        name_pattern = re.escape(character)
        if character[0].isascii() and (character[0].isalnum() or character[0]=='_'):
            name_pattern = r'(?<![A-Za-z0-9_])' + name_pattern
        if character[-1].isascii() and (character[-1].isalnum() or character[-1]=='_'):
            name_pattern += r'(?![A-Za-z0-9_])'
        mentioned = any(re.search(name_pattern,
            segment.get('text', ''), re.IGNORECASE)
            for segment in payload['segments'] if segment['type'] == 'text')
        if group:
            quoted_ids = [s['message_id'] for s in payload['segments'] if s['type'] == 'reply']
            quoted_miko = any((self.store.get_event(key, mid) or {}).get('origin') == 'miko' for mid in quoted_ids)
            directed = mentioned or quoted_miko or any(s['type'] == 'at' and s['qq'] == account for s in payload['segments'])
            payload['directed'] = directed
            with self.store.chat.connect() as db:
                db.execute('UPDATE qq_events SET payload=? WHERE account=? AND friend=? AND message_id=?',
                    (json.dumps(payload, ensure_ascii=False), *key, mid))
            if not directed and not cfg['natural_chat']:
                return
            if directed:
                await self.invalidate(key)
            due = batch_due if directed else max(batch_due, cfg['last_response_at'] + 30)
            self.store.update_friend(key, mode='miko', pending_since=first, pending_last=now, due_at=due,
                note='正在合并群消息' if directed else '等待合适的话题')
            return
        if not mentioned and cfg['mode'] == 'owner' and not cfg['auto_reply_idle']:
            self.store.update_friend(key, pending_since=None, pending_last=None, due_at=None)
            return
        mode = 'miko' if mentioned else cfg['mode']
        due = batch_due if mode == 'miko' else max(first + self.wait_seconds, batch_due)
        self.store.update_friend(key, mode=mode, pending_since=first, pending_last=now, due_at=due,
            note='等待你回复' if mode == 'owner' else '正在合并连续消息')

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
                worker = Worker(self.versions.get(key, 0), self.store.new_run(key, self.clock()), previous_mode=friend['mode'])
                self.store.update_friend(key, due_at=None, mode='miko', pending_since=None, pending_last=None)
                if friend['chat_type'] == 2:
                    self.store.update_friend(key, last_response_at=self.clock())
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

    async def context(self, key):
        records = self.store.context(key)
        for record in records:
            for segment in record['segments']:
                if segment['type'] != 'reply':
                    continue
                quoted = self.store.get_event(key, segment['message_id'])
                if quoted:
                    record['quoted_message'] = {'message_id': quoted['message_id'], 'text': quoted['text'],
                        'author': '好友' if quoted['origin'] == 'friend' else 'Miko' if quoted['origin'] == 'miko' else '橙汁',
                        'recalled': bool(quoted['recalled'])}
                    if self.store.friend(key)['chat_type'] == 2:
                        info = json.loads(quoted['payload'])
                        record['quoted_message']['author'] = info.get('sender_name') or record['quoted_message']['author']
                elif not record.get('quoted_message') or record['quoted_message'].get('content_status') == 'unavailable':
                    try:
                        value = await self.client.message(*key, segment['message_id'])
                        record['quoted_message'] = {'message_id': segment['message_id'],
                            'text': parse_message(value.get('message'))['text'],
                            'author': '好友' if str(value['sender']['user_id']) == key[1] else '橙汁'}
                        if self.store.friend(key)['chat_type'] == 2:
                            record['quoted_message']['author'] = value['sender'].get('card') or value['sender'].get('nickname') or str(value['sender']['user_id'])
                    except (SendError, AttributeError):
                        record['quoted_message'] = {'message_id': segment['message_id'], 'content_status': 'unavailable'}
                self.store.quote_snapshot(key, record['message_id'], record['quoted_message'])
        return records

    async def pause_between(self, key, worker, previous):
        until = asyncio.get_running_loop().time() + bubble_delay(previous)
        while asyncio.get_running_loop().time() < until:
            self.check(key, worker)
            await asyncio.sleep(min(.1, max(0, until - asyncio.get_running_loop().time())))

    def bubble_text(self, bubble, candidates):
        if bubble['kind'] == 'text':
            return bubble['text']
        if bubble['kind'] == 'face':
            return '[' + next(f['name'] for f in FACES if f['face_id'] == bubble['face_id']) + ']'
        return '[表情包：' + next(s['description'] for s in candidates if s['sticker_id'] == bubble['sticker_id']) + ']'

    async def segments(self, key, worker, bubble):
        self.check(key, worker)
        if bubble['kind'] == 'text':
            segments = [{'type': 'text', 'data': {'text': bubble['text']}}]
        elif bubble['kind'] == 'face':
            segments = [{'type': 'face', 'data': {'id': bubble['face_id']}}]
        else:
            segments = [self.stickers.segment(key[0], bubble['sticker_id'])]
        if 'quote_message_id' in bubble:
            event = self.store.get_event(key, bubble['quote_message_id'])
            if not event or event['recalled'] or event['origin'] in ('pending', 'unknown'):
                raise StaleReply('引用原消息已失效，未发送')
            await self.client.quote(*key, event)
            segments.insert(0, {'type': 'reply', 'data': {'id': str(event['message_id'])}})
        if self.store.friend(key)['chat_type'] == 2 and worker.position == 0 and bubble['kind'] == 'text':
            pending = pending_contacts(self.store.context(key))
            target = pending[-1] if pending and pending[-1].get('directed') else None
            if target:
                segments.insert(0, {'type': 'at', 'data': {'qq': target['author']['id'].removeprefix('qq:'),
                    'name': target['author']['name'], 'uid': target.get('sender_uid', '')}})
        self.check(key, worker)
        return segments

    async def reply(self, key, worker):
        uncertain = False
        try:
            self.check(key, worker)
            cfg = self.store.friend(key)
            records = await self.context(key)
            await attach_images(self.client, key, records)
            self.check(key, worker)
            candidates = self.stickers.candidates(key[0], '\n'.join(r['text'] for r in records[-8:]))
            if isinstance(self.client, NativeQq) and not self.client.capabilities.get('send_images', True):
                candidates = []
            for attempt in range(4):
                self.check(key, worker)
                worker.turn = self.chat.begin_qq(cfg['conversation_id'], records, candidates)
                await worker.turn.task
                self.check(key, worker)
                if worker.turn.status != 'completed':
                    raise ValueError(worker.turn.error or '本轮未生成可发送的回复')
                try:
                    bubbles = parse_bubbles(worker.turn.text.strip(), records, candidates, self.max_reply_chars, group=cfg['chat_type'] == 2)
                    break
                except ValueError:
                    if attempt == 3:
                        raise
            warning = getattr(worker.turn, 'warning', None)
            if not bubbles:
                self.store.update_run(worker.run_id, state='skipped', error=warning)
                if self.versions.get(key, 0) == worker.version and not (cfg['chat_type'] == 2 and self.store.friend(key)['pending_since'] is not None):
                    self.store.update_friend(key, mode=worker.previous_mode, note=warning or '本轮无需回复，等待新消息',
                        pending_since=None, pending_last=None, due_at=None)
                return
            texts = [self.bubble_text(b, candidates) for b in bubbles]
            self.store.plan(worker.run_id, bubbles, texts)
            self.store.update_run(worker.run_id, state='sending')
            for i, bubble in enumerate(bubbles):
                worker.position, worker.phase = i, 'between_bubbles'
                if i:
                    await self.pause_between(key, worker, bubbles[i-1])
                self.check(key, worker)
                self.store.update_bubble(worker.run_id, i, state='preparing')
                segments = await self.segments(key, worker, bubble)
                if i == 0 and self.avatar:
                    await self.avatar.acquire(worker.run_id)
                    self.check(key, worker)
                payload = parse_message(segments)
                if bubble['kind'] == 'sticker':
                    payload['segments'][-1]['sticker_id'] = bubble['sticker_id']
                    payload['text'] = texts[i]

                def guard():
                    self.check(key, worker)
                    self.store.update_bubble(worker.run_id, i, state='sending',
                                             segments=json.dumps(payload['segments'], ensure_ascii=False))
                    worker.phase = 'sending'

                receipt = await self.client.send(*key, segments, guard)
                # Each receipt is durable before echo classification or dispatch of the next bubble.
                self.store.update_bubble(worker.run_id, i, state='sent', receipt_id=receipt['message_id'],
                                         timestamp=receipt['timestamp'])
                self.store.confirm_event(key, receipt['message_id'], texts[i], receipt['timestamp'], payload)
                self.store.record(key, receipt['message_id'], 'miko')
                worker.phase = 'between_bubbles'
                await self.settle_echoes(key)
                if bubble['kind'] == 'sticker':
                    self.stickers.used(key[0], bubble['sticker_id'])
            self.store.update_run(worker.run_id, state='sent', receipt_id=receipt['message_id'], error=warning)
            if self.versions.get(key, 0) == worker.version and not (cfg['chat_type'] == 2 and self.store.friend(key)['pending_since'] is not None):
                self.store.update_friend(key, mode='miko', note=warning or 'Miko 正在接话',
                    pending_since=None, pending_last=None, due_at=None)
            await self.settle_echoes(key)
        except StaleReply as exc:
            self.fail_run(key, worker, 'stale', str(exc))
        except SendUncertain as exc:
            uncertain = True
            self.fail_run(key, worker, 'unknown', str(exc))
        except Exception as exc:
            if isinstance(exc, sqlite3.Error):
                self.storage_faults[key] = '回复记录保存失败，请检查本机存储并到 QQ 核对后恢复'
                logging.getLogger(__name__).exception('Unable to save QQ reply outcome')
            uncertain = worker.phase == 'sending'
            self.fail_run(key, worker, 'unknown' if uncertain else 'error',
                '发送结果未知，请到 QQ 核对；不会重发' if uncertain else
                str(exc) if isinstance(exc, (ValueError, SendError)) else '本轮生成未完成')
        finally:
            try:
                self.store.cancel_tail(worker.run_id)
                await self.settle_echoes(key, uncertain=uncertain)
            except sqlite3.Error:
                self.storage_faults[key] = '消息保存失败，请检查本机存储并到 QQ 核对后恢复'
                logging.getLogger(__name__).exception('Unable to save QQ message origin')
            finally:
                try:
                    if self.avatar:
                        await self.avatar.release(worker.run_id)
                finally:
                    self.workers.pop(key, None)

    def fail_run(self, key, worker, state, error):
        try:
            if worker.position is not None:
                with self.store.chat.connect() as db:
                    db.execute("""UPDATE qq_bubbles SET state=?,error=? WHERE run_id=? AND position=?
                        AND state IN ('pending','preparing','sending')""",
                        ('unknown' if state == 'unknown' else 'cancelled' if state == 'stale' else 'error',
                         error, worker.run_id, worker.position))
            self.store.update_run(worker.run_id, state=state, error=error)
            if state == 'unknown':
                self.store.update_friend(key, blocked=1, note=error, due_at=None,
                    pending_since=None, pending_last=None)
            elif state == 'error' and self.versions.get(key, 0) == worker.version:
                self.store.update_friend(key, mode=worker.previous_mode, note=error,
                    pending_since=None, pending_last=None, due_at=None)
        except sqlite3.Error:
            self.storage_faults[key] = '回复记录保存失败，请检查本机存储并到 QQ 核对后恢复'
            logging.getLogger(__name__).exception('Unable to save QQ reply outcome')
