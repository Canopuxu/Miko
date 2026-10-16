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
from miko.domain.life_material import conversation_background, generation_material
from miko.storage.search import terms
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
                self.assertIn('摄影', json.dumps(material['既有角色生活记录'], ensure_ascii=False))
            self.assertIn('前端配色', json.dumps(material['真实用户资料'], ensure_ascii=False))
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
        self.assertTrue(all('goal_id' not in r for r in material['既有角色生活记录']))
        self.assertEqual(material['已经告一段落的事项'][0]['status'], 'completed')
        self.assertNotIn('id', material['已经告一段落的事项'][0])

    async def test_owner_facts_are_separate_from_generated_stories_and_names_follow_settings(self):
        self.chat.store.rename_character('青柠', subject='owner')
        self.chat.store.rename_character('小禾')
        overview = self.chat.profiles.save(ProfileInput(subject='miko', kind='overview', title='关系',
                                            content='Miko；与橙汁一起生活的朋友；熟悉橙汁的日常').record())
        self.chat.profiles.save(ProfileInput(subject='owner', kind='overview', title='身份',
                                            content='研究生二年级').record())
        self.chat.profiles.save(ProfileInput(subject='owner', kind='overview', title='姓名与性别',
                                            content='名叫橙汁，男生').record())
        self.life.save(date(2026, 10, 8), [{'text': '青柠在实验室赶论文'},
            {'text': '橙汁新买了键盘'}, {'text': '继续画《秋天》的分镜'}], 0, 'generated')
        captured = {}
        async def complete(system, message, **kwargs):
            captured.update(system=self.chat.named_prompt(system), material=json.loads(message))
            return {'text': json.dumps({'entries': [{'text': '和青柠一起吃晚饭'}]}, ensure_ascii=False)}
        self.chat.complete_memory = complete
        await self.life.generate(date(2026, 10, 9))
        material = captured['material']
        self.assertNotIn('研究生', json.dumps(material, ensure_ascii=False))
        self.assertNotIn('实验室', json.dumps(material, ensure_ascii=False))
        self.assertNotIn('键盘', json.dumps(material['既有角色生活记录'], ensure_ascii=False))
        self.assertIn('《秋天》', json.dumps(material['既有角色生活记录'], ensure_ascii=False))
        self.assertIn('前端配色', json.dumps(material['真实用户资料'], ensure_ascii=False))
        self.assertIn('男生', json.dumps(material['真实用户资料'], ensure_ascii=False))
        self.assertIn('青柠', json.dumps(material['真实用户资料'], ensure_ascii=False))
        self.assertIn('青柠', captured['system'])
        self.assertIn('小禾', captured['system'])
        self.assertNotIn('橙汁', captured['system'])
        self.assertEqual(self.chat.profiles.get(overview['id'])['content'], overview['content'])
        self.assertEqual(self.life.store.get(date(2026, 10, 9)).entries[0]['text'], '和青柠一起吃晚饭')

    async def test_conversation_background_is_small_dated_and_specific_to_miko(self):
        def event(day, text, subjects=('owner', 'miko'), updated=1):
            return {'day': day, 'text': text, 'subjects': list(subjects), 'updated_at': updated}
        rows = [event('2026-10-09', '橙汁与 Miko 约好今晚玩《双人成行》', updated=3),
                event('2026-10-09', '橙汁与 Miko 约好饭后散步', updated=2),
                event('2026-10-09', '较早的另一项安排'),
                event('2026-10-08', '一起玩《双人成行》，改天继续'),
                event('2026-10-07', '一起去税务局递交材料'),
                event('2026-10-01', '一起玩《双人成行》'),
                event('2026-10-10', '约好明天再玩《双人成行》'),
                event('2026-10-20', '约好远期一起玩游戏'),
                event('2026-10-09', '橙汁独自在实验室买键盘', subjects=('owner',))]
        result = conversation_background(self.chat.profiles, rows, date(2026, 10, 9), terms('在家打游戏、看视频'))
        self.assertEqual(len(result['当天']), 2)
        self.assertEqual(len(result['近期对话']), 1)
        self.assertNotIn('临近', result)
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('税务局', text)
        self.assertNotIn('实验室', text)
        self.assertNotIn('2026-10-01', text)
        self.assertNotIn('2026-10-20', text)
        near = conversation_background(self.chat.profiles, [r for r in rows if r['day'] != '2026-10-09'],
                                      date(2026, 10, 9), terms('继续玩《双人成行》'))
        self.assertEqual(near['临近'][0]['日期'], '2026-10-10')
        self.assertIn('约好', near['临近'][0]['内容'])

    async def test_recall_uses_anchored_source_words_without_mixing_people_or_old_plans(self):
        past = {'day': '2026-10-08', 'text': '一起玩《双人成行》，改天继续', 'subjects': ['owner', 'miko'],
                'sources': [{'content': '今晚还打游戏吗？'}, {'content': '接着玩《双人成行》吧'}]}
        result = conversation_background(self.chat.profiles, [past], date(2026, 10, 9), terms('在家打游戏'))
        self.assertIn('双人成行', result['近期对话'][0]['内容'])
        # A whole extraction batch can share sources: unrelated snippets must
        # not make an old bedtime plan relevant to today's lunch.
        rows = [{'day': '2026-10-09', 'text': '中午一起吃披萨', 'subjects': ['owner', 'miko']},
                {'day': '2026-10-08', 'text': '另一位好友约好买披萨', 'subjects': ['miko', 'other']},
                {'day': '2026-10-08', 'text': '约定今晚早睡', 'subjects': ['owner', 'miko'],
                 'sources': [{'content': '中午一起吃披萨'}]}]
        lunch = conversation_background(self.chat.profiles, rows, date(2026, 10, 9), terms('绘制分镜，吃饭休息'))
        self.assertEqual(set(lunch), {'当天'})
        unrelated = conversation_background(self.chat.profiles,
            [{'day': '2026-10-10', 'text': '明天一起去听交响音乐会', 'subjects': ['owner', 'miko']}],
            date(2026, 10, 9), terms('在家洗衣服和看电影'))
        self.assertEqual(unrelated, {})

    async def test_life_reads_committed_shared_memory_without_embedding_and_honors_memory_switch(self):
        self.chat.memory = self.memory
        self.chat.store.begin_turn('shared', 'shared-source', '10月9日晚上一起玩《双人成行》吧')
        self.chat.store.finish('shared-source', '好，晚上一起玩', 'completed', None)
        self.memory.social.store.sync()
        with self.memory.social.store.connect() as db:
            sid = db.execute('SELECT id FROM tulpa_scopes LIMIT 1').fetchone()[0]
            sources = [r[0] for r in db.execute('SELECT id FROM messages')]
            self.memory.structured.commit(db, sid, {'days': [
                {'subject': '', 'subjects': ['owner', 'miko'], 'peer': '', 'day': '2026-10-09',
                 'text': '橙汁与 Miko 约好今晚玩《双人成行》', 'sources': sources, 'through': 'shared'},
                {'subject': 'owner', 'subjects': ['owner'], 'peer': '', 'day': '2026-10-09',
                 'text': '橙汁独自在实验室买键盘', 'sources': sources, 'through': 'owner'}]})
        inputs = []
        async def complete(system, message, **kwargs):
            inputs.append(json.loads(message))
            return {'text': json.dumps({'entries': [{'text': '饭后在楼下散步'}]}, ensure_ascii=False)}
        self.chat.complete_memory = complete
        embeddings = self.memory.index._embedding_pool._cache['embedding']
        with patch.object(embeddings, 'embed', side_effect=AssertionError('Life must not call embeddings')) as embed:
            await self.life.generate(date(2026, 10, 9))
            embed.assert_not_called()
        self.assertEqual(len(inputs), 1)
        self.assertIn('双人成行', json.dumps(inputs[0]['对话提炼记录'], ensure_ascii=False))
        self.assertNotIn('实验室', json.dumps(inputs[0], ensure_ascii=False))
        self.memory.social.toggle(False)
        await self.life.generate(date(2026, 10, 9))
        self.assertNotIn('对话提炼记录', inputs[1])
