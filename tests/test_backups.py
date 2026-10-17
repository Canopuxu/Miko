from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.backups import backups_router
from miko.restore import restore
from miko.settings import Settings, write_json
from miko.storage.backups import apply_backup, archive_path, backup_list, create_backup, inspect_backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'source'
        (self.root / 'config').mkdir(parents=True)
        (self.root / 'runtime').mkdir()
        write_json(self.root / 'config/miko.local.json', {'port': 7870})
        self.db_path = self.root / 'data/database/miko.sqlite3'
        self.db_path.parent.mkdir(parents=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE messages(text TEXT)')
        self.db.execute("INSERT INTO messages VALUES('待备份的聊天')")
        self.db.commit()
        (self.root / 'data/qq').mkdir(parents=True)
        (self.root / 'data/qq/sticker.bin').write_bytes(b'sticker-original')
        write_json(self.root / 'config/provider.local.json',
                   dict(api_base='https://example.test/v1', api_key='secret-never-return', model='chat'))
        write_json(self.root / 'config/qq.local.json', dict(account='123', friends=[{'id': '456'}],
                   http_token='old-bridge-secret', miko_avatar_file=str(self.root / 'data/qq/avatar.png')))
        write_json(self.root / 'config/qq-client.local.json', dict(token='machine-token', port=12345))
        (self.root / 'data/backups').mkdir()
        (self.root / 'data/backups/old-private-dump.txt').write_text('excluded')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def value(self, root):
        with closing(sqlite3.connect(root / 'data/database/miko.sqlite3')) as db:
            return db.execute('SELECT text FROM messages').fetchone()[0]

    def test_snapshot_wal_and_restore_to_other_folder_with_settings_and_media(self):
        backup = create_backup(self.root)
        path = archive_path(self.root, backup['id'])
        with zipfile.ZipFile(path) as archive:
            self.assertNotIn('config/qq-client.local.json', archive.namelist())
            self.assertNotIn('config/miko.local.json', archive.namelist())
            self.assertFalse(any('data/backups' in n or n.endswith('-wal') for n in archive.namelist()))
        target = Path(self.tmp.name) / 'moved'
        (target / 'config').mkdir(parents=True)
        write_json(target / 'config/miko.local.json', {'port': 7999})
        write_json(target / 'config/life-provider.local.json', {'stale': True})
        write_json(target / 'config/qq-client.local.json', {'token': 'target-machine'})
        stage = target / 'cache/stage'
        summary = inspect_backup(path, stage)
        self.assertNotIn('secret-never-return', json.dumps(summary))
        self.assertEqual(self.value(stage), '待备份的聊天')
        apply_backup(target, stage)
        self.assertEqual(self.value(target), '待备份的聊天')
        self.assertEqual((target / 'data/qq/sticker.bin').read_bytes(), b'sticker-original')
        self.assertEqual(json.loads((target / 'config/miko.local.json').read_text())['port'], 7999)
        self.assertFalse((target / 'config/life-provider.local.json').exists())
        self.assertFalse((target / 'config/qq-client.local.json').exists())
        qq = json.loads((target / 'config/qq.local.json').read_text())
        self.assertFalse(qq['enabled'])
        self.assertNotIn('http_token', qq)
        self.assertEqual(qq['miko_avatar_file'], str(target / 'data/qq/avatar.png'))
        self.assertEqual(backup_list(self.root)[0]['id'], backup['id'])

    def test_invalid_or_changed_backup_leaves_live_records_untouched(self):
        backup = create_backup(self.root)
        raw = archive_path(self.root, backup['id']).read_bytes()
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as before, zipfile.ZipFile(output, 'w') as after:
            for name in before.namelist():
                after.writestr(name, b'damaged' if name.endswith('sticker.bin') else before.read(name))
        broken = self.root / 'broken.zip'; broken.write_bytes(output.getvalue())
        with self.assertRaises(ValueError):
            inspect_backup(broken, self.root / 'cache/broken')
        stage = self.root / 'cache/good'
        inspect_backup(archive_path(self.root, backup['id']), stage)
        (stage / 'data/qq/sticker.bin').write_bytes(b'changed-after-preview')
        with self.assertRaises(ValueError):
            apply_backup(self.root, stage)
        self.assertEqual(self.value(self.root), '待备份的聊天')

    def test_named_backups_do_not_overwrite_and_legacy_files_remain_visible(self):
        first = create_backup(self.root, name='首次配置.zip')
        second = create_backup(self.root, name='首次配置')
        self.assertEqual(archive_path(self.root, first['id']).name, '首次配置.zip')
        self.assertEqual(archive_path(self.root, second['id']).name, '首次配置 (2).zip')
        legacy_id = 'a' * 32
        with zipfile.ZipFile(self.root / 'backups/archives' / (legacy_id + '.zip'), 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'created_at': first['created_at'], 'reason': 'manual', 'files': {}}))
        self.assertEqual({row['id'] for row in backup_list(self.root)}, {first['id'], second['id'], legacy_id})
        self.assertEqual(archive_path(self.root, legacy_id).name, legacy_id + '.zip')
        with self.assertRaises(ValueError):
            create_backup(self.root, name='../outside')

    def test_archive_cannot_write_outside_managed_data(self):
        destination = self.root / 'unsafe.zip'
        bad = 'data/qq/../../../outside.txt'
        with zipfile.ZipFile(destination, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(dict(format='miko-backup', version=1,
                files={bad: {'bytes': 1, 'sha256': 'x'}})))
            archive.writestr(bad, 'x')
        with self.assertRaises(ValueError):
            inspect_backup(destination, self.root / 'cache/unsafe')
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_restart_failure_rolls_back_records_and_keeps_safety_backup(self):
        backup = create_backup(self.root)
        stage = self.root / 'cache/restore/staged'
        inspect_backup(archive_path(self.root, backup['id']), stage)
        self.db.execute("UPDATE messages SET text='恢复前最新聊天'"); self.db.commit()
        self.db.close()
        with patch('miko.restore.stop') as stopped, patch('miko.restore.start', side_effect=[RuntimeError('startup'), None]) as started, \
                patch('miko.restore.ManagedQq', return_value=SimpleNamespace(disconnect=AsyncMock())), \
                patch('miko.restore.time.sleep'):
            with self.assertRaises(RuntimeError):
                restore(Settings(self.root, 7870), stage)
        stopped.assert_called_once(); self.assertEqual(started.call_count, 2)
        self.assertEqual(self.value(self.root), '恢复前最新聊天')
        self.assertTrue(any(row['reason'] == 'before-restore' for row in backup_list(self.root)))
        self.assertEqual(json.loads((self.root / 'runtime/restore-status.json').read_text())['state'], 'error')

    def test_restore_worker_applies_selected_snapshot_and_restarts(self):
        backup = create_backup(self.root)
        stage = self.root / 'cache/restore/selected'
        inspect_backup(archive_path(self.root, backup['id']), stage)
        self.db.execute("UPDATE messages SET text='稍后新增的聊天'"); self.db.commit(); self.db.close()
        with patch('miko.restore.stop') as stopped, patch('miko.restore.start') as started, \
                patch('miko.restore.ManagedQq', return_value=SimpleNamespace(disconnect=AsyncMock())), \
                patch('miko.restore.time.sleep'):
            restore(Settings(self.root, 7870), stage)
        stopped.assert_called_once(); started.assert_called_once()
        self.assertEqual(self.value(self.root), '待备份的聊天')
        self.assertFalse(stage.exists())
        self.assertEqual(json.loads((self.root / 'runtime/restore-status.json').read_text())['state'], 'completed')

    def test_api_preview_is_read_only_and_restore_requires_idle(self):
        self.chat = SimpleNamespace(settings=Settings(self.root, 7870), turns={},
            life=SimpleNamespace(generating=False), memory=SimpleNamespace(social=SimpleNamespace(active_jobs=set())))
        self.qq = SimpleNamespace(workers={})
        app = FastAPI(); app.include_router(backups_router(self.chat, self.qq))
        with TestClient(app) as client:
            backup = client.post('/api/settings/backups', json={'name': '我的备份'}).json()
            self.assertEqual(backup['name'], '我的备份')
            self.assertEqual(client.get('/api/settings/backups').status_code, 200)
            with patch('miko.api.backups.os.startfile') as opened:
                self.assertEqual(client.post('/api/settings/backups/' + backup['id'] + '/open-folder').status_code, 200)
                opened.assert_called_once_with(archive_path(self.root, backup['id']).parent)
            response = client.get('/api/settings/backups/' + backup['id'] + '/download')
            self.assertEqual(response.status_code, 200)
            preview = client.post('/api/settings/backups/inspect', content=response.content)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertNotIn('secret-never-return', preview.text)
            self.assertEqual(self.value(self.root), '待备份的聊天')
            disposable = client.post('/api/settings/backups/inspect', content=response.content).json()['stage_id']
            self.assertEqual(client.delete('/api/settings/backups/staged/' + disposable).status_code, 200)
            self.assertFalse((self.root / 'cache/restore' / disposable).exists())
            self.qq.workers['friend'] = object()
            body = {'stage_id': preview.json()['stage_id']}
            self.assertEqual(client.post('/api/settings/backups/restore', json=body).status_code, 409)
            self.qq.workers.clear()
            (self.root / 'logs').mkdir()
            with patch('miko.api.backups.subprocess.Popen') as process:
                self.assertEqual(client.post('/api/settings/backups/restore', json=body).status_code, 200)
                process.assert_called_once()
            self.assertEqual(client.get('/api/settings/backups/restore-status').json()['state'], 'starting')


if __name__ == '__main__':
    unittest.main()
