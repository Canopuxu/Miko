// Loaded by Frida; execute once inside an existing QQ Node-API callback.
const required = ['napi_open_handle_scope', 'napi_close_handle_scope', 'napi_create_string_utf8',
    'napi_run_script', 'napi_get_value_string_utf8', 'napi_get_and_clear_last_exception'];
const candidates = Process.enumerateModules().sort((a,b) =>
    Number(/QQNT|wrapper/i.test(b.name)) - Number(/QQNT|wrapper/i.test(a.name)));
const native = candidates.find(module => required.every(name => module.findExportByName(name))
    && ['napi_get_cb_info', 'napi_call_function'].some(name => module.findExportByName(name)));
if (!native) throw new Error('MIKO_NAPI_UNAVAILABLE');
const api = (name, result, args) => new NativeFunction(native.getExportByName(name), result, args);
const openScope = api('napi_open_handle_scope', 'int', ['pointer', 'pointer']);
const closeScope = api('napi_close_handle_scope', 'int', ['pointer', 'pointer']);
const makeString = api('napi_create_string_utf8', 'int', ['pointer', 'pointer', 'size_t', 'pointer']);
const runScript = api('napi_run_script', 'int', ['pointer', 'pointer', 'pointer']);
const readString = api('napi_get_value_string_utf8', 'int', ['pointer', 'pointer', 'pointer', 'size_t', 'pointer']);
const clearError = api('napi_get_and_clear_last_exception', 'int', ['pointer', 'pointer']);
const payload = __MIKO_PAYLOAD__;
let invoked = false;
const hooks = [];
const callbacks = {
    onEnter(args) { this.env = args[0]; },
    onLeave() {
        if (invoked) return;
        invoked = true;
        const env = this.env;
        const scope = Memory.alloc(Process.pointerSize);
        const source = Memory.alloc(Process.pointerSize);
        const result = Memory.alloc(Process.pointerSize);
        if (openScope(env, scope) !== 0) { send({error: 'scope'}); return; }
        try {
            const code = Memory.allocUtf8String(payload);
            let status = makeString(env, code, unescape(encodeURIComponent(payload)).length, source);
            if (status === 0) status = runScript(env, source.readPointer(), result);
            if (status !== 0) {
                if (status === 10) clearError(env, result);
                send({error: 'execute', status});
                return;
            }
            const output = Memory.alloc(65536);
            const size = Memory.alloc(Process.pointerSize);
            status = readString(env, result.readPointer(), output, 65536, size);
            send(status === 0 ? {result: output.readUtf8String()} : {error: 'result', status});
        } finally {
            closeScope(env, scope.readPointer());
            setImmediate(() => hooks.forEach(hook => hook.detach()));
        }
    }
};
for (const name of ['napi_get_cb_info', 'napi_call_function']) {
    const address = native.findExportByName(name);
    if (address) hooks.push(Interceptor.attach(address, callbacks));
}
send({ready: true});
