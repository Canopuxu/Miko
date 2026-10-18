"""Build Miko's local-only Windows x64 Frida wheel from the pinned upstream source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pickle
import platform
import subprocess
import sys


VERSION = '17.24.1'
COMMIT = '4bd95e6f33f90a41a28455ed71174252bbf49827'
OPTIONS = [
    '-Dfrida_tools=disabled', '-Dfrida_python=enabled',
    '-Dgraft_tool=disabled', '-Dgadget=disabled', '-Dserver=disabled',
    '-Dportal=disabled', '-Dinject=disabled',
    '-Dfrida-gum:quickjs=enabled', '-Dfrida-gum:v8=disabled',
    '-Dfrida-gum:database=disabled', '-Dfrida-gum:tests=disabled',
    # Core links this dependency even when Miko never opens an inspector.
    '-Dfrida-gum:inspector=enabled',
    '-Dfrida-core:local_backend=enabled', '-Dfrida-core:compat=disabled',
    '-Dfrida-core:fruity_backend=disabled', '-Dfrida-core:droidy_backend=disabled',
    '-Dfrida-core:simmy_backend=disabled', '-Dfrida-core:socket_backend=disabled',
    '-Dfrida-core:barebone_backend=disabled', '-Dfrida-core:compiler_backend=disabled',
    '-Dfrida-core:connectivity=disabled', '-Dfrida-core:tests=disabled',
    '-Doptimization=s',
]


def run(argv, **kwargs):
    subprocess.run([str(value) for value in argv], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'MikoLocal/cache/frida-build')
    parser.add_argument('--toolchain', type=Path, help='PortableMSVC directory containing env.json')
    parser.add_argument('--jobs', type=int, default=6)
    args = parser.parse_args()
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Build this wheel on Windows x64.')
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    recipe = hashlib.sha256(json.dumps([COMMIT, OPTIONS]).encode()).hexdigest()[:12]
    source, output = cache / f'source-{VERSION}', cache / 'wheels'
    build = cache / f'build-local-{recipe}'
    output.mkdir(exist_ok=True)
    if not source.exists():
        run(['git', 'clone', '--depth', '1', '--branch', VERSION,
             'https://github.com/frida/frida.git', source])
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if revision != COMMIT:
        raise SystemExit(f'Expected upstream commit {COMMIT}, found {revision}')
    run(['git', 'submodule', 'update', '--init', '--depth', '1', 'releng',
         'subprojects/frida-gum', 'subprojects/frida-core', 'subprojects/frida-python'], cwd=source)
    run(['git', 'submodule', 'update', '--init', '--depth', '1'], cwd=source / 'releng')
    run(['git', 'submodule', 'update', '--init', '--depth', '1', 'frida-bindgen'],
        cwd=source / 'subprojects/frida-python')
    run(['git', 'diff', '--exit-code'], cwd=source)

    environment = dict(os.environ)
    if args.toolchain:
        toolchain = args.toolchain.resolve()
        spec = json.loads((toolchain / 'env.json').read_text('utf-8'))
        for name in ('PATH', 'INCLUDE', 'LIB', 'LIBPATH'):
            paths = [str(Path(p) if Path(p).is_absolute() else toolchain / p) for p in spec[name]]
            environment[name] = ';'.join(paths) + ';' + environment.get(name, '')
        for name in ('CC', 'CXX', 'AR'):
            # Meson's env2mfile parses these values as shell words.
            environment[name] = subprocess.list2cmdline([Path(spec[name]).as_posix()])
        environment['VSCMD_ARG_TGT_ARCH'] = 'x64'
    environment.update(FRIDA_VERSION=VERSION, PYTHONUTF8='1',
                       MESON_BUILD_ROOT=str(build), FRIDA_DEPS=str(source / 'deps'),
                       PIP_CONFIG_FILE='nul', PIP_INDEX_URL='https://pypi.org/simple',
                       PIP_EXTRA_INDEX_URL='')
    environment['PATH'] = str(Path(sys.executable).parent) + ';' + environment['PATH']
    wrapper = 'import sys; sys.path.insert(0,sys.argv[1]); from releng.meson_configure import main; main()'
    if not (build / 'build.ninja').exists():
        run([sys.executable, '-c', wrapper, source, '--', *OPTIONS], cwd=source, env=environment)

    # Use the exact upstream-generated build environment, including Vala and resource tools.
    sys.path.insert(0, str(source))
    from releng import env
    state = pickle.loads((build / 'frida-env.dat').read_bytes())
    machine = state['host'] or state['build']
    build_environment = machine.make_merged_environment(environment)
    build_environment['FRIDA_ALLOWED_PREBUILDS'] = ','.join(state['allowed_prebuilds'])
    build_environment['FRIDA_DEPS'] = str(state['deps'])
    env.call_meson(['configure', str(build), *OPTIONS],
                   use_submodule=True, env=build_environment, check=True)
    env.call_meson(['compile', '-C', str(build), '-j', str(args.jobs)],
                   use_submodule=True, env=build_environment, check=True)

    generated = build / 'subprojects/frida-python/frida'
    extension, = generated.glob('_frida*.pyd')
    environment['FRIDA_EXTENSION'] = str(extension)
    run([sys.executable, 'setup.py', 'bdist_wheel', '--build-number', '1miko',
         '--dist-dir', output], cwd=source / 'subprojects/frida-python', env=environment)
    wheel, = output.glob(f'frida-{VERSION}-1miko-*.whl')
    with wheel.open('rb') as stream:
        wheel_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    provenance = {
        'name': 'frida', 'version': VERSION, 'profile': 'miko-windows-x64-local-quickjs',
        'source': 'https://github.com/frida/frida', 'commit': COMMIT,
        'gum_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'],
            cwd=source / 'subprojects/frida-gum', text=True).strip(),
        'options': OPTIONS, 'python': sys.version, 'wheel': wheel.name,
        'wheel_sha256': wheel_hash, 'wheel_bytes': wheel.stat().st_size,
        'extension_bytes': extension.stat().st_size,
        'extension_sha256': hashlib.sha256(extension.read_bytes()).hexdigest(),
        'dependencies_bundle': (source / 'deps/sdk-windows-x86_64-mt/VERSION.txt').read_text().strip(),
    }
    if args.toolchain:
        provenance['toolchain'] = {name: value for name, value in spec.items() if name.startswith('PORTABLE_')}
    (output / 'frida-build.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(provenance, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
