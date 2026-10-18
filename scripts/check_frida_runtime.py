"""Exercise the native APIs used by the QQ loader against a disposable Windows process."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-packages', type=Path)
    args = parser.parse_args()
    if args.site_packages:
        sys.path.insert(0, str(args.site_packages.resolve()))
    import frida
    if args.site_packages and not Path(frida.__file__).resolve().is_relative_to(args.site_packages.resolve()):
        raise RuntimeError('The requested Frida installation was not imported.')

    # ctypes catches this native exception. Raising it after detach also exercises
    # Windows unwinding, where a leftover instrumentation hook can crash a target.
    target = '''import ctypes,os,time
k = ctypes.WinDLL('kernelbase')
print(os.getpid(),flush=True)
while True:
    k.GetCurrentProcessId()
    try: k.RaiseException(0xe001,0,0,None)
    except OSError: pass
    time.sleep(.02)
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', target], stdout=subprocess.PIPE, text=True)
    session = None
    try:
        pid = int(child.stdout.readline())
        messages = []
        hooked = threading.Event()
        session = frida.attach(pid)
        script = session.create_script('''
const kernel = Process.getModuleByName('kernelbase.dll');
let reported = false;
Interceptor.attach(kernel.getExportByName('GetCurrentProcessId'), {
    onLeave(result) {
        if (!reported) { reported = true; send({kind:'hook',pid:result.toUInt32()}); }
    }
});
const thread = new NativeFunction(kernel.getExportByName('GetCurrentThreadId'),'uint32',[])();
send({kind:'ready',runtime:Script.runtime,pointerSize:Process.pointerSize,thread,
      memory:Memory.allocUtf8String('miko').readUtf8String()});
''', runtime='qjs')

        def on_message(message, data):
            messages.append(message)
            kinds = {m.get('payload', {}).get('kind') for m in messages}
            if {'hook', 'ready'} <= kinds or message.get('type') == 'error':
                hooked.set()

        script.on('message', on_message)
        script.load()
        if not hooked.wait(8):
            raise RuntimeError(f'Native hook did not execute: {messages}')
        if not any(m.get('payload', {}).get('pid') == pid for m in messages):
            raise RuntimeError(f'Native hook returned an incorrect process: {messages}')
        ready = next(m['payload'] for m in messages if m.get('payload', {}).get('kind') == 'ready')
        if ready['runtime'] != 'QJS' or ready['pointerSize'] != 8 or ready['memory'] != 'miko' or ready['thread'] <= 0:
            raise RuntimeError(f'Native function or memory check failed: {ready}')
        session.detach()
        session = None
        time.sleep(.3)
        if child.poll() is not None:
            raise RuntimeError('The target exited after the agent detached.')
        session = frida.attach(pid)
        second = session.create_script('send({pid:Process.id})', runtime='qjs')
        reattached = threading.Event()
        second.on('message', lambda message, data: reattached.set()
                  if message.get('payload', {}).get('pid') == pid else None)
        second.load()
        if not reattached.wait(3):
            raise RuntimeError('Attaching again did not succeed.')
        session.detach()
        session = None
        time.sleep(.3)
        if child.poll() is not None:
            raise RuntimeError('The target exited after attaching again.')
        print(json.dumps({'frida': frida.__version__, 'module': frida.__file__,
                          'native_hook': True, 'native_function': True, 'memory': True,
                          'reattach': True, 'native_exception_after_detach': True}), flush=True)
    finally:
        if session:
            session.detach()
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()


if __name__ == '__main__':
    main()
