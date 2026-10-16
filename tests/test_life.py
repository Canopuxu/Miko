import asyncio
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.life import life_router
from miko.api.profiles import ProfileInput
from miko.domain.conversation.chat import ChatService, reply_context
from miko.domain.life import Life, GoalInput
from miko.domain.life_material import generation_material
from miko.settings import Settings
from miko.adapters.memory.service import Memory


class SimulatedDay(date):
    current = date(2026, 10, 6)

    @classmethod
    def today(cls):
        return cls.current


class Embeddings:
    async def embed(self, texts):
        return [[1., 0.] if '摄影' in text or '相机' in text else [0., 1.] for text in texts], {}


class LifeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = Path(self.tmp.name)
        (self.local / 'config').mkdir()
        (self.local / 'config/provider.local.json').write_text(json.dumps({
            'api_base': 'http://127.0.0.1:1/v1', 'api_key': 'test', 'model': 'test'}), encoding='utf-8')
        (self.local / 'config/embedding.local.json').write_text('{"model":"test"}', encoding='utf-8')
        self.chat = ChatService(Settings(self.local, 7870))
        self.chat.life = self.life = Life(self.chat)
        self.memory = Memory(self.chat)
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()
        self.chat.profiles.save(ProfileInput(subject='miko', kind='overview', title='Miko',
                                            content='喜欢平面设计和键盘').record())
        self.chat.profiles.save(ProfileInput(subject='owner', kind='recent', title='近况',
                                            content='最近在学习前端配色', as_of='2026-10-06').record())

    async def asyncTearDown(self):
        await self.life.close()
        await self.chat.close()
        await self.memory.close()
        self.tmp.cleanup()

    async def test_week_continuity_persistence_retrieval_and_manual_api(self):
        inputs = []

        async def generate(system, message, **kwargs):
            material = json.loads(message)
            inputs.append(material)
            if len(inputs) > 1:
                self.assertIn('摄影', json.dumps(material['生活条目'], ensure_ascii=False))
            self.assertIn('前端配色', json.dumps(material['role_material'], ensure_ascii=False))
            return {'text': json.dumps({'entries': [{'text': '开始学摄影' if len(inputs) == 1 else '练构图'}]}, ensure_ascii=False)}

        self.chat.complete_memory = generate
        with patch('miko.domain.life.date', SimulatedDay):
            for day in range(6, 13):
                SimulatedDay.current = date(2026, 10, day)
                await self.life.generate()
            self.assertEqual(len(self.life.store.all()), 7)
            self.assertEqual(self.life.context('摄影')[0]['day'], '2026-10-06')
        restarted = Life(self.chat)
        self.assertEqual(restarted.records(), self.life.records())
        context = await self.memory.context('摄影')
        self.assertTrue(any(r['text']=='开始学摄影' for r in context['life_entries']))
        compact = reply_context(context)
        self.assertNotIn('revision', json.dumps(compact['life_entries']))
        self.assertIn('life:2026-10-12:0', self.memory.documents())
        # A related question with no matching keyword recalls an earlier day.
        semantic = await self.memory.context('相机')
        self.assertEqual(semantic['retrieval']['engine'], 'memU Embedding + Tulpa FTS')
        self.assertTrue(any(r['day'] == '2026-10-06' for r in semantic['life_entries']))
        app = FastAPI()
        app.include_router(life_router(self.life))
        with TestClient(app) as client:
            row = client.get('/api/life?day=2026-10-12').json()['record']
            changed = client.put('/api/life', json={'day': '2026-10-12', 'revision': row['revision'],
                'entries': [{'text': '练光影；构图暂停'}]})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertIn('光影', self.life.context('光影')[0]['text'])
            self.assertEqual(client.put('/api/life/control', json={'enabled': False}).status_code, 200)
            self.assertFalse(Life(self.chat).enabled)
            self.assertTrue(self.life.context('摄影'))
            self.assertEqual(client.get('/api/life?day=2026-10-06').json()['record']['entries'][0]['text'], '开始学摄影')

    async def test_life_retrieval_selects_activity_without_entire_day(self):
        self.life.save(date.today(), [{'text': '摄影：练构图'}, {'text': '网球：练正手'}], 0)
        self.life.save_goal(GoalInput(text='练习网球', target_date=date.today()), self.life.store.revision)
        self.assertEqual(self.life.context('录屏软件'), [])
        self.assertEqual(self.life.goals_context('录屏软件'), [])
        context = await self.memory.context('相机')
        self.assertEqual([r['text'] for r in context['life_entries']], ['摄影：练构图'])
        self.assertEqual(context['life_goals'], [])

    async def test_failed_or_superseded_generation_preserves_owner_edits(self):
        day = date.today()
        self.life.save(day, [{'text': '练和弦'}], 0)
        async def fail(*args, **kwargs):
            raise ValueError('模拟生成失败')
        self.chat.complete_memory = fail
        with self.assertRaises(ValueError):
            await self.life.generate()
        self.assertEqual(self.life.store.get(day).entries[0]['text'], '练和弦')
        async def concurrent(*args, **kwargs):
            self.life.save(day, [{'text': '号主修正：今天休息'}], 1)
            return {'text': '{"entries":[{"text":"练新曲"}]}'}
        self.chat.complete_memory = concurrent
        with self.assertRaises(ValueError):
            await self.life.generate()
        self.assertEqual(Life(self.chat).store.get(day).entries[0]['text'], '号主修正：今天休息')

    async def test_background_survives_page_close_and_pause_stops_new_days(self):
        calls = []
        async def complete(*args, **kwargs):
            calls.append(1)
            return {'text': '{"entries":[{"text":"练配色"}]}'}
        self.chat.complete_memory = complete
        from datetime import datetime, time
        from zoneinfo import ZoneInfo
        before = datetime.combine(date.today(), time(21, 59), ZoneInfo('Asia/Shanghai'))
        after = before.replace(hour=22, minute=0)
        self.assertFalse(self.life.automatic_due(before))
        self.assertTrue(self.life.automatic_due(after))
        clock = patch('miko.domain.life.datetime')
        mocked = clock.start(); self.addCleanup(clock.stop)
        mocked.now.return_value = after
        self.life.start()
        for _ in range(30):
            if self.life.store.has(date.today()):
                break
            await asyncio.sleep(.01)
        self.assertEqual(len(calls), 1)
        await self.life.close()
        self.life.toggle(False)
        self.life.start()
        await asyncio.sleep(.03)
        self.assertEqual(len(calls), 1)
        await self.life.close()
        # Existing topic-based records retain their useful information on load.
        path = self.local / 'data/life/entries.json'
        day = date.today().isoformat()
        path.write_text(json.dumps({day: {'date': day, 'revision': 1, 'source': 'manual',
            'entries': [{'topic': '摄影', 'text': '练构图'}]}}), encoding='utf-8')
        reopened = Life(self.chat)
        self.assertEqual(reopened.listing()['record']['entries'], [{'text': '摄影：练构图'}])

    async def test_goal_cycle_and_shared_semantic_memory(self):
        outputs = [
            {'new_goals': [{'ref': 'new1', 'text': '拍一组窗边的照片', 'target_date': '2026-10-08'}],
             'entries': [{'text': '练摄影，用窗边的杯子试构图', 'goal_id': 'new1'}, {'text': '泡茶，听雨'}]},
            {'goal_updates': [], 'entries': [{'text': '阴天，今天没拿相机'}]},
        ]
        inputs = []
        async def complete(system, message, **kwargs):
            inputs.append(json.loads(message))
            return {'text': json.dumps(outputs.pop(0), ensure_ascii=False)}
        self.chat.complete_memory = complete
        with patch('miko.domain.life.date', SimulatedDay), patch('miko.domain.life_material.day_rhythm',
                return_value={'白天的时间去向': '半天处理本职事务，另外处理生活琐事',
                              '空下来之后': '做一会儿自己有兴致的活动，之后休息'}):
            SimulatedDay.current = date(2026, 10, 6)
            self.life.save(SimulatedDay.today(), [{'text': '午后小憩'}], 0)
            await self.life.generate()
            goal_id = next(iter(self.life.store.goals))
            self.assertEqual(len(self.life.listing()['record']['entries']), 3)
            self.assertEqual(self.life.store.get(SimulatedDay.today()).entries[1]['goal_id'], goal_id)
            # Passing an estimate does not change the goal's state.
            SimulatedDay.current = date(2026, 10, 9)
            await self.life.generate()
            self.assertEqual(self.life.store.goals[goal_id]['status'], 'active')
            self.assertEqual(inputs[-1]['生活目标'][0]['id'], goal_id)
            outputs.append({'goal_updates': [{'id': goal_id, 'status': 'paused', 'target_date': '2026-10-12'}],
                            'entries': [{'text': '收起相机，改天再拍', 'goal_id': goal_id}]})
            await self.life.generate()
            self.assertEqual(self.life.store.goals[goal_id]['status'], 'paused')
            outputs.append({'goal_updates': [{'id': goal_id, 'status': 'completed'}],
                            'new_goals': [{'ref': 'next', 'text': '给照片做一张双颜色海报', 'target_date': '2026-10-18'}],
                            'entries': [{'text': '选好了窗边那组摄影照片', 'goal_id': goal_id},
                                        {'text': '试海报配色', 'goal_id': 'next'}]})
            SimulatedDay.current = date(2026, 10, 12)
            await self.life.generate()
        restarted = Life(self.chat)
        self.assertEqual(restarted.store.goals[goal_id]['status'], 'completed')
        self.assertEqual(restarted.store.goals[goal_id]['completed_date'], '2026-10-12')
        self.assertEqual(len(restarted.store.goals), 2)
        self.assertEqual(len(restarted.listing()['trajectory']), 7)
        context = await self.memory.context('相机')
        self.assertTrue(any(g['id'] == goal_id and g['status'] == 'completed' for g in context['life_goals']))
        self.assertTrue(any(r.get('goal_id') == goal_id for r in context['life_entries']))
        compact = reply_context(context)
        self.assertNotIn('goal_id', json.dumps(compact))
        self.assertNotIn('id', compact['life_goals'][0])
        app = FastAPI(); app.include_router(life_router(self.life))
        with TestClient(app) as client:
            body = {'text': '给照片做一张双颜色海报', 'target_date': '2026-10-20',
                    'status': 'paused', 'revision': self.life.store.revision}
            other = next(g for g in self.life.store.goals if g != goal_id)
            self.assertEqual(client.put('/api/life/goals/' + other, json=body).status_code, 200)
            self.assertEqual(client.put('/api/life/goals/' + other, json=body).status_code, 409)

    async def test_goal_generation_is_atomic_and_owner_edit_wins(self):
        self.life.save_goal(GoalInput(text='练和弦', target_date=date.today()), self.life.store.revision)
        goal_id = next(iter(self.life.store.goals))
        async def invalid(*args, **kwargs):
            return {'text': json.dumps({'goal_updates': [{'id': goal_id, 'status': 'completed'}],
                'entries': [{'text': '随手写了几个字', 'goal_id': 'missing'}]})}
        self.chat.complete_memory = invalid
        with self.assertRaises(ValueError): await self.life.generate()
        self.assertEqual(Life(self.chat).store.goals[goal_id]['status'], 'active')
        self.assertFalse(self.life.store.has(date.today()))
        async def concurrent(*args, **kwargs):
            self.life.save_goal(GoalInput(text='练和弦，先搁置', target_date=date.today(), status='paused'),
                                self.life.store.revision, goal_id)
            return {'text': json.dumps({'goal_updates': [{'id': goal_id, 'status': 'completed'}],
                                       'entries': [{'text': '练和弦', 'goal_id': goal_id}]})}
        self.chat.complete_memory = concurrent
        with self.assertRaises(ValueError): await self.life.generate()
        self.assertEqual(Life(self.chat).store.goals[goal_id]['status'], 'paused')
        self.assertFalse(self.life.store.has(date.today()))

    async def test_daily_background_does_not_turn_all_hobbies_into_a_task_list(self):
        self.chat.profiles.save(ProfileInput(subject='miko', kind='fact', category='兴趣与能力/学习与工作',
                                            title='职业', content='绘本插画师').record())
        self.chat.profiles.save(ProfileInput(subject='miko', kind='fact', category='性格与表达/性格',
                                            title='性格', content='好胜、小傲娇').record())
        self.chat.profiles.save(ProfileInput(subject='miko', kind='fact', category='兴趣与能力/乐器',
                                            title='乐器', content='贝斯').record())
        old = {'id': 'old', 'text': '完成绘本《秋天》分镜', 'status': 'completed',
               'completed_date': '2026-10-05', 'start_date': '2026-10-01'}
        drawing = {'id': 'drawing', 'text': '绘本《小树》第1页上色', 'status': 'active',
                   'start_date': '2026-10-06', 'target_date': '2026-10-30'}
        piano = {'id': 'piano', 'text': '练习电子琴《River Flows in You》', 'status': 'active',
                 'start_date': '2026-10-06', 'target_date': '2026-11-30'}
        records = [{'day': '2026-10-05', 'text': '《秋天》分镜已经交付', 'goal_id': 'old'},
                   {'day': '2026-10-05', 'text': '《小树》第1页铺好了底色', 'goal_id': 'drawing'}]
        material = generation_material(self.chat.profiles, records,
            {g['id']: g for g in (old, drawing, piano)}, date(2026, 10, 6),
            {'白天的时间去向': '处理手上工作和家务', '空下来之后': '看电影，吃饭洗澡后休息'})
        self.assertEqual([g['id'] for g in material['生活目标']], ['drawing'])
        self.assertNotIn('贝斯', json.dumps(material['role_material'], ensure_ascii=False))
        self.assertNotIn('小傲娇', json.dumps(material['role_material'], ensure_ascii=False))
        self.assertEqual(material['相关事项的最新进展'][0]['goal_id'], 'drawing')
        self.assertTrue(all('goal_id' not in r for r in material['生活条目']))
        self.assertEqual(material['已经告一段落的事项'][0]['status'], 'completed')
        self.assertNotIn('id', material['已经告一段落的事项'][0])
