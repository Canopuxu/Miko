"""Run backup restoration outside the backend process being restarted."""
import argparse
import asyncio
from pathlib import Path
import shutil
import time
import uuid

from miko.adapters.qq.managed import ManagedQq
from miko.cli import start, stop
from miko.settings import Settings, write_json
from miko.storage.backups import apply_backup, archive_path, create_backup, inspect_backup


def restore(settings, stage):
    status = settings.local_root / 'runtime/restore-status.json'
    safety = None
    bridge_config = settings.local_root / 'config/qq-client.local.json'
    original_bridge = bridge_config.read_bytes() if bridge_config.exists() else None
    rollback = None
    try:
        time.sleep(.6)  # Let the initiating HTTP response reach the page.
        stop(settings)
        asyncio.run(ManagedQq(settings.local_root).disconnect())
        safety = create_backup(settings.local_root, reason='before-restore')
        write_json(status, dict(state='restoring', message='正在恢复数据…', backup_id=safety['id']))
        apply_backup(settings.local_root, stage)
        start(settings)
        write_json(status, dict(state='completed', message='数据已恢复，请在 QQ 连接中重新选择账号', backup_id=safety['id']))
    except Exception:
        try:
            if safety:
                rollback = settings.local_root / 'cache/restore' / uuid.uuid4().hex
                inspect_backup(archive_path(settings.local_root, safety['id']), rollback)
                apply_backup(settings.local_root, rollback, reconnect=False)
                if original_bridge is not None:
                    bridge_config.write_bytes(original_bridge)
            start(settings)
            note = '恢复未完成，已保留原数据。请检查运行日志后重试。'
        except Exception:
            note = '恢复未完成，请重新启动 Miko；恢复前的备份保存在 backups/archives 中。'
        write_json(status, dict(state='error', message=note, backup_id=safety['id'] if safety else None))
        raise
    finally:
        for temporary in (stage, rollback):
            if temporary and temporary.resolve().is_relative_to(settings.local_root / 'cache/restore'):
                shutil.rmtree(temporary, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-root', type=Path, required=True)
    parser.add_argument('--stage-id', required=True)
    args = parser.parse_args()
    archive_path(args.local_root, args.stage_id)  # Shared identifier validation.
    restore(Settings.load(args.local_root), args.local_root / 'cache/restore' / args.stage_id)
