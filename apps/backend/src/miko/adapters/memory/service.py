"""Thin integration of Tulpa social memory and memU's embedding-only service."""
import asyncio
import json
import logging
import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from memu.app.service import MemoryService
from pydantic import BaseModel

from miko.adapters.memory.tulpa import SocialMemory
from miko.adapters.memory.structured import StructuredMemory
from miko.adapters.memory.expansion import SearchExpansion
from miko.settings import ProviderConfig
from miko.adapters.memory.retrieval import LocalRecall, semantic_text, query_words, category_words
from miko.domain.conversation.context_selection import (retrieval_query, scoring_query, topic_scopes, select, selected_context)


class Scope(BaseModel):
    subject: str = ''
    peer: str = ''
    entry_id: str = ''


class Memory:
    def __init__(self, chat):
        self.chat = chat
        local = chat.settings.local_root
        self.social = SocialMemory(local / 'data/database/social-memory.sqlite3', chat)
        self.structured = StructuredMemory(self.social.store)
        self.local = LocalRecall(local / 'data/database/retrieval.sqlite3')
        self.social.structured = self.structured
        if self.social.engine == 'memobase':
            from miko.adapters.memory.memobase import MemobasePipeline
            self.social.pipeline = MemobasePipeline(self.social, chat)
        self.index = None
        self.error = None
        self.lock = asyncio.Lock()
        self.task = None
        self.category_members = {}
        self.category_labels = {}
        self.expansion=SearchExpansion(self)
        self.configure_index()

    def configure_index(self):
        chat = self.chat
        local = chat.settings.local_root
        if self.index:
            self.index.database.close()
        self.index = None
        self.error = None
        path = local / 'config/embedding.local.json'
        self.model = None
        self.reranker = chat.settings.load_rerank()
        self.expansion.retry()
        if path.exists():
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            self.model = config['model']
            if not self.model:
                return
            provider = ProviderConfig(api_base=config['api_base'], api_key=config['api_key'], model=self.model) if config.get('api_base') else chat.provider
            if not provider:
                return
            identity=hashlib.sha256((provider.api_base.rstrip('/')+'\n'+self.model).encode()).hexdigest()[:12]
            index_file=Path(config.get('index_file','memu.sqlite3'))
            index_file=index_file.with_name(index_file.stem+'-'+identity+index_file.suffix)
            self.index = MemoryService(
                database_config={'metadata_store': {'provider': 'sqlite',
                    'dsn': 'sqlite:///' + (local / 'data/database' / index_file).as_posix()}},
                embedding_profiles={'default': {'provider': 'openai', 'client_backend': 'httpx',
                    'base_url': provider.api_base, 'api_key': provider.api_key.get_secret_value(),
                    'embed_model': self.model}},
                progressive_retrieve_config={'file': {'top_k': 40, 'tracks': ['memory']}, 'resource': {'enabled': False}},
                user_config={'model': Scope},
            )

    def status(self):
        return {'configured': self.index is not None, 'embedding_model': self.model,
                'error': self.error, 'automatic': self.social.control(), 'batch_size': 100,
                'memory_engine': self.social.engine,
                'memory_model': (self.social.pipeline.model or self.chat.provider.model) if self.social.pipeline and self.chat.provider else None,
                'search_expansion':self.expansion.status(),
                'idle_flush_seconds': 60}

    def documents(self):
        result = {}
        if self.chat.life:
            for goal in self.chat.life.store.goals.values():
                name = 'life-goal:' + goal['id']
                result[name] = {'name': name, 'track': 'memory', 'description': goal['text'],
                    'content': json.dumps({k: v for k, v in goal.items() if k != 'id'}, ensure_ascii=False),
                    'subject': 'miko', 'peer': '', 'entry_id': name,
                    'row': {'group':'life_goals','source':dict(goal)}}
            for day, row in self.chat.life.store.all().items():
                if day > date.today().isoformat() or not row.entries:
                    continue
                for position, entry in enumerate(row.entries):
                    name = f'life:{day}:{position}'
                    result[name] = {'name': name, 'track': 'memory', 'description': entry['text'],
                        'content': entry['text'], 'subject': 'miko', 'peer': '', 'entry_id': name,
                        'row': {'group':'life_entries','source':dict(entry,day=day,_retrieval_id=name)}}
        for row in self.chat.profiles.list(active=True,limit=None):
            name = 'profile:' + row['id']
            peer = f"qq:{row['account']}:{row['friend']}" if row['account'] else ''
            category = row['category'].replace('/', ' · ')
            result[name] = {'name': name, 'track': 'memory', 'description': f"{category}：{row['content']}",
                'content': f"{category}：{row['content']}", 'subject': row['subject'],
                'peer': peer, 'entry_id': name, 'row': {'group':'role_material','source':row}}
        if self.social.control()['enabled']:
            for row in self.structured.items(active=True,limit=None):
                name = 'item:' + row['id']
                result[name] = {'name': name, 'track': 'memory',
                    'description': f"{row['category']} · {row['topic']}：{row['content']}",
                    'content': f"# {row['category']} · {row['topic']}\n{row['content']}",
                    'subject': row['subject'], 'peer': row['peer'], 'entry_id': name,
                    'row': {'group':'people_memory','source':{k:v for k,v in row.items() if k not in ('sources','history')}}}
            for row in self.structured.days(limit=None):
                name = row['id']
                result[name] = {'name': name, 'track': 'memory',
                    'description': row['text'],
                    'content': row['text'], 'subject': row['subject'], 'peer': row['peer'], 'entry_id': name,
                    'row': {'group':'dated_experiences','source':{k:v for k,v in row.items() if k!='sources'}}}
            for row in self.social.listing()['scopes']:
                detail = self.social.detail(row['id'])
                automatic = detail['automatic'] if detail['valid'] and self.social.engine!='memobase' else ''
                if not automatic and not detail['manual']:
                    continue
                subject, peer = row['conversation_id'].split('|', 1)
                if peer == 'web:owner':
                    peer = ''
                name = 'social:' + str(row['id'])
                result[name] = {'name': name, 'track': 'memory', 'description': f"{subject}|{peer}|{row['name']}",
                    'content': f"# 关系与经历\n{automatic}\n# 用户修正\n{detail['manual']}",
                    'subject': subject, 'peer': peer, 'entry_id': name,
                    'row': {'group':'social_memory','source':{'id':detail['id'],'subject':subject,'peer':peer,
                        'automatic':automatic,'manual_corrections':detail['manual']}}}
        names = self.chat.store.identity()
        for doc in result.values():
            doc['row'] = names.material(doc['row'], canonical=True)
            doc['description'] = semantic_text(doc['row'])
            doc['content'] = doc['description']
            source=doc['row']['source']
            day=source.get('day',source.get('as_of'))
            doc['timestamp']=source.get('updated_at') or (int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000) if day else 0)
        return result

    def embedding_documents(self, documents):
        """Recall a category as a whole, then rank its actual entries individually."""
        result = dict(documents)
        groups = {}
        for name, doc in documents.items():
            row = doc['row']; source = row['source']
            if row['group'] not in ('role_material', 'people_memory') or not source.get('category'):
                continue
            category = '/'.join(filter(None, [source['category'], source.get('topic') if row['group']=='people_memory' else '']))
            scope = (doc['subject'], doc['peer'], category, source.get('as_of') or '')
            groups.setdefault(scope, []).append((name, doc))
        for scope, entries in groups.items():
            name = 'category:' + hashlib.sha256(json.dumps(scope,ensure_ascii=False).encode()).hexdigest()[:20]
            # A category represents its topic, not the unrelated values a broad category
            # may contain. Members remain individually indexed and individually scored.
            text = scope[2].split('/')[-1]+'；'+category_words(scope[2])
            result[name] = {'name':name, 'track':'memory', 'description':text, 'content':text,
                'subject':scope[0], 'peer':scope[1], 'entry_id':name,
                'category':scope[2],
                'members':[key for key, _ in entries]}
        return result

    async def sync(self):
        await asyncio.to_thread(self.social.store.sync)
        async with self.lock:
            desired = self.documents()
            self.local.sync(desired,self.expansion.version)
            if self.local.expansion_status()['pending']:self.expansion.wake.set()
            if not self.index:
                return
            try:
                desired = self.embedding_documents(desired)
                self.category_members = {name:doc['members'] for name,doc in desired.items() if 'members' in doc}
                self.category_labels = {name:doc['category'] for name,doc in desired.items() if 'members' in doc}
                files = self.index.database.recall_file_repo.list_recall_files()
                existing = {r.name: r for r in files.values()}
                for name, row in existing.items():
                    new = desired.get(name)
                    if new is None or row.description != new['description'] or row.content != new['content']:
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

    async def feedback(self, query, peer):
        from miko.adapters.memory.feedback import select_feedback
        return await select_feedback(self.reranker, query, self.structured.feedback_candidates(peer))

    async def context(self, query, key=None, conversation_id=None, *, previous=(), participants=(), speaker=None, raw=False):
        await self.sync()
        query = self.chat.canonical_query(query)
        previous = [self.chat.canonical_query(text) for text in previous]
        peer = f'qq:{key[0]}:{key[1]}' if key else ''
        peers = ['',peer] if key else ['']
        context = {'current_time':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='minutes'),
            'friend_relationships':self.chat.profiles.relationships(query,key),
            '_previous_contact_texts':previous, '_contact_names':participants if isinstance(participants,dict) else {},
            '_group_speaker':speaker, 'retrieval':{'engine':'Tulpa FTS','error':self.error}}
        if key is None: context['_self_subject']='owner'
        group=key and key[1].startswith('group-')
        if group: context['friend_relationships']=[]
        material=self.local.material(peers)
        for name,row in material.items():
            source=row['source']
            # Global role settings/life are shared; private conversation memories stay private.
            if group and source.get('peer','')=='' and row['group'] not in ('role_material','life_entries','life_goals'):
                continue
            context.setdefault(row['group'],[]).append(source)
        context['owner_feedback']=await self.feedback(retrieval_query(query,previous),conversation_id or peer or 'web:owner')
        if self.index and not self.error and query_words(retrieval_query(query,previous)):
            from miko.domain.conversation.context_selection import candidates, scoped_row, time_matches, source_key
            _,rows=candidates(context)
            try:
                topics,scopes=topic_scopes(query,context)
                all_hits={}; by_topic={}; category_topics={}; by_category={}
                async with self.lock:
                    for topic,subjects in zip(topics,scopes):
                        # Person/conversation/time boundaries are shared. Category hints guide
                        # local matching, but must not fence semantic recall into a guessed topic.
                        allowed=[source_key(row) for row in rows if scoped_row(row,subjects) and time_matches(row,topic,context)]
                        if not allowed:continue
                        category_ids=[name for name,members in self.category_members.items() if set(members)&set(allowed)]
                        hits=await self.index.progressive_retrieve(scoring_query(retrieval_query(topic,previous)),where={'entry_id__in':[*allowed,*category_ids]})
                        category_hits=sorted((h for h in hits['files'] if h['name'] in self.category_labels),key=lambda h:h['score'],reverse=True)
                        if category_hits and category_hits[0]['score'] >= .45:
                            category_topics[topic]=self.category_labels[category_hits[0]['name']].split('/')[-1]
                        by_category[topic]=[{'category':self.category_labels[h['name']], 'score':h['score'],
                            'members':[name for name in self.category_members[h['name']] if name in allowed]}
                            for h in category_hits]
                        topic_hits={}
                        for hit in hits['files']:
                            name=hit['name']
                            if name not in allowed:continue
                            topic_hits[name]={'name':name,'score':hit['score']}
                            if name not in all_hits or hit['score']>all_hits[name]['score']:
                                all_hits[name]={'name':name,'score':hit['score']}
                        by_topic[topic]=sorted(topic_hits.values(),key=lambda h:h['score'],reverse=True)
                context['retrieval']={'engine':'memU Embedding + Tulpa FTS','model':self.model,
                    'by_topic':by_topic,
                    'category_hits':by_category,
                    'topic_categories':category_topics,
                    'hits':[{'name':h['name'],'score':h['score']} for h in sorted(all_hits.values(),key=lambda h:h['score'],reverse=True)]}
            except Exception as exc:
                self.error='语义检索失败（'+type(exc).__name__+'），本次使用本地检索'
                context['retrieval']['error']=self.error
        if raw:return context
        _,chosen,_=await select(self.reranker,query,context,local=self.local)
        return self.chat.store.identity().material(selected_context(context,chosen))

    def start(self):
        self.social.start()
        self.task = asyncio.create_task(self.background())
        self.expansion.start()

    async def background(self):
        while True:
            await self.sync()
            await asyncio.sleep(15)

    async def close(self):
        await self.expansion.close()
        self.social.stop()
        if self.social.worker.thread:
            await asyncio.to_thread(self.social.worker.thread.join,5)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.index:
            self.index.database.close()
