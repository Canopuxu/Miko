from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from miko.api.chat import chat_router
from miko.storage.chat import ChatStore

class DeleteChatTests(unittest.TestCase):
    def test_delete_web_conversation_protects_running_and_qq(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ChatStore(Path(folder) / 'chat.sqlite3')
            store.begin_turn('web-test', 'turn-test', '测试')
            service = SimpleNamespace(store=store, turns={'turn-test': SimpleNamespace(conversation_id='web-test')})
            app = FastAPI(); app.include_router(chat_router(service))
            with TestClient(app) as client:
                self.assertEqual(client.delete('/api/conversations/web-test').status_code, 409)
                self.assertIsNotNone(store.conversation('web-test'))
                service.turns.clear(); store.finish('turn-test', '收到', 'completed', None)
                with store.connect() as db:
                    db.execute("INSERT INTO conversations VALUES('qq-test','好友','qq',1,1)")
                self.assertEqual(client.delete('/api/conversations/qq-test').status_code, 404)
                self.assertIsNotNone(store.conversation('qq-test'))
                self.assertEqual(client.delete('/api/conversations/web-test').status_code, 200)
                reopened = ChatStore(store.path)
                self.assertIsNone(reopened.conversation('web-test'))
                self.assertEqual(reopened.messages('web-test'), [])
