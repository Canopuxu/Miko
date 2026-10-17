from pathlib import Path
import tempfile
import sqlite3
import unittest

from miko.storage.chat import ChatStore


class ChatStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'chat.sqlite3'
        self.store = ChatStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reopen_preserves_messages_and_migration_version(self):
        self.store.begin_turn('conversation', 'turn', '你好')
        self.store.finish('turn', '你好呀', 'completed', None)
        reopened = ChatStore(self.path)
        self.assertEqual(reopened.history('conversation'), [
            {'role': 'user', 'content': '你好'}, {'role': 'assistant', 'content': '你好呀'},
        ])
        with reopened.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9)

    def test_interrupted_partial_reply_is_kept_but_excluded_from_context(self):
        self.store.begin_turn('conversation', 'finished', '第一轮')
        self.store.finish('finished', '已完成', 'completed', None)
        self.store.begin_turn('conversation', 'unfinished', '第二轮')
        self.store.checkpoint('unfinished', '写到一半')
        reopened = ChatStore(self.path)
        reopened.recover_interrupted()
        last = reopened.messages('conversation')[-1]
        self.assertEqual(last['text'], '写到一半')
        self.assertEqual(last['status'], 'interrupted')
        self.assertEqual(len(reopened.history('conversation')), 2)
        reopened.begin_turn('conversation', 'continued', '重新聊')
        reopened.finish('continued', '继续', 'completed', None)
        self.assertEqual(len(reopened.history('conversation')), 4)

    def test_three_authors_are_independent_of_qq_account(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO people VALUES ('qq:friend', 'contact', '测试好友')")
            db.execute("INSERT INTO conversations VALUES ('qq-conversation', '测试好友', 'qq', 1, 1)")
            for i, author in enumerate(('owner', 'miko', 'qq:friend')):
                db.execute('''INSERT INTO messages
                    (id, conversation_id, turn_id, author_id, source, text, status, created_at, updated_at)
                    VALUES (?, 'qq-conversation', ?, ?, 'qq', ?, 'completed', ?, ?)''',
                    (f'message-{i}', f'turn-{i}', author, f'内容 {i}', i, i))
        messages = self.store.messages('qq-conversation')
        self.assertEqual([m['author']['kind'] for m in messages], ['owner', 'miko', 'contact'])
        self.assertTrue(all(m['source'] == 'qq' for m in messages))

    def test_upgrade_from_chat_only_database_preserves_existing_history(self):
        old_path=Path(self.temporary.name)/'old.sqlite3'
        sql=(Path(__file__).parents[1]/'apps/backend/src/miko/storage/migrations/001_chat.sql').read_text(encoding='utf-8')
        with sqlite3.connect(old_path) as db:
            db.executescript(sql+'\nPRAGMA user_version=1;')
            db.execute("INSERT INTO conversations VALUES ('saved','原来的对话','web',1,1)")
        db.close()
        upgraded=ChatStore(old_path)
        self.assertEqual(upgraded.list_conversations()[0]['title'],'原来的对话')
        with upgraded.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
            self.assertEqual(db.execute('SELECT count(*) FROM qq_friends').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
