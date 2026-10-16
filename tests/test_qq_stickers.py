import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import httpx
from fastapi import FastAPI
from PIL import Image

from miko.adapters.qq.media import StickerLibrary
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore
from miko.api.qq import qq_router


class StickerLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = QqStore(ChatStore(self.root / 'chat.sqlite3'))
        self.library = StickerLibrary(self.store, self.root / 'stickers')
        self.library.bind(SimpleNamespace(receiver=SimpleNamespace(status=lambda: dict(state='connected', account='123'))))
        out = io.BytesIO()
        Image.new('RGB',(8,8),'blue').save(out,'GIF',save_all=True,
            append_images=[Image.new('RGB',(8,8),'red')],duration=80,loop=0)
        self.raw = out.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def test_import_candidates_original_animation_and_account_scope(self):
        digest = self.library.add('123', self.raw, '笑着表示赞同', semantic=dict(tags=['赞同'],usage='认可朋友的提议'))
        rows = self.library.candidates('123','这个提议赞同')
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['sticker_id']),32)
        payload = self.library.segment('123',rows[0]['sticker_id'])
        self.assertEqual(base64.b64decode(payload['data']['file'][9:]), self.raw)
        self.assertEqual(self.library.list('123')[0]['info']['total_frames'],2)
        with self.assertRaises(ValueError):
            self.library.read('456',rows[0]['sticker_id'])
        self.library.edit('123',digest,'表示同意',False)
        self.assertEqual(self.library.candidates('123','同意'),[])
        self.library.edit('123',digest,'表示同意',True)
        self.assertEqual(len(self.library.candidates('123','同意')),1)

    def test_uncertain_meaning_and_tampered_bytes_are_not_candidates(self):
        digest = self.library.add('123',self.raw,'可能在笑',semantic=dict(uncertainty='图片文字看不清'))
        self.assertEqual(self.library.candidates('123','笑'),[])
        self.library.edit('123',digest,'开心地笑',True)
        self.assertEqual(len(self.library.candidates('123','笑')),1)
        self.library.path('123',digest).write_bytes(b'changed')
        self.assertEqual(self.library.candidates('123','笑'),[])

    def test_receipts_count_once_and_unknown_survives_restart(self):
        digest = self.library.add('123',self.raw,'点赞')
        self.library.attempt('123',digest,'run:0')
        self.assertEqual(self.library.candidates('123','点赞'),[])
        self.library.used('123',digest,'run:0')
        self.library.used('123',digest,'run:0')
        self.assertEqual(self.library.list('123')[0]['use_count'],1)
        self.library.attempt('123',digest,'run:1')
        restarted = StickerLibrary(self.store,self.root/'stickers')
        restarted.bind(self.library.qq)
        self.assertEqual(restarted.list('123')[0]['last_state'],'UNKNOWN')
        self.assertEqual(restarted.candidates('123','点赞'),[])
        restarted.delete('123',digest)
        self.assertEqual(restarted.list('123'),[])
        self.assertFalse(restarted.path('123',digest).exists())


class StickerApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_visual_note_passes_images_and_normalizes_no_uncertainty(self):
        from miko.domain.conversation.stickers import describe
        received=[]
        async def complete(system,message,**kwargs):
            received.append(kwargs)
            return {'text':json.dumps(dict(description='点赞',uncertainty='无'))}
        out=io.BytesIO();Image.new('RGB',(8,8),'green').save(out,'PNG')
        qq=SimpleNamespace(chat=SimpleNamespace(qq_provider='test',provider=None,complete_memory=complete))
        result=await describe(qq,out.getvalue())
        self.assertEqual(result['uncertainty'],'')
        self.assertTrue(received[0]['image_inputs'][0]['image_url']['url'].startswith('data:image/jpeg;base64,'))

    async def test_collect_and_preview_require_the_correct_live_message(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            store=QqStore(ChatStore(root/'chat.sqlite3'))
            key=('123','456')
            store.add_friend(*key,'朋友')
            store.event(key,1,'[图片]',1000,'friend',dict(segments=[dict(type='image')]))
            out=io.BytesIO();Image.new('RGB',(8,8),'green').save(out,'PNG');raw=out.getvalue()
            class Client:
                async def message(self,*args,**kwargs):
                    return dict(message=[dict(type='image',data=dict(file='base64://'+base64.b64encode(raw).decode()))])
            service=SimpleNamespace(store=store,client=Client(),stickers=StickerLibrary(store,root/'stickers'))
            app=FastAPI();app.include_router(qq_router(service))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                url='/api/qq/friends/123/456/images/1/0'
                picture=await client.get(url)
                self.assertEqual(picture.content,raw)
                collected=await client.post(url+'/collect',json=dict(description='打招呼'))
                self.assertEqual(collected.status_code,200)
                digest=collected.json()['sticker_id']
                self.assertEqual(len(service.stickers.list('123')),1)
                bad=await client.post('/api/qq/friends/123/999/images/1/0/collect',json=dict(description='不应读取'))
                self.assertEqual(bad.status_code,400)
                store.recall(key,1)
                recalled=await client.get(url)
                self.assertEqual(recalled.status_code,400)
                removed=await client.delete('/api/qq/stickers/123/'+digest)
                self.assertEqual(removed.status_code,200)
