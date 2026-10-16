"""Execute an explicitly supplied local probe in QQ, then detach the loader."""
import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path


def load_payload(pid, payload, result_dir, frida_path=None, await_result=True):
    if frida_path and str(frida_path) not in sys.path:
        sys.path.insert(0, str(frida_path))
    import frida
    root = Path(__file__).resolve().parents[1]
    if '__MIKO_COMPAT__' in payload:
        payload = payload.replace('__MIKO_COMPAT__', (root / 'apps/qq-bridge/compat.js').read_text('utf-8'))
    code = (root / 'apps/qq-bridge/loader.js').read_text('utf-8')
    output = Path(result_dir) / ('.probe-' + uuid.uuid4().hex + '.json')
    if await_result:
        payload = ('(() => { const save = value => process.mainModule.require("fs").writeFileSync('
            + json.dumps(str(output.resolve())) + ', JSON.stringify(value));'
            + 'Promise.resolve().then(() => (0,eval)(' + json.dumps(payload)
            + ')).then(value=>save({result:value}), error=>save({error:String(error)})); return "scheduled"; })()')
    code = code.replace('__MIKO_PAYLOAD__', json.dumps(payload))
    finished = threading.Event()
    session = frida.attach(pid)
    reply = {}

    def message(value, data):
        if value.get('type') == 'error':
            reply['error'] = value.get('description', 'QQ loader failed')
            finished.set()
        elif not value.get('payload', {}).get('ready'):
            reply.update(value.get('payload', {}))
            finished.set()

    try:
        script = session.create_script(code)
        script.on('message', message)
        script.load()
        if not finished.wait(20):
            raise TimeoutError('QQ did not enter a Node-API callback')
        if 'error' in reply:
            raise RuntimeError(str(reply['error']))
        if await_result:
            deadline = time.monotonic() + 20
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(.1)
            if not output.exists():
                raise TimeoutError('QQ client initialization timed out')
            reply = json.loads(output.read_text('utf-8'))
            if 'error' in reply:
                raise RuntimeError(reply['error'])
        return reply.get('result')
    finally:
        session.detach()
        output.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pid', type=int)
    parser.add_argument('payload', type=Path)
    parser.add_argument('--frida-path', type=Path)
    parser.add_argument('--await-result', action='store_true')
    args = parser.parse_args()
    result = load_payload(args.pid, args.payload.read_text('utf-8'), args.payload.parent,
                          args.frida_path, args.await_result)
    print(json.dumps({'result': result}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
