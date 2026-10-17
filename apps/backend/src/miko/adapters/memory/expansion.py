"""Background document expansion over the existing local index and memory model."""
import asyncio
import hashlib
import json
import logging
import time

from miko.settings import REPO_ROOT, write_json
from .retrieval import fields


class SearchExpansion:
    def __init__(self, memory):
        self.memory=memory
        self.chat=memory.chat
        self.path=self.chat.settings.local_root/'config/retrieval.local.json'
        self.prompt=REPO_ROOT/'config/defaults/retrieval-expansion.txt'
        self.task=None
        self.wake=asyncio.Event()
        self.running=False
        self.error=None
        self.retry_at=0

    @property
    def enabled(self):
        return json.loads(self.path.read_text('utf-8-sig')).get('expansion_enabled',True) if self.path.exists() else True

    @property
    def version(self):
        return hashlib.sha256(self.prompt.read_bytes()).hexdigest()

    def connection(self):
        override=self.chat.settings.load_task_provider('memory')
        provider=override or self.chat.provider
        config=self.chat.settings.local_root/'config/memory.local.json'
        model=json.loads(config.read_text('utf-8-sig')).get('model') if config.exists() else None
        return provider,override.model if override else model or (provider.model if provider else None)

    def status(self):
        provider,model=self.connection()
        return {**self.memory.local.expansion_status(),'enabled':self.enabled,'configured':provider is not None,
                'running':self.running,'model':model,'error':self.error}

    def control(self, enabled):
        write_json(self.path,{'expansion_enabled':enabled})
        self.wake.set()

    def retry(self, rebuild=False):
        self.memory.local.retry_expansions(rebuild)
        self.error=None;self.retry_at=0;self.wake.set()

    async def process(self):
        provider,model=self.connection()
        if not self.enabled or not provider or time.time()<self.retry_at:
            return False
        batch=[];size=0
        for item in self.memory.local.expansion_pending():
            length=len(json.dumps(item['row'],ensure_ascii=False))
            if batch and size+length>16000:break
            batch.append(item);size+=length
        if not batch:return False
        material=[]
        with self.chat.store.connect() as db:
            names={r['id']:r['display_name'] for r in db.execute('SELECT id,display_name FROM people')}
        for i,item in enumerate(batch):
            row=item['row'];source=row['source'];body,title,category,_=fields(row)
            subjects=source.get('subjects') or [source.get('subject','miko')]
            material.append({'id':i,'subjects':{subject:names.get(subject,subject) for subject in subjects},
                'date':source.get('day',source.get('as_of','')),'status':source.get('status',''),
                'start_date':source.get('start_date',''),'target_date':source.get('target_date',''),
                'category':category,'title':title,'text':body})
        self.running=True
        try:
            result=await self.chat.complete_memory(self.prompt.read_text('utf-8'),json.dumps({'items':material},ensure_ascii=False),
                                                   model=model,json_output=True,provider=provider)
            value=json.loads(result['text']);items=value['items']
            if not isinstance(items,list):raise ValueError('items must be an array')
            parsed={}
            for item in items:
                key=str(item['id'])
                if key not in {str(i) for i in range(len(batch))} or key in parsed:raise ValueError('unknown or repeated item id')
                values=item['expressions']
                if not isinstance(values,list) or any(not isinstance(text,str) for text in values):raise ValueError('expressions must be strings')
                parsed[key]=list(dict.fromkeys(self.chat.store.identity().canonical(text.strip()) for text in values if text.strip()))[:5]
            if len(parsed)!=len(batch):raise ValueError('missing items')
            async with self.memory.lock:
                # An edit/delete made while the model was running supersedes that batch.
                self.memory.local.sync(self.memory.documents(),self.version)
                applied=self.memory.local.apply_expansions(batch,{item['source_id']:parsed[str(i)] for i,item in enumerate(batch)})
            self.error=None
            logging.getLogger(__name__).info('Local search expansion updated %s entries',applied)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error='检索表达生成未完成，可重试；当前本地检索仍可使用'
            self.memory.local.expansion_failed(batch,type(exc).__name__)
            self.retry_at=time.time()+300
            logging.getLogger(__name__).warning('Local search expansion failed: %s',type(exc).__name__)
            return False
        finally:
            self.running=False

    async def run(self):
        while True:
            self.wake.clear()
            busy=any(turn.status=='running' for turn in self.chat.turns.values())
            if not busy and await self.process():
                await asyncio.sleep(.2)
                continue
            try:await asyncio.wait_for(self.wake.wait(),15)
            except TimeoutError:pass

    def start(self):
        self.task=asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
