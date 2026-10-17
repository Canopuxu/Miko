import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx
from PIL import Image

from miko.api.qq_avatar import qq_avatar_router
from miko.adapters.qq.onebot import QqConfig
from miko.settings import write_json


class AvatarSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        self.saved = {'account':'123','http_url':'http://127.0.0.1:17971','ws_url':'ws://127.0.0.1:17971/'}
        write_json(self.root / 'config/qq.local.json', self.saved)
        self.qq = SimpleNamespace(client=SimpleNamespace(config=QqConfig(**self.saved),avatar=AsyncMock()),
            receiver=SimpleNamespace(status=lambda:{'state':'connected'}),avatar=None,workers={})
        app = FastAPI()
        app.include_router(qq_avatar_router(SimpleNamespace(settings=SimpleNamespace(local_root=self.root)),self.qq))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver')
        buffer = io.BytesIO()
        Image.new('RGB',(32,32),'#386d8a').save(buffer,'PNG')
        self.picture = buffer.getvalue()
        self.base = '/api/settings/qq/avatar'

    async def asyncTearDown(self):
        if self.qq.avatar:
            await self.qq.avatar.close()
        await self.client.aclose()
        self.tmp.cleanup()

    async def test_default_enable_overlap_restore_and_persist(self):
        status = (await self.client.get(self.base)).json()
        self.assertFalse(status['enabled'])
        self.assertTrue(status['using_default'])
        self.assertEqual((await self.client.get(self.base+'/image/role')).status_code,200)
        with patch('miko.api.qq_avatar.current_qq_avatar',AsyncMock(return_value=self.picture)) as fetch:
            response = await self.client.put(self.base,json={'enabled':True})
            self.assertEqual(response.status_code,200,response.text)
            fetch.assert_awaited_once_with('123')
        avatar = self.qq.avatar
        await avatar.acquire('a')
        await avatar.acquire('b')
        self.assertEqual(self.qq.client.avatar.await_count,1)
        await avatar.interrupt('a')
        self.assertTrue(avatar.showing_miko)
        await avatar.interrupt('b')
        self.assertFalse(avatar.showing_miko)
        self.assertEqual(self.qq.client.avatar.await_count,2)
        saved = json.loads((self.root/'config/qq.local.json').read_text('utf-8'))
        self.assertTrue(Path(saved['miko_avatar_file']).is_relative_to(self.root))
        self.assertEqual(Path(saved['owner_avatar_file']).read_bytes(),self.picture)
        self.assertEqual(QqConfig(**saved).account,'123')
        await self.client.put(self.base,json={'enabled':False})
        self.assertIsNone(self.qq.avatar)
        status = (await self.client.get(self.base)).json()
        self.assertTrue(status['has_owner'])
        with patch('miko.api.qq_avatar.current_qq_avatar',AsyncMock()) as fetch:
            self.assertEqual((await self.client.put(self.base,json={'enabled':True})).status_code,200)
            fetch.assert_not_awaited()

    async def test_custom_role_automatic_restore_and_default_reset(self):
        payload = {'image_base64':base64.b64encode(self.picture).decode()}
        response = await self.client.put(self.base+'/image/role',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((await self.client.get(self.base+'/image/role')).content,self.picture)
        self.assertFalse((await self.client.get(self.base)).json()['using_default'])
        with patch('miko.api.qq_avatar.current_qq_avatar',AsyncMock(return_value=self.picture)) as fetch:
            await self.client.put(self.base,json={'enabled':True})
            fetch.assert_awaited_once_with('123')
        self.assertTrue((await self.client.post(self.base+'/default')).json()['using_default'])
        self.assertTrue(self.qq.avatar.miko_file.endswith('role.image'))

    async def test_role_can_be_customized_before_connecting_qq(self):
        write_json(self.root/'config/qq.local.json',{})
        response = await self.client.put(self.base+'/image/role',json={'image_base64':base64.b64encode(self.picture).decode()})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['using_default'])
        self.assertEqual((await self.client.get(self.base+'/image/role')).content,self.picture)

    async def test_reject_active_edits_and_bad_image_without_losing_settings(self):
        self.qq.workers['reply'] = object()
        self.assertEqual((await self.client.put(self.base,json={'enabled':True})).status_code,409)
        self.assertEqual(json.loads((self.root/'config/qq.local.json').read_text()),self.saved)
        self.qq.workers.clear()
        self.assertEqual((await self.client.put(self.base+'/image/role',json={'image_base64':'bad'})).status_code,400)
        self.assertEqual((await self.client.put(self.base,json={'enabled':True},headers={'origin':'https://elsewhere.example'})).status_code,403)

    async def test_account_switch_does_not_reuse_previous_accounts_images(self):
        payload = {'image_base64':base64.b64encode(self.picture).decode()}
        await self.client.put(self.base+'/image/role',json=payload)
        with patch('miko.api.qq_avatar.current_qq_avatar',AsyncMock(return_value=self.picture)):
            await self.client.put(self.base,json={'enabled':True})
        write_json(self.root/'config/qq.local.json',{**self.saved,'account':'456'})
        self.qq.client.config = QqConfig(**{**self.saved,'account':'456'})
        current = (await self.client.get(self.base)).json()
        self.assertFalse(current['using_default'])
        self.assertFalse(current['has_owner'])
        self.assertFalse(current['enabled'])


if __name__ == '__main__':
    unittest.main()
