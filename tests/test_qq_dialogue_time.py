import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from miko.domain.conversation.context_selection import render_dialogue
import test_qq_replies as fixtures


class DialogueTimeTests(unittest.TestCase):
    def test_history_keeps_the_sixteen_hour_gap_in_beijing_time(self):
        records = []
        for kind, stamp, text in [('miko', '2026-10-14 02:06:20', '晚安'),
                                  ('contact', '2026-10-14 18:23:49', '哈喽miko')]:
            records.append({'author': {'kind': kind, 'name': kind}, 'text': text,
                'created_at': datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').replace(
                    tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000})
        value = render_dialogue({'chat_records': records, 'available_faces': [], 'output_format': {}})
        self.assertIn('[2026-10-14 02:06:20]', value)
        self.assertIn('[2026-10-14 18:23:49]', value)


class DialogueGapTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_pause_preserves_recent_context(self):
        fixture=fixtures.QqRepliesTests()
        await fixture.asyncSetUp()
        try:
            await fixture.receive(1, '晚安')
            fixture.now += 16*60*60
            await fixture.receive(2, '哈喽miko')
            self.assertEqual([r['text'] for r in fixture.service.store.context(fixture.key)], ['晚安', '哈喽miko'])
            self.assertEqual(len(fixture.service.store.messages(fixture.key)), 2)
        finally:
            await fixture.asyncTearDown()
