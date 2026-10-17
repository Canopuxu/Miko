"""Embed Memobase's reviewed extraction and merge modules in Miko."""
from collections import defaultdict
from datetime import datetime, date
import json
import re

from miko.settings import REPO_ROOT
from .memobase_upstream.prompts import (
    zh_merge_profile_yolo as merge,
    organize_profile as organize,
    utils as parser,
)
from .memobase_upstream.processors import merge_by_topic_sub_topics


class MemobasePipeline:
    def __init__(self, social, chat):
        self.social, self.chat = social, chat
        self.provider = chat.settings.load_task_provider('memory')
        path = chat.settings.local_root / 'config/memory.local.json'
        self.model = json.loads(path.read_text(encoding='utf-8-sig')).get('model') if path.exists() else None
        if self.provider:
            self.model = self.provider.model

    async def summarize(self,sid,payload,model=None):
        usage={};calls=[]
        async def ask(label,system,user,json_output=False):
            result=await self.chat.complete_memory(system,user,model or self.model,json_output=json_output,
                **({'provider': self.provider} if self.provider else {}))
            calls.append(label)
            for key,value in result.get('usage',{}).items():
                if isinstance(value,int):usage[key]=usage.get(key,0)+value
            return result['text']
        ids=list(dict.fromkeys(r['id'] for r in payload['stage'] + payload.get('context', [])))
        with self.social.store.connect() as db:
            rows=[dict(db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()) for mid in ids]
        rows.sort(key=lambda row: (row['timestamp'], row['id']))
        subjects=list(dict.fromkeys(r['sender_id'] for r in rows))
        with self.chat.store.connect() as db:names={r['id']:r['display_name'] for r in db.execute('SELECT * FROM people')}
        _,cid=self.social.detail(sid)['conversation_id'].split('|',1);peer='' if cid=='web:owner' else cid
        identity = self.chat.store.identity()
        subjects = list(dict.fromkeys([*subjects, *identity.mentioned(
            '\n'.join(r['content'] for r in rows), names)]))
        existing={s:identity.material(self.social.structured.items(subject=s)) for s in subjects}
        permitted_peers = (peer,) if ':group-' in peer else ('', peer)
        relevant={s:[r for r in existing[s] if r['peer'] in permitted_peers and r['enabled'] and (r['valid'] or r['manual'])] for s in subjects}
        previous='\n'.join(f'{names.get(s,s)}[{s}] · {r["category"]}::{r["topic"]}::{r["content"]}' for s in subjects for r in relevant[s]) or 'NONE'
        events=self.social.structured.days(peer=peer)[:20]
        previous_events='\n'.join(r['day']+' · '+r['text'] for r in events) or 'NONE'
        dialogue='\n'.join(f'[{datetime.fromtimestamp(r["timestamp"]/1000).astimezone().isoformat()}] {names.get(r["sender_id"],r["sender_id"])}[{r["sender_id"]}]：{r["content"]}' for r in rows)
        user='人物：'+json.dumps({s:names.get(s,s) for s in subjects},ensure_ascii=False)+'\n已有资料：\n'+previous+'\n已有经历与约定：\n'+previous_events+'\n本次对话：\n'+dialogue
        raw=await ask('conversation:extract',(REPO_ROOT / 'config/defaults/memory-extraction.txt').read_text(encoding='utf-8'),user,True)
        value=json.loads(raw)
        if not isinstance(value,dict) or not isinstance(value.get('facts'),list) or not isinstance(value.get('events'),list):raise ValueError('提炼结果需要 facts 和 events 数组')
        grouped=defaultdict(list)
        for fact in value['facts']:
            if fact['subject'] not in subjects:raise ValueError('资料人物编号不在输入中')
            grouped[fact['subject']].append({k:fact[k] for k in ('topic','sub_topic','memo')})
        plans=[];days=[]
        for subject,raw_facts in grouped.items():
            old={(r['category'],r['topic']):r for r in relevant[subject]}
            facts=merge_by_topic_sub_topics(raw_facts)
            changes=[]
            for fact in facts:
                key=(fact['topic'],fact['sub_topic']);current=old.get(key)
                changes.append({'memo_id':len(changes)+1,'new_info':fact['memo'],'current_memo':current['content'] if current else '',
                    'topic':key[0],'subtopic':key[1],'topic_description':None,'update_instruction':None})
            present={(c['topic'],c['subtopic']) for c in changes}
            for fact in facts:
                for key,current in old.items():
                    if key not in present and key[0]==fact['topic'] and len(key[1])>=2 and key[1] in fact['memo']:
                        changes.append({'memo_id':len(changes)+1,'new_info':fact['memo'],
                            'current_memo':current['content'],'topic':key[0],'subtopic':key[1],
                            'topic_description':None,'update_instruction':None})
                        present.add(key)
            actions={}
            if any(c['current_memo'] for c in changes):
                actions=parser.parse_string_into_merge_yolo_action(await ask(subject+':merge',merge.get_prompt(),merge.get_input(changes)))
                if any(c['memo_id'] not in actions for c in changes):raise ValueError('合并结果缺少条目')
            for change in changes:
                key=(change['topic'],change['subtopic']);current=old.get(key)
                action=actions.get(change['memo_id'],{'action':'APPEND','memo':'APPEND'})
                if action['action']=='ABORT':continue
                content=action['memo'] if action['action']=='UPDATE' else ((current['automatic']+';' if current else '')+change['new_info'])
                item_peer=peer if ':group-' in peer or subject not in ('owner','miko') or change['topic']=='人物关系' else ''
                exact=next((r for r in existing[subject] if (r['peer'],r['category'],r['topic'])==(item_peer,*key)),None)
                plans.append({'subject':subject,'peer':item_peer,'category':key[0],'topic':key[1],'content':content,'expected_revision':exact['revision'] if exact else 0,'sources':ids})
        for i,event in enumerate(value['events']):
            people=list(dict.fromkeys(event['subjects']))
            if not people or any(s not in subjects for s in people):raise ValueError('事件人物编号不在输入中')
            date.fromisoformat(event['date'])
            days.append({'subject':people[0] if len(people)==1 else '', 'subjects':people,'peer':peer,'day':event['date'],'text':event['text'],'sources':ids,'through':f'{max(ids)}:{i}'})
        return {'memory':raw[:4000],'evidence_ids':ids,'structured':plans,'days':days,'usage':{'model':model or self.model or self.chat.provider.model,'calls':calls,**usage}}

    async def organize_category(self, subject, peer, category):
        """Use the original organizer on demand, returning a reviewable proposal."""
        rows = [r for r in self.social.structured.items(subject=subject, category=category, active=True) if r['peer']==peer]
        text = f'topic: {category}\n' + '\n'.join(f'- {r["topic"]}::{r["content"]}' for r in rows)
        value = await self.chat.complete_memory(organize.get_prompt(8, ''), text, self.model,
            **({'provider': self.provider} if self.provider else {}))
        return {'items': parser.parse_string_into_subtopics(value['text']),
                'revisions': {r['id']:r['revision'] for r in rows}, 'usage': value['usage'],
                'subject':subject,'peer':peer,'category':category}
