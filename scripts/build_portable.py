"""Build the Windows portable folder from pinned runtimes and an explicit file list."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile


REPO = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.13.16'


def run(command, **kwargs):
    subprocess.run([str(v) for v in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=REPO.parent / 'MikoLocal/cache/portable-build')
    args = parser.parse_args()
    output, cache = args.output.resolve(), args.cache.resolve()
    if output.exists():
        raise SystemExit('Output must be a new directory: ' + str(output))
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True)
    app = output / 'app'
    app.mkdir()
    for relative in ('apps/backend/src', 'apps/qq-bridge', 'config/defaults', 'licenses'):
        shutil.copytree(REPO / relative, app / relative, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    for name in ('LICENSE', 'THIRD_PARTY.md'):
        shutil.copyfile(REPO / name, output / name)
    shutil.copyfile(REPO / 'scripts/desktop/README.md', output / 'README.md')
    scripts = ('desktop.py', 'run_qq_client_bridge.py', 'load_qq_probe.py', 'discover_qq.py', 'qq_clients.py')
    (app / 'scripts').mkdir()
    for name in scripts:
        shutil.copyfile(REPO / 'scripts' / name, app / 'scripts' / name)
    for name in ('python-dependencies.lock.json', 'web-dependencies.lock.json', 'upstream.lock.json'):
        shutil.copyfile(REPO / 'config' / name, app / 'config' / name)

    pnpm = shutil.which('pnpm.cmd') or shutil.which('pnpm')
    if not pnpm:
        raise SystemExit('pnpm is required on the build machine.')
    run([pnpm, '--dir', REPO / 'apps/web', 'exec', 'tsc', '--noEmit'])
    run([pnpm, '--dir', REPO / 'apps/web', 'exec', 'vite', 'build', '--mode', 'standalone',
         '--outDir', app / 'apps/web/dist'])

    archive = cache / f'python-{PYTHON_VERSION}-embed-amd64.zip'
    if not archive.exists():
        urllib.request.urlretrieve(f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{archive.name}', archive)
    python = app / 'python'
    with zipfile.ZipFile(archive) as source:
        source.extractall(python)
    # Embeddable Python owns its complete search path; no user's Python/pip setup is used.
    (python / 'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../apps/backend/src\n../scripts\nimport site\n', encoding='utf-8')
    site = python / 'Lib/site-packages'
    env = dict(os.environ, PIP_CONFIG_FILE='nul', PIP_INDEX_URL='https://pypi.org/simple',
        PIP_EXTRA_INDEX_URL='', PIP_CACHE_DIR=str(cache / 'pip'), PYTHONPATH='', PYTHONHOME='')
    run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile',
        '--target', site, '-r', REPO / 'requirements.lock.txt', '-r', REPO / 'requirements-desktop.lock.txt'], env=env)
    run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile', '--no-deps',
        '--target', site, REPO / 'packages/upstream/memu'], env=env)
    # Setuptools is needed to build wheels, but isn't part of Miko's runtime.
    for name in ('setuptools', 'pkg_resources', '_distutils_hack'):
        if (site / name).is_dir():
            shutil.rmtree(site / name)
    for path in site.glob('setuptools-*.dist-info'):
        shutil.rmtree(path)
    for path in site.glob('*.pth'):
        path.unlink()
    shutil.rmtree(site / 'bin', ignore_errors=True)

    # Export real desktop dependency provenance and retain the wheel license files.
    metadata = '''import importlib.metadata as m,json
names=['pywebview','pythonnet','clr_loader','cffi','pycparser','proxy_tools','bottle']
print(json.dumps([{'name':n,'version':m.version(n),'license':m.metadata(n).get('License-Expression') or m.metadata(n).get('License'),
'files':[str(p) for p in m.files(n) if 'license' in str(p).lower() or 'notice' in str(p).lower()]} for n in names],ensure_ascii=False,indent=2))'''
    info = subprocess.check_output([str(python / 'python.exe'), '-B', '-X', 'utf8', '-c', metadata], text=True, encoding='utf-8')
    dependencies = json.loads(info)
    dependencies.append({'name': 'Microsoft.Web.WebView2', 'version': '1.0.3856.49',
        'source': 'https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49',
        'license': 'Microsoft Software License Terms',
        'files': ['licenses/Microsoft.WebView2-LICENSE.txt', 'licenses/Microsoft.WebView2-NOTICE.txt']})
    (app / 'config/desktop-dependencies.lock.json').write_text(json.dumps(dependencies, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(python / 'LICENSE.txt', app / 'licenses/CPython-LICENSE.txt')

    # Executable assets are generated from the project's existing avatar.
    from PIL import Image
    icon = cache / 'Miko.ico'
    Image.open(REPO / 'config/defaults/qq-avatar.png').convert('RGBA').save(icon, sizes=[(16,16),(32,32),(48,48),(256,256)])
    shutil.copyfile(icon, app / 'Miko.ico')
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    run([compiler, '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/reference:System.Windows.Forms.dll',
         '/win32icon:' + str(icon), '/out:' + str(output / 'Miko.exe'), REPO / 'scripts/desktop/Launcher.cs'])
    # Release identity changes whenever the application files change, including uncommitted builds.
    digest = hashlib.sha256()
    digest.update((REPO / 'scripts/desktop/Launcher.cs').read_bytes())
    for path in sorted(app.rglob('*')):
        if path.is_file() and path.suffix not in ('.pyc', '.pyo'):
            digest.update(path.relative_to(app).as_posix().encode())
            with path.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
    version = subprocess.check_output([str(python / 'python.exe'), '-B', '-c', 'from miko import __version__; print(__version__)'], text=True).strip()
    with archive.open('rb') as stream:
        python_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = {'version': version, 'build': digest.hexdigest(), 'python': PYTHON_VERSION,
        'python_archive_sha256': python_hash, 'workbench': False}
    (app / 'portable.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    inventory = sorted(p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_file())
    (app / 'portable-files.txt').write_text('\n'.join(inventory) + '\n', encoding='utf-8')
    # MikoLocal is deliberately absent: extracting a newer archive cannot replace user data.
    zip_path = output.parent / (output.name + '.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file() and path.relative_to(output).parts[0] != 'MikoLocal':
                bundle.write(path, Path('Miko') / path.relative_to(output))
    total = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    print(json.dumps({'folder': str(output), 'zip': str(zip_path), 'unpacked_bytes': total,
        'zip_bytes': zip_path.stat().st_size, **manifest}, ensure_ascii=False))


if __name__ == '__main__':
    main()
