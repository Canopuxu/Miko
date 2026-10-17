"""Portable snapshots of Miko's local records and active settings."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
from tempfile import TemporaryDirectory
import uuid
import zipfile

from miko import __version__
from miko.settings import chat_prompts, write_json

DATA_DIRS = ('attachments', 'database', 'life', 'qq')
CONFIG_FILES = ('provider.local.json', 'qq-provider.local.json', 'life-provider.local.json',
                'memory-provider.local.json', 'embedding.local.json', 'rerank.local.json',
                'life.local.json', 'memory.local.json', 'retrieval.local.json', 'expression.local.json', 'qq.local.json')


def managed_path(root, relative):
    path = PurePosixPath(relative)
    if (not path.parts or path.is_absolute() or any(p in ('..', '.') or ':' in p or '\\' in p for p in path.parts)
            or str(path) != relative):
        raise ValueError('备份包含无效路径')
    allowed = (len(path.parts) >= 3 and path.parts[0] == 'data' and path.parts[1] in DATA_DIRS
               or len(path.parts) == 2 and path.parts[0] == 'config' and path.parts[1] in CONFIG_FILES)
    target = Path(root).resolve() / Path(*path.parts)
    if not allowed or not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('备份包含不属于 Miko 数据的文件')
    return target


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_path(root, backup_id):
    if len(backup_id) != 32 or any(c not in '0123456789abcdef' for c in backup_id):
        raise ValueError('备份编号无效')
    return Path(root) / 'backups/archives' / (backup_id + '.zip')


def create_backup(root, *, reason='manual'):
    root = Path(root).resolve()
    backup_id = uuid.uuid4().hex
    destination = archive_path(root, backup_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    (root / 'cache').mkdir(exist_ok=True)
    with TemporaryDirectory(dir=root / 'cache', prefix='backup-') as temporary:
        stage = Path(temporary)
        for folder in DATA_DIRS:
            source = root / 'data' / folder
            if not source.exists():
                continue
            for original in sorted(source.rglob('*')):
                if not original.is_file() or original.name.endswith(('-wal', '-shm', '.tmp')):
                    continue
                relative = original.relative_to(root).as_posix()
                managed_path(root, relative)
                target = managed_path(stage, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if original.suffix == '.sqlite3':
                    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source_db:
                        with closing(sqlite3.connect(target)) as target_db:
                            source_db.backup(target_db)
                else:
                    shutil.copyfile(original, target)
        for name in CONFIG_FILES:
            source = root / 'config' / name
            if source.is_file():
                target = managed_path(stage, 'config/' + name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        # Preserve the actual expression text even when it came from editable defaults.
        (stage / 'config').mkdir(exist_ok=True)
        write_json(stage / 'config/expression.local.json', chat_prompts(root)[0])
        files = {p.relative_to(stage).as_posix(): {'bytes': p.stat().st_size, 'sha256': checksum(p)}
                 for p in sorted(stage.rglob('*')) if p.is_file()}
        manifest = dict(format='miko-backup', version=1, app_version=__version__, reason=reason,
                        created_at=datetime.now(timezone.utc).isoformat(), source_root=str(root), files=files)
        write_json(stage / 'manifest.json', manifest)
        partial = destination.with_suffix('.tmp')
        try:
            with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
                for relative in ['manifest.json', *files]:
                    archive.write(stage / relative, relative)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
    return dict(id=backup_id, created_at=manifest['created_at'], reason=reason,
                bytes=destination.stat().st_size, files=len(files))


def backup_list(root):
    values = []
    for path in (Path(root) / 'backups/archives').glob('*.zip'):
        try:
            archive_path(root, path.stem)
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            values.append(dict(id=path.stem, bytes=path.stat().st_size,
                               created_at=manifest['created_at'], reason=manifest['reason'],
                               files=len(manifest['files'])))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
    return sorted(values, key=lambda row: row['created_at'], reverse=True)


def inspect_backup(archive_file, stage):
    """Validate and extract to a new private directory, never onto live data."""
    stage = Path(stage)
    if stage.exists():
        raise ValueError('这份备份已在处理中，请重新选择文件')
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_file) as archive:
            manifest_info = archive.getinfo('manifest.json')
            if manifest_info.file_size > 8 * 1024 * 1024:
                raise ValueError('备份目录过大')
            manifest = json.loads(archive.read(manifest_info))
            if manifest.get('format') != 'miko-backup' or manifest.get('version') != 1:
                raise ValueError('请选择 Miko 导出的完整备份文件')
            files = manifest['files']
            infos = archive.infolist()
            if (not isinstance(files, dict) or len(infos) != len(files) + 1
                    or {info.filename for info in infos} != {'manifest.json', *files}
                    or sum(info.file_size for info in infos) > 4 * 1024**3):
                raise ValueError('备份目录不完整或解压后超过 4 GiB')
            for relative, expected in files.items():
                target = managed_path(stage, relative)
                info = archive.getinfo(relative)
                if stat.S_ISLNK(info.external_attr >> 16) or info.file_size != expected['bytes']:
                    raise ValueError('备份文件校验失败')
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                if checksum(target) != expected['sha256']:
                    raise ValueError('备份文件校验失败')
                if relative.startswith('config/'):
                    json.loads(target.read_text('utf-8-sig'))
                elif target.suffix == '.sqlite3':
                    with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
                        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('备份中的数据库不完整')
            write_json(stage / 'manifest.json', manifest)
        return dict(created_at=manifest['created_at'], app_version=manifest['app_version'],
                    files=len(files), bytes=sum(row['bytes'] for row in files.values()),
                    has_api_keys=any('provider' in name or 'embedding' in name or 'rerank' in name
                                     for name in files if name.startswith('config/')))
    except Exception as exc:
        shutil.rmtree(stage)
        if isinstance(exc, (ValueError, KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile, RuntimeError)):
            raise ValueError('无法读取这份备份，请选择完整、未损坏的 Miko 备份 ZIP 文件') from None
        raise


def apply_backup(root, stage, *, reconnect=True):
    """Called only after the backend and bridge have stopped."""
    root, stage = Path(root).resolve(), Path(stage).resolve()
    manifest = json.loads((stage / 'manifest.json').read_text('utf-8'))
    # Recheck the staged contents before touching any live files.
    for relative, info in manifest['files'].items():
        if checksum(managed_path(stage, relative)) != info['sha256']:
            raise ValueError('备份暂存文件已改变，请重新选择备份')
    for folder in DATA_DIRS:
        target = root / 'data' / folder
        if not target.resolve().is_relative_to(root):
            raise ValueError('本地数据路径异常，未恢复数据')
        if target.exists():
            shutil.rmtree(target)
        source = stage / 'data' / folder
        if source.exists():
            shutil.copytree(source, target)
    config = root / 'config'
    config.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        source, target = stage / 'config' / name, config / name
        if source.is_file():
            shutil.copyfile(source, target)
        else:
            target.unlink(missing_ok=True)
    if reconnect:
        path = config / 'qq.local.json'
        if path.exists():
            qq = json.loads(path.read_text('utf-8-sig'))
            value = {key: qq[key] for key in ('account', 'friends', 'miko_avatar_file', 'owner_avatar_file', 'avatar_switch_enabled') if key in qq}
            for key in ('miko_avatar_file', 'owner_avatar_file'):
                original = str(value.get(key, '')).replace('\\', '/')
                prefix = manifest['source_root'].replace('\\', '/').rstrip('/') + '/'
                if original.casefold().startswith(prefix.casefold()):
                    value[key] = str(root / original[len(prefix):])
            write_json(path, dict(value, enabled=False))
        # PID, endpoint and authentication belong to this machine's next connection.
        (config / 'qq-client.local.json').unlink(missing_ok=True)
