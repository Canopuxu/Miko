"""Embed Memobase's reviewed extraction and merge modules in Miko."""
from collections import defaultdict
from datetime import datetime
import json
import re

from miko.settings import REPO_ROOT
from .memobase_upstream.prompts import (
    zh_summary_entry_chats as summary,
    zh_extract_profile as extract,
    zh_merge_profile_yolo as merge,
    zh_user_profile_topics as topics,
    organize_profile as organize,
    summary_profile as abstract,
    utils as parser,
)
from .memobase_upstream.processors import merge_by_topic_sub_topics


class MemobasePipeline:
    def __init__(self, social, chat):
        self.social, self.chat = social, chat
        path = chat.settings.local_root / 'config/memory.local.json'
        self.model = json.loads(path.read_text(encoding='utf-8-sig')).get('model') if path.exists() else None
        self.guidance = (REPO_ROOT / 'config/defaults/memory-guidance.txt').read_text(encoding='utf-8').strip()

    async def summarize(self, sid, payload, model=None):
        usage, calls = {}, []

        async def ask(label, system, user):
            value = await self.chat.complete_memory(system, user, model or self.model)
            calls.append(label)
            for key, amount in value.get('usage', {}).items():
                if isinstance(amount, int):
                    usage[key] = usage.get(key, 0) + amount
            return value['text']

        ids = [r['id'] for r in payload['stage']]
        with self.social.store.connect() as db:
            rows = [dict(db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()) for mid in ids]
        _, cid = self.social.detail(sid)['conversation_id'].split('|', 1)
        peer = '' if cid == 'web:owner' else cid
        subjects = list(dict.fromkeys(r['sender_id'] for r in rows))
        with self.chat.store.connect() as db:
            names = {r['id']: r['display_name'] for r in db.execute('SELECT * FROM people')}
        plans, days, summaries = [], [], []
        for subject in subjects:
            existing = self.social.structured.items(subject=subject)
            relevant = [r for r in existing if r['peer'] in ('', peer) and r['enabled'] and (r['valid'] or r['manual'])]
            old = {(r['category'], r['topic']): r for r in relevant}
            previous = '\n'.join(f'- {r["category"]}::{r["topic"]}::{r["content"]}' for r in relevant) or 'NONE'
            dialogue = '\n'.join(
                f'[{datetime.fromtimestamp(r["timestamp"]/1000).astimezone().isoformat()}] '
                f'{names.get(r["sender_id"], r["sender_id"])}({"user" if r["sender_id"] == subject else {"owner":"主人","miko":"Miko","contact":"好友"}[r["author"]]}): {r["content"]}'
                for r in rows)
            dialogue = '本次整理对象：' + names.get(subject,subject) + '\n参与者身份：' + json.dumps(
                {names.get(r['sender_id'],r['sender_id']): {'owner':'主人','miko':'Miko','contact':'好友'}[r['author']] for r in rows},
                ensure_ascii=False) + '\n' + dialogue
            topic_prompt = topics.get_prompt()
            recorded = await ask(f'{subject}:summary', summary.get_prompt(topic_prompt, '', self.guidance),
                                 summary.pack_input(previous, dialogue))
            extracted = await ask(f'{subject}:extract', extract.get_prompt(topic_prompt),
                                  extract.pack_input(previous, recorded))
            facts = merge_by_topic_sub_topics(parser.parse_string_into_profiles(extracted).model_dump()['facts'])
            changes = []
            for fact in facts:
                key = (fact['topic'], fact['sub_topic'])
                current = old.get(key)
                changes.append({'memo_id': len(changes)+1, 'new_info': fact['memo'],
                    'current_memo': current['content'] if current else '', 'topic': key[0], 'subtopic': key[1],
                    'topic_description': None, 'update_instruction': None})
            # A new broader sub-topic can explicitly discuss an existing named
            # item (e.g. 乐器 -> 吉他). Reuse the upstream merge judgment for that
            # item too, rather than leaving its old current-state wording behind.
            present={(c['topic'],c['subtopic']) for c in changes}
            for fact in facts:
                for key,current in old.items():
                    if key not in present and key[0]==fact['topic'] and len(key[1])>=2 and key[1] in fact['memo']:
                        changes.append({'memo_id':len(changes)+1,'new_info':fact['memo'],
                            'current_memo':current['content'],'topic':key[0],'subtopic':key[1],
                            'topic_description':None,'update_instruction':None})
                        present.add(key)
            actions = {}
            # Reuse the upstream new-entry fast path; merge existing entries together.
            if any(c['current_memo'] for c in changes):
                actions = parser.parse_string_into_merge_yolo_action(await ask(
                    f'{subject}:merge', merge.get_prompt(), merge.get_input(changes)))
                if any(c['memo_id'] not in actions for c in changes):
                    raise ValueError('合并结果缺少对应条目，保留本批待重试')
            for change in changes:
                key = (change['topic'], change['subtopic'])
                current = old.get(key)
                action = actions.get(change['memo_id'], {'action': 'APPEND', 'memo': 'APPEND'})
                if action['action'] == 'ABORT':
                    continue
                content = action['memo'] if action['action'] == 'UPDATE' else (
                    (current['automatic'] + ';' if current else '') + change['new_info'])
                # Personal facts are shared; relationship knowledge belongs to this conversation.
                item_peer = peer if subject not in ('owner', 'miko') or change['topic'] == '人物关系' else ''
                exact = next((r for r in existing if (r['peer'],r['category'],r['topic']) == (item_peer,*key)), None)
                plans.append({'subject': subject, 'peer': item_peer, 'category': key[0], 'topic': key[1],
                    'content': content, 'expected_revision': exact['revision'] if exact else 0, 'sources': ids})
            grouped = defaultdict(list)
            for line in recorded.splitlines():
                if not line.strip().startswith('- ') or not re.search(r'//\s*(event|schedule)', line):
                    continue
                date = re.search(r'提及于\s*[:：]?\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})', line)
                day = f'{date[1]}-{int(date[2]):02d}-{int(date[3]):02d}' if date else datetime.fromtimestamp(rows[-1]['timestamp']/1000).date().isoformat()
                grouped[day].append(line)
            for day, lines in grouped.items():
                days.append({'subject': subject, 'peer': peer, 'day': day, 'text': '\n'.join(lines),
                             'sources': ids, 'through': f'{max(ids)}:{day}'})
            summaries.append(recorded)
        return {'memory': '\n'.join(summaries)[:4000] or '本批未提炼出新增人物资料。',
                'evidence_ids': ids, 'structured': plans, 'days': days,
                'usage': {'model': model or self.model or self.chat.provider.model, 'calls': calls, **usage}}

    async def organize_category(self, subject, peer, category):
        """Use the original organizer on demand, returning a reviewable proposal."""
        rows = [r for r in self.social.structured.items(subject=subject, category=category, active=True) if r['peer']==peer]
        text = f'topic: {category}\n' + '\n'.join(f'- {r["topic"]}::{r["content"]}' for r in rows)
        value = await self.chat.complete_memory(organize.get_prompt(8, ''), text, self.model)
        return {'items': parser.parse_string_into_subtopics(value['text']),
                'revisions': {r['id']:r['revision'] for r in rows}, 'usage': value['usage'],
                'subject':subject,'peer':peer,'category':category}

    async def summarize_item(self, item_id):
        # This upstream prompt generalizes preferences; use it for browsing only.
        row = self.social.structured.detail(item_id)
        value = await self.chat.complete_memory(abstract.get_prompt(), row['content'], self.model)
        return {'summary': value['text'], 'entry_id': item_id, 'usage': value['usage']}
