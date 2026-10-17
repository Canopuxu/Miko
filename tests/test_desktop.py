import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from miko.settings import write_json
from miko.storage.backups import backup_list, relocate_settings

spec = importlib.util.spec_from_file_location('miko_desktop', Path(__file__).parents[1] / 'scripts/desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopDataTests(unittest.TestCase):
    def test_update_backs_up_existing_data_before_opening_it(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'MikoLocal'
            first = {'version': '0.1.0', 'build': 'first'}
            desktop.prepare_data(root, first)
            database = root / 'data/database/miko.sqlite3'
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE saved(text)')
                db.execute("INSERT INTO saved VALUES('原来的聊天和资料')")
            write_json(root / 'config/provider.local.json', {'api_key': 'user-secret'})
            write_json(root / 'runtime/desktop-build.json', {**first, 'local_root': str(root)})
            second = {'version': '0.1.1', 'build': 'second'}
            desktop.prepare_data(root, second)
            snapshots = backup_list(root)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]['reason'], 'before-upgrade')
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT text FROM saved').fetchone()[0], '原来的聊天和资料')
            self.assertEqual(json.loads((root / 'config/provider.local.json').read_text('utf-8'))['api_key'], 'user-secret')
            write_json(root / 'runtime/desktop-build.json', {**second, 'local_root': str(root)})
            desktop.prepare_data(root, second)
            self.assertEqual(len(backup_list(root)), 1)

    def test_move_keeps_local_avatars_and_external_files_distinct(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / '中文目录 有空格' / 'MikoLocal'
            (root / 'config').mkdir(parents=True)
            previous = 'F:/old/MikoLocal'
            write_json(root / 'config/qq.local.json', {
                'miko_avatar_file': previous + '/data/qq/avatars/role.image',
                'owner_avatar_file': 'F:/outside/avatar.png'})
            relocate_settings(root, previous)
            value = json.loads((root / 'config/qq.local.json').read_text('utf-8'))
            self.assertEqual(value['miko_avatar_file'], str(root / 'data/qq/avatars/role.image'))
            self.assertEqual(value['owner_avatar_file'], 'F:/outside/avatar.png')


if __name__ == '__main__':
    unittest.main()
