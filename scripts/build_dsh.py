"""Build a Windows x64 DSH runtime wheel with the upstream sdk-minimal dependency tree.

Requires Node.js, pnpm and scripts/dsh/requirements-build.txt on the build machine.
Only the staged CLI dependency manifest changes; upstream runtime code is preserved.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from wheel.wheelfile import WheelFile


REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / 'scripts/dsh'
VERSION = '0.1.5rc1'
NPM_VERSION = '0.1.5-rc.1'
COMMIT = '183f08e9c6dde7e36cd2318eaee70b0da08fb35e'
NODE_VERSION = '24.22.0'
PKG_VERSION = '6.21.0'
CLI_INTEGRITY = 'sha512-rmNmzQCg3oIc1z8xH7izRSOuy1TNzq+/NILyfM+7e8DKOyV+yBtg47WEsqR2SiIe1ATec3L/rUa1YhIcfQ2XEg=='
OFFICIAL_WHEEL = f'deepseek_harness_runtime_bin-{VERSION}-py3-none-win_amd64.whl'
WHEEL_URL = 'https://files.pythonhosted.org/packages/a2/0e/4b52e5792a7accb09c70831ad3182efa253e15118c683a49dc66f3cbd83a/' + OFFICIAL_WHEEL
WHEEL_SHA256 = 'd031a5ab4919c572fd7e48c7ef19ec8b1896e93d93c44b0937bb958c528f8acf'
NODE_SHA256 = 'f9683a19877e12a9b167305c8db71040d812876359de21f0d2e97c3f321f80c3'
EXECUTABLE = 'deepseek-harness-sdk-runtime-win-x64.exe'


def run(argv, **kwargs):
    subprocess.run([str(value) for value in argv], check=True, **kwargs)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url, path, digest, algorithm='sha256'):
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.part')
        with urllib.request.urlopen(url, timeout=30) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        temporary.replace(path)
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != digest:
        raise SystemExit(f'Checksum mismatch: {path}')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=REPO.parent / 'MikoLocal/cache/dsh-build')
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build this wheel on Windows x64.')
    pnpm = shutil.which('pnpm.cmd') or shutil.which('pnpm')
    node = shutil.which('node.exe') or shutil.which('node')
    if not pnpm or not node:
        raise SystemExit('Node.js and pnpm are required on the build machine.')
    if f'deepseek-harness-runtime-bin=={VERSION}' not in (REPO / 'requirements.lock.txt').read_text().splitlines():
        raise SystemExit('The DSH recipe differs from requirements.lock.txt.')
    cache = args.cache.resolve()
    downloads, output = cache / 'downloads', cache / 'wheels'
    downloads.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=True)
    # New lock/config inputs receive a separate staging tree, without reusing stale modules.
    inputs = {p.relative_to(RECIPE).as_posix(): sha256(p)
              for p in sorted(RECIPE.rglob('*')) if p.is_file()}
    inputs['build_dsh.py'] = sha256(Path(__file__))
    identity = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:12]
    stage = cache / f'stage-{identity}'
    stage.mkdir(exist_ok=True)
    cli_archive = fetch(
        f'https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-{NPM_VERSION}.tgz',
        downloads / f'dsh-{NPM_VERSION}.tgz',
        base64.b64decode(CLI_INTEGRITY.split('-', 1)[1]).hex(), 'sha512')
    cli = stage / 'closure/cli'
    cli.mkdir(parents=True, exist_ok=True)
    with tarfile.open(cli_archive) as archive:
        archive.extractall(stage / 'published-cli', filter='data')
    shutil.copytree(stage / 'published-cli/package', cli, dirs_exist_ok=True)
    manifest_path = cli / 'package.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    keep = json.loads((RECIPE / 'cli-dependencies.json').read_text('utf-8'))
    if set(keep) - manifest['dependencies'].keys():
        raise SystemExit('CLI dependency recipe contains an unknown upstream dependency.')
    manifest['dependencies'] = {name: value for name, value in manifest['dependencies'].items() if name in keep}
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for area in ('closure', 'tools'):
        shutil.copytree(RECIPE / area, stage / area, dirs_exist_ok=True)
        run([pnpm, '--dir', stage / area, 'install', '--prod', '--ignore-scripts',
             '--frozen-lockfile', '--config.registry=https://registry.npmjs.org'])
    shutil.copyfile(RECIPE / 'runtime-bootstrap.mjs', stage / 'closure/runtime-bootstrap.mjs')
    # Same packager patch as upstream DSH: preserve its subprocess runner protocol.
    packager = stage / 'tools/node_modules/@yao-pkg/pkg'
    if 'shared.patchChildProcess(entrypoint2);' in (packager / 'prelude/sea-bootstrap.bundle.js').read_text('utf-8'):
        raise SystemExit('Upstream DSH packager patch was not applied.')
    executable = stage / EXECUTABLE
    run([node, packager / 'lib-es5/bin.js', stage / 'closure', '--sea', '--targets',
         f'node{NODE_VERSION}-win-x64', '--compress', 'Brotli', '--output', executable])

    official = fetch(WHEEL_URL, downloads / OFFICIAL_WHEEL, WHEEL_SHA256)
    node_name = f'node-v{NODE_VERSION}-win-x64'
    node_archive = downloads / (node_name + '.zip')
    # pkg's SEA cache may already contain the verified official Node archive.
    pkg_cached_node = Path.home() / '.pkg-cache/sea' / node_archive.name
    if not node_archive.exists() and pkg_cached_node.is_file():
        shutil.copyfile(pkg_cached_node, node_archive)
    fetch(f'https://nodejs.org/dist/v{NODE_VERSION}/{node_archive.name}', node_archive, NODE_SHA256)
    wheel = output / f'deepseek_harness_runtime_bin-{VERSION}-1miko-py3-none-win_amd64.whl'
    dist_info = f'deepseek_harness_runtime_bin-{VERSION}.dist-info'
    # Retain upstream Python bindings, ripgrep and notices. WheelFile rebuilds RECORD.
    runtime_entry = f'deepseek_harness_runtime/runtime/{EXECUTABLE}'
    with zipfile.ZipFile(official) as source, WheelFile(wheel, 'w') as target:
        source.getinfo(runtime_entry)
        for item in source.infolist():
            if item.filename.endswith('/RECORD'):
                continue
            if item.filename == runtime_entry:
                target.writestr(item, executable.read_bytes())
            else:
                target.writestr(item, source.read(item))
        with zipfile.ZipFile(node_archive) as node_source:
            target.writestr(f'{dist_info}/licenses/Node.js-LICENSE.txt', node_source.read(node_name + '/LICENSE'))
        target.writestr(f'{dist_info}/licenses/yao-pkg-LICENSE.txt', (packager / 'LICENSE').read_bytes())
        # Include actual installed packages' notices alongside the upstream wheel's notices.
        modules = stage / 'closure/node_modules'
        for path in sorted(modules.rglob('*')):
            relative = path.relative_to(modules)
            if '.pnpm' in relative.parts or not path.is_file():
                continue
            if path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
                target.writestr(f'{dist_info}/licenses/npm/{relative.as_posix()}', path.read_bytes())
        for area in ('closure', 'tools'):
            target.writestr(f'{dist_info}/build/{area}-pnpm-lock.yaml', (stage / area / 'pnpm-lock.yaml').read_bytes())
        target.writestr(f'{dist_info}/build/pkg.patch', (RECIPE / 'tools/patches/pkg.patch').read_bytes())
    provenance = {
        'name': 'deepseek-harness-runtime-bin', 'version': VERSION,
        'profile': 'miko-windows-x64-sdk-minimal',
        'source': 'https://github.com/deepseek-ai/deepseek-harness', 'commit': COMMIT,
        'npm_version': NPM_VERSION, 'node': NODE_VERSION, 'packager': PKG_VERSION,
        'compression': 'Brotli', 'cli_integrity': CLI_INTEGRITY,
        'official_wheel_sha256': WHEEL_SHA256, 'node_archive_sha256': NODE_SHA256,
        'recipe_files_sha256': inputs, 'wheel': wheel.name,
        'wheel_sha256': sha256(wheel), 'wheel_bytes': wheel.stat().st_size,
        'executable_sha256': sha256(executable), 'executable_bytes': executable.stat().st_size,
    }
    (output / 'dsh-build.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(provenance, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
