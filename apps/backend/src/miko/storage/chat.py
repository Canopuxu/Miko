from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
import uuid


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class ChatStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("聊天数据库版本较新，请使用对应版本的 Miko")
            if version == 0:
                sql = (Path(__file__).parent / "migrations/001_chat.sql").read_text(encoding="utf-8")
                db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nPRAGMA user_version=1;\nCOMMIT;")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute("""UPDATE messages SET status='interrupted',
                error='后台重启，本轮回复未完成', updated_at=? WHERE status='running'""", (now_ms(),))

    def conversation(self, conversation_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            return dict(row) if row else None

    def list_conversations(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT c.*,
                (SELECT substr(text, 1, 80) FROM messages WHERE conversation_id=c.id ORDER BY sequence DESC LIMIT 1) AS preview
                FROM conversations c WHERE source='web' ORDER BY updated_at DESC, rowid DESC""")]

    def begin_turn(self, conversation_id: str, turn_id: str, message: str) -> str:
        user_id = uuid.uuid4().hex
        stamp = now_ms()
        title = " ".join(message.split())[:36]
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?, 'web', ?, ?)",
                       (conversation_id, title, stamp, stamp))
            db.executemany("""INSERT INTO messages
                (id, conversation_id, turn_id, author_id, source, text, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'web', ?, ?, ?, ?)""", [
                (user_id, conversation_id, turn_id, 'owner', message, 'completed', stamp, stamp),
                (turn_id, conversation_id, turn_id, 'miko', '', 'running', stamp, stamp),
            ])
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, conversation_id))
        return user_id

    def checkpoint(self, turn_id: str, text: str):
        with self.connect() as db:
            db.execute("UPDATE messages SET text=?, updated_at=? WHERE id=? AND status='running'",
                       (text, now_ms(), turn_id))

    def finish(self, turn_id: str, text: str, status: str, error: str | None):
        stamp = now_ms()
        with self.connect() as db:
            db.execute("UPDATE messages SET text=?, status=?, error=?, updated_at=? WHERE id=?",
                       (text, status, error, stamp, turn_id))
            db.execute("""UPDATE conversations SET updated_at=? WHERE id=
                (SELECT conversation_id FROM messages WHERE id=?)""", (stamp, turn_id))

    def messages(self, conversation_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT m.*, p.kind AS author_kind, p.display_name AS author_name
                FROM messages m JOIN people p ON p.id=m.author_id
                WHERE conversation_id=? ORDER BY sequence""", (conversation_id,))
            result = []
            for row in rows:
                data = dict(row)
                data.pop('sequence')
                data['author'] = {'id': data.pop('author_id'), 'kind': data.pop('author_kind'),
                                  'name': data.pop('author_name')}
                result.append(data)
            return result

    def history(self, conversation_id: str) -> list[dict]:
        with self.connect() as db:
            # A web turn is usable as context only after its Miko response completes.
            rows = db.execute("""SELECT m.text, p.kind FROM messages m JOIN people p ON p.id=m.author_id
                WHERE m.conversation_id=? AND EXISTS (
                    SELECT 1 FROM messages a WHERE a.turn_id=m.turn_id
                    AND a.author_id='miko' AND a.status='completed') ORDER BY m.sequence""", (conversation_id,))
            return [{'role': 'assistant' if row['kind'] == 'miko' else 'user', 'content': row['text']}
                    for row in rows]
