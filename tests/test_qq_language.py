import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import httpx
from fastapi import FastAPI

from test_qq_replies import FakeChat, FakeClient
from miko.api.qq import qq_router
from miko.domain.conversation.qq import QqService
from miko.storage.chat import ChatStore
from miko.adapters.qq.tulpa_language import mcp_expression_evidence as evidence
from miko.adapters.qq.tulpa_language.mcp_language_store import LearningError


class GroupLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chat = FakeChat(ChatStore(self.root / 'chat.sqlite3'))
        self.chat.settings = SimpleNamespace(local_root=self.root, load_task_provider=lambda task: 'memory-provider')
        self.chat.provider, self.chat.qq_provider = 'shared-provider', 'chat-provider'
        self.calls = []
        self.bad_evidence = False
        self.diverse = False
        self.model_gate = None
        self.chat.complete_memory = self.complete
        self.now = 1000
        self.qq = QqService(self.chat, None, client=FakeClient(), clock=lambda: self.now)
        self.qq.receiver.state.update(state='connected', account='123')
        self.key = ('123', 'group-900')
        self.other = ('123', 'group-901')
        for key in (self.key, self.other):
            self.qq.store.add_friend(*key, '群聊', chat_type=2)
            await self.qq.configure(key, True)
        self.language = self.qq.language

    async def asyncTearDown(self):
        await self.qq.close()
        self.tmp.cleanup()

    async def complete(self, prompt, message, **options):
        payload = json.loads(message)
        self.calls.append((prompt, payload, options))
        if self.model_gate:
            await self.model_gate.wait()
        if 'candidates' in payload:
            result = dict(ids=[r['id'] for r in payload['candidates'][:2]])
        else:
            material = payload['material']
            if 'messages' in material and 'expressions' not in material:
                forms = ([r['text'] for r in material['messages'][:3]] if self.diverse
                         else ['又来加班？', '不然你来？', '笑不活了'])
                expressions = [dict(situation=f'场景{i}', style=f'群友使用短句{form}',
                    source_id=material['messages'][i]['source_id'], evidence_quote=form,
                    surface_form=form, form_type='wording') for i, form in enumerate(forms)]
                if self.bad_evidence:
                    expressions[0]['evidence_quote'] = '原文没有的证据'
                result = dict(expressions=expressions, jargon=[dict(term='天台', source_id=material['messages'][0]['source_id'])])
            elif 'expressions' in material:
                result = dict(reviews=[dict(index=i, accept=True, reason='原话确有对应短句',
                    evidence_quote=r['evidence_quote'], checks={k: True for k in
                    ('peer_expression', 'source_supported', 'not_response_policy', 'persona_independent', 'privacy_safe')})
                    for i, r in enumerate(material['expressions'])])
            elif 'with_context' in material:
                result = dict(is_similar=False, sufficient=True, reason='本群特指食堂，不是楼顶')
            else:
                result = dict(meaning='学校食堂' if 'evidence' in material else '建筑物楼顶', insufficient=False, reason='原文提供了用法')
        return dict(text=json.dumps(result, ensure_ascii=False), usage={})

    async def event(self, mid, text, *, sender=456, key=None):
        key = key or self.key
        await self.qq.receive(dict(post_type='message_sent' if sender == 123 else 'message',
            self_id=123, message_type='group', group_id=int(key[1][6:]), user_id=sender,
            message_id=mid, time=self.now, sender=dict(nickname='群友'),
            message=[dict(type='text', data=dict(text=text))]))

    async def batch(self, n=0):
        self.now += 31
        lines = [f'又来加班？今天去天台吃午饭{n}', '不然你来？', '笑不活了'] + [f'吃饭啦{n}-{i}' for i in range(7)]
        if self.diverse:
            lines[1] += str(n)
            lines[2] += str(n)
        for i, text in enumerate(lines):
            await self.event(n * 10 + i + 1, text)
        await self.drain()

    async def drain(self):
        for _ in range(8):
            ready = self.language.store.readiness(self.language.binding(self.key), reply_pending=self.language.busy(self.key))
            if not ready['ready']:
                break
            await self.language.learn(self.key)

    async def test_default_off_and_peer_only_group_isolation(self):
        self.assertFalse(self.language.status(self.key)['enabled'])
        await self.event(1, '不学习')
        self.assertEqual(self.language.status(self.key)['buffered_messages'], 0)
        await self.language.configure(self.key, True)
        await self.event(2, '橙汁自己说的话', sender=123)
        await self.event(3, '别的群的消息', key=self.other)
        await self.event(4, '群友说的话')
        self.assertEqual(self.language.status(self.key)['buffered_messages'], 1)
        self.assertEqual(self.language.status(self.other)['buffered_messages'], 0)
        await self.language.configure(self.key, False)
        self.assertEqual(self.language.status(self.key)['buffered_messages'], 0)

    async def test_batch_learning_sources_and_jargon_retrieval(self):
        await self.language.configure(self.key, True)
        for n in range(4):
            await self.batch(n)
        data = self.language.status(self.key)
        self.assertEqual(data['usable_expressions'], 3)
        self.assertEqual(data['confirmed_jargon'], 1)
        source = data['expressions'][0]['source']
        self.assertTrue(source['message_id'].startswith('qq:123:group-900:'))
        self.assertIn(source['text'][:4], ('又来加班', '不然你来', '笑不活了'))
        records = [dict(author=dict(kind='contact'), text='天台还有饭吗')]
        reference = await self.language.reference(self.key, records, 'plan1')
        self.assertIn('学校食堂', reference)
        self.assertNotIn('本群表达参考', reference)  # Original threshold is ten distinct verified forms.
        self.assertEqual(self.calls[0][2]['provider'], 'memory-provider')
        self.assertNotIn('学校食堂', await self.language.reference(self.other, records, 'other'))
        await self.language.configure(self.key, False)
        self.assertEqual(await self.language.reference(self.key, records, 'off'), '')
        self.assertEqual(self.language.status(self.key)['expression_count'], 3)

    async def test_invalid_source_is_not_learned(self):
        self.bad_evidence = True
        await self.language.configure(self.key, True)
        await self.batch()
        self.assertEqual(self.language.status(self.key)['expression_count'], 0)
        self.assertEqual(self.language.status(self.key)['last_failure'], 'model_error')

    async def test_only_chosen_expressions_are_passed_to_chat(self):
        self.diverse = True
        await self.language.configure(self.key, True)
        for n in range(4):
            await self.batch(n)
        self.assertEqual(self.language.status(self.key)['usable_expressions'], 12)
        reference = await self.language.reference(self.key,
            [dict(author=dict(kind='contact'), text='不然你来？')], 'choose')
        self.assertEqual(reference.count('\n- '), 2)
        self.assertEqual(self.calls[-1][2]['provider'], 'chat-provider')
        self.assertNotIn('material', self.calls[-1][1])
        await self.language.configure(self.key, False)
        self.assertEqual(await self.language.reference(self.key, [], 'stop-using'), '')

    async def test_recall_cancels_unprocessed_evidence(self):
        await self.language.configure(self.key, True)
        await self.event(1, '又来加班？')
        await self.qq.receive(dict(post_type='notice', notice_type='group_recall',
            self_id=123, group_id=900, message_id=1))
        self.assertEqual(self.language.status(self.key)['buffered_messages'], 0)

    def test_review_reason_can_quote_forms_and_deny_policies(self):
        item = dict(evidence_quote='我先躺为敬')
        checks = {key: True for key in evidence.CHECKS}
        for reason in ('原话“我先躺为敬”符合这个句式', '原话支持该表达，不含机器人应对策略'):
            review = dict(index=0, accept=True, reason=reason, evidence_quote=item['evidence_quote'], checks=checks)
            evidence.check_review(review, item, LearningError)
        review['checks'] = {**checks, 'source_supported': False}
        with self.assertRaises(LearningError):
            evidence.check_review(review, item, LearningError)
        with self.assertRaises(LearningError):
            evidence.check_expression(dict(situation='群友聊天', style='机器人应该保持沉默', source_id='1',
                evidence_quote='我先躺为敬', surface_form='我先躺为敬', form_type='wording'),
                {'1': dict(source='PEER', text='我先躺为敬')}, LearningError)

    async def test_chat_priority_and_disabling_inflight_work(self):
        await self.language.configure(self.key, True)
        self.model_gate = asyncio.Event()
        self.now += 31
        for i in range(10):
            await self.event(i + 1, ['又来加班？天台', '不然你来？', '笑不活了'][i % 3])
        self.qq.store.update_friend(self.key, due_at=self.now + 2)
        await self.language.tick()
        self.assertFalse(self.language.tasks)
        self.qq.store.update_friend(self.key, due_at=None)
        await self.language.tick()
        await asyncio.sleep(0)
        self.assertIn(self.key, self.language.tasks)
        await self.language.configure(self.key, False)
        self.assertFalse(self.language.tasks)
        self.assertEqual(self.language.status(self.key)['expression_count'], 0)

    async def test_manage_api_scope_edit_disable_and_delete(self):
        await self.language.configure(self.key, True)
        await self.batch()
        item = self.language.status(self.key)['jargon'][0]
        app = FastAPI()
        app.include_router(qq_router(self.qq))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            base = '/api/qq/friends/123/group-900/language'
            response = await client.patch(base + '/jargon/' + item['id'], json=dict(meaning='学校食堂'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['jargon'][0]['manual'], 1)
            self.assertIn('学校食堂', await self.language.reference(self.key,
                [dict(author=dict(kind='contact'), text='天台')], 'manual'))
            other = await client.patch('/api/qq/friends/123/group-901/language/jargon/' + item['id'], json=dict(enabled=False))
            self.assertEqual(other.status_code, 400)
            response = await client.patch(base + '/jargon/' + item['id'], json=dict(enabled=False))
            self.assertEqual(response.json()['jargon'][0]['enabled'], 0)
            self.assertEqual(await self.language.reference(self.key,
                [dict(author=dict(kind='contact'), text='天台')], 'disabled-item'), '')
            response = await client.delete(base + '/jargon/' + item['id'])
            self.assertEqual(response.json()['jargon'], [])
