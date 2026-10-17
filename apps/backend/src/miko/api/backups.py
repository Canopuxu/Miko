import asyncio
import os
import shutil
import subprocess
import sys
import uuid

import psutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from miko.api.local import local_request
from miko.settings import REPO_ROOT, write_json
from miko.storage.backups import archive_path, backup_list, create_backup, inspect_backup
from miko.restore import restore_status as read_restore_status


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage_id: str = Field(pattern='^[a-f0-9]{32}$')


class BackupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=80)


def backups_router(chat, qq):
    router = APIRouter(prefix='/api/settings/backups', dependencies=[Depends(local_request)])
    root = chat.settings.local_root
    lock = asyncio.Lock()
    status_file = root / 'runtime/restore-status.json'

    def status():
        return read_restore_status(root)

    def idle():
        if (chat.turns or qq.workers or chat.life.generating or chat.memory.social.active_jobs
                or (getattr(qq, 'language', None) and qq.language.tasks)):
            raise HTTPException(409, '有回复或整理任务正在进行，请完成后再备份或恢复')
        if status()['state'] in ('starting', 'restoring'):
            raise HTTPException(409, '数据正在恢复，请等待服务重新启动')

    @router.get('')
    async def list_backups():
        return {'backups': await asyncio.to_thread(backup_list, root)}

    @router.post('')
    async def make_backup(body: BackupInput | None = None):
        async with lock:
            idle()
            try:
                result = await asyncio.to_thread(create_backup, root, name=body.name if body else '')
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            except OSError:
                raise HTTPException(400, '备份未完成，请检查磁盘空间和运行日志') from None
            return dict(result, message='备份已创建')

    @router.get('/restore-status')
    async def restore_status():
        return status()

    @router.get('/{backup_id}/download')
    async def download(backup_id: str):
        try:
            path = archive_path(root, backup_id)
            if not path.is_file():
                raise ValueError()
        except ValueError:
            raise HTTPException(404, '找不到这份备份') from None
        return FileResponse(path, media_type='application/zip', filename=path.name)

    @router.post('/{backup_id}/open-folder')
    async def open_backup_folder(backup_id: str):
        try:
            path = archive_path(root, backup_id)
            if not path.is_file():
                raise ValueError()
        except ValueError:
            raise HTTPException(404, '找不到这份备份') from None
        try:
            os.startfile(path.parent)
        except OSError:
            raise HTTPException(400, '文件夹未能打开，请从本地数据目录旁的 backups 文件夹查看') from None
        return {'message': '文件夹已打开'}

    @router.post('/inspect')
    async def inspect(request: Request):
        async with lock:
            if status()['state'] in ('starting', 'restoring'):
                raise HTTPException(409, '数据正在恢复，请稍后再选择文件')
            stage_id = uuid.uuid4().hex
            base = root / 'cache/restore'
            base.mkdir(parents=True, exist_ok=True)
            upload = base / (stage_id + '.zip')
            try:
                with upload.open('wb') as output:
                    async for chunk in request.stream():
                        output.write(chunk)
                summary = await asyncio.to_thread(inspect_backup, upload, base / stage_id)
                return dict(summary, stage_id=stage_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            except OSError:
                raise HTTPException(400, '无法暂存备份，请检查磁盘空间后重试') from None
            finally:
                upload.unlink(missing_ok=True)

    @router.post('/restore')
    async def restore_backup(body: RestoreInput):
        async with lock:
            idle()
            if not (root / 'cache/restore' / body.stage_id / 'manifest.json').is_file():
                raise HTTPException(400, '备份暂存已失效，请重新选择文件')
            status_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(status_file, {'state': 'starting', 'message': '正在停止服务并备份当前数据…'})
            try:
                with (root / 'logs/restore.log').open('ab') as log:
                    worker = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'miko.restore',
                        '--local-root', str(root), '--stage-id', body.stage_id], cwd=REPO_ROOT,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                write_json(status_file, {'state': 'starting', 'message': '正在停止服务并备份当前数据…',
                                        'pid': worker.pid, 'created': psutil.Process(worker.pid).create_time()})
            except OSError:
                write_json(status_file, {'state': 'error', 'message': '无法启动恢复，请检查运行日志'})
                raise HTTPException(400, '无法启动恢复，当前数据未改变') from None
            return {'message': '正在恢复，服务重启后页面会自动刷新'}

    @router.delete('/staged/{stage_id}')
    async def discard_preview(stage_id: str):
        async with lock:
            if status()['state'] in ('starting', 'restoring'):
                raise HTTPException(409, '正在使用这份备份，请等待恢复完成')
            try:
                archive_path(root, stage_id)
            except ValueError:
                raise HTTPException(400, '备份编号无效') from None
            stage = root / 'cache/restore' / stage_id
            if stage.resolve().is_relative_to((root / 'cache/restore').resolve()) and stage.is_dir():
                await asyncio.to_thread(shutil.rmtree, stage)
            return {'discarded': True}

    return router
