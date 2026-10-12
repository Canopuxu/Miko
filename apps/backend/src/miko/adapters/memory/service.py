"""Thin integration of Tulpa social memory and memU's embedding-only service."""
import asyncio
import json
import logging

from memu.app.service import MemoryService
from pydantic import BaseModel

from miko.adapters.memory.tulpa import SocialMemory


class Scope(BaseModel):
    subject: str = ''
    peer: str = ''
    entry_id: str = ''


class Memory:
    def __init__(self, chat):
        self.chat = chat
        local = chat.settings.local_root
        self.social = SocialMemory(local / 'data/database/social-memory.sqlite3', chat)
        self.index = None
        self.error = None
        self.lock = asyncio.Lock()
        self.task = None
        path = local / 'config/embedding.local.json'
        self.model = None
        if path.exists() and chat.provider:
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            self.model = config['model']
            self.index = MemoryService(
                database_config={'metadata_store': {'provider': 'sqlite',
                    'dsn': 'sqlite:///' + (local / 'data/database/memu.sqlite3').as_posix()}},
                embedding_profiles={'default': {'provider': 'openai', 'client_backend': 'httpx',
                    'base_url': chat.provider.api_base, 'api_key': chat.provider.api_key.get_secret_value(),
                    'embed_model': self.model}},
                progressive_retrieve_config={'file': {'top_k': 12, 'tracks': ['memory']}, 'resource': {'enabled': False}},
                user_config={'model': Scope},
            )

    def status(self):
        return {'configured': self.index is not None, 'embedding_model': self.model,
                'error': self.error, 'automatic': self.social.control(), 'batch_size': 100}

    def documents(self):
        result = {}
        for row in self.chat.profiles.list(active=True):
            name = 'profile:' + row['id']
            peer = f"qq:{row['account']}:{row['friend']}" if row['account'] else ''
            result[name] = {'name': name, 'track': 'memory', 'description': f"{row['subject']}|{peer}|{row['title']}",
                'content': f"# {row['title']}\n{row['content']}", 'subject': row['subject'],
                'peer': peer, 'entry_id': name}
        if self.social.control()['enabled']:
            for row in self.social.listing()['scopes']:
                detail = self.social.detail(row['id'])
                automatic = detail['automatic'] if detail['valid'] else ''
                if not automatic and not detail['manual']:
                    continue
                subject, peer = row['conversation_id'].split('|', 1)
                if peer == 'web:owner':
                    peer = ''
                name = 'social:' + str(row['id'])
                result[name] = {'name': name, 'track': 'memory', 'description': f"{subject}|{peer}|{row['name']}",
                    'content': f"# 关系与经历\n{automatic}\n# 用户修正\n{detail['manual']}",
                    'subject': subject, 'peer': peer, 'entry_id': name}
        return result

    async def sync(self):
        await asyncio.to_thread(self.social.store.sync)
        if not self.index:
            return
        async with self.lock:
            try:
                desired = self.documents()
                files = self.index.database.recall_file_repo.list_recall_files()
                existing = {r.name: r for r in files.values()}
                for name, row in existing.items():
                    new = desired.get(name)
                    if new is None or row.description != new['description']:
                        scope = {'entry_id': name}
                        self.index.database.recall_file_segment_repo.clear_segments(scope)
                        self.index.database.recall_file_repo.clear_recall_files(scope)
                for doc in desired.values():
                    old = existing.get(doc['name'])
                    if old and old.content == doc['content'] and old.description == doc['description']:
                        continue
                    await self.index.commit_results(recall_files=[{k: doc[k] for k in ('name','track','description','content')}],
                        user={k: doc[k] for k in ('subject','peer','entry_id')})
                self.error = None
            except Exception as exc:
                self.error = '记忆索引更新失败（' + type(exc).__name__ + '），本次使用原有关键词检索'
                logging.getLogger(__name__).warning('Memory index sync failed: %s', type(exc).__name__)

    async def context(self, query, key=None):
        await self.sync()
        context = self.chat.profiles.context(query, key)
        context['social_memory'] = self.social.context(query, key)
        context['retrieval'] = {'engine': 'Tulpa FTS', 'error': self.error}
        if not self.index or self.error or not query.strip():
            return context
        peers = ['', f'qq:{key[0]}:{key[1]}'] if key else ['']
        async with self.lock:
            try:
                hits = await self.index.progressive_retrieve(query, where={'peer__in': peers})
                active = {r['id']: r for r in self.chat.profiles.list(key=key, active=True)}
                used = {r['id'] for r in context['role_material']}
                remaining = 20000 - sum(len(r['content']) for r in context['role_material'])
                for hit in hits['files']:
                    name = hit['name']
                    if name.startswith('profile:') and name[8:] in active and name[8:] not in used:
                        row = active[name[8:]]
                        if len(row['content']) <= remaining:
                            context['role_material'].append(row)
                            remaining -= len(row['content'])
                            used.add(name[8:])
                    elif name.startswith('social:'):
                        detail = self.social.detail(int(name[7:]))
                        subject, peer = detail['conversation_id'].split('|', 1)
                        if not any(r['subject'] == subject and r['peer'] == peer for r in context['social_memory']):
                            context['social_memory'].append({'subject': subject, 'peer': peer,
                                'automatic': detail['automatic'] if detail['valid'] else '',
                                'manual_corrections': detail['manual'], 'sources': self.social.sources(detail['id'])})
                context['retrieval'] = {'engine': 'memU Embedding + Tulpa FTS', 'model': self.model,
                                        'hits': [{k: h[k] for k in ('name','score')} for h in hits['files']]}
            except Exception as exc:
                self.error = '语义检索失败（' + type(exc).__name__ + '），本次使用原有关键词检索'
                context['retrieval']['error'] = self.error
        return context

    def start(self):
        self.social.start()
        self.task = asyncio.create_task(self.background())

    async def background(self):
        while True:
            await self.sync()
            await asyncio.sleep(15)

    async def close(self):
        self.social.worker.stop()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.index:
            self.index.database.close()
