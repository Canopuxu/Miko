import json
import unittest
from unittest.mock import patch

from miko.adapters.qq.messages import FACES, FACE_NAMES, parse_bubbles, parse_message, update_face_catalog
from miko.domain.conversation.context_selection import render_dialogue
import test_qq_replies as fixtures


class FaceReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved_faces, self.saved_names = list(FACES), dict(FACE_NAMES)
        self.fixture = fixtures.QqRepliesTests()
        await self.fixture.asyncSetUp()
        update_face_catalog({'packs': [{'emojis': [
            {'q_sid': '76', 'q_des': '/赞'},
            {'q_sid': '182', 'q_des': '/笑哭'},
        ]}]})

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()
        FACES[:] = self.saved_faces
        FACE_NAMES.clear()
        FACE_NAMES.update(self.saved_names)

    async def test_catalog_is_visible_to_model_and_bounds_accepted_faces(self):
        request = {'chat_records': [], 'available_faces': FACES, 'output_format': {}}
        self.assertIn('76 赞', render_dialogue(request))
        self.assertEqual(parse_message([{'type': 'face', 'data': {'id': '182'}}])['text'], '[笑哭]')
        with self.assertRaisesRegex(ValueError, '表情不在本轮候选中'):
            parse_bubbles(json.dumps({'bubbles': [{'kind': 'face', 'face_id': '999999'}]}), [], [])

    async def test_face_only_and_text_then_face_go_through_normal_reply_queue(self):
        for mid, bubbles in enumerate((
            [{'kind': 'face', 'face_id': '76'}],
            [{'kind': 'text', 'text': '哈哈'}, {'kind': 'face', 'face_id': '182'}],
        ), start=1):
            await self.fixture.receive(mid, '刚刚把这件事搞定了')
            self.fixture.chat.bubbles = bubbles
            self.fixture.now += 120
            with patch.object(self.fixture.service, 'pause_between'):
                await self.fixture.due()
            run = self.fixture.service.store.runs()[0]
            self.assertEqual(run['state'], 'sent', run['error'])
            self.assertTrue(all(b['state'] == 'sent' for b in run['bubbles']))
        self.assertEqual([p[-1]['type'] for p in self.fixture.client.payloads], ['face', 'text', 'face'])

    async def test_failed_directory_refresh_keeps_working_candidates(self):
        with self.assertRaisesRegex(ValueError, '目录为空'):
            update_face_catalog({'packs': []})
        self.assertEqual(parse_bubbles('{"bubbles":[{"kind":"face","face_id":"76"}]}', [], []),
                         [{'kind': 'face', 'face_id': '76'}])
        self.assertEqual(FACE_NAMES['76'], '赞')
