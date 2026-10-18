// Loaded by Frida; execute once on QQ's own JavaScript thread.
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
let inspecting = false;
let disposed = false;
const hooks = [];
function dispose() {
    if (disposed) return;
    disposed = true;
    for (const hook of hooks.splice(0)) hook.detach();
    Interceptor.flush();
}
rpc.exports = {dispose};
const callbacks = {
    onEnter(args) { this.env = args[0]; },
    onLeave(status) {
        if (disposed || invoked || inspecting || status.toInt32() !== 0) return;
        inspecting = true;
        const env = this.env;
        const scope = Memory.alloc(Process.pointerSize);
        const source = Memory.alloc(Process.pointerSize);
        const result = Memory.alloc(Process.pointerSize);
        if (openScope(env, scope) !== 0) { inspecting = false; return; }
        try {
            // QQ also runs Node-API work in worker environments. Only the main
            // Electron environment owns the logged-in account and IPC handlers.
            const probe = 'String(typeof process !== "undefined" && process.type === "browser" && !!process.mainModule)';
            let status = makeString(env, Memory.allocUtf8String(probe), probe.length, source);
            if (status === 0) status = runScript(env, source.readPointer(), result);
            if (status !== 0) {
                if (status === 10) clearError(env, result);
                return;
            }
            const check = Memory.alloc(16);
            if (readString(env, result.readPointer(), check, 16, ptr(0)) !== 0 || check.readUtf8String() !== 'true') return;
            invoked = true;
            const code = Memory.allocUtf8String(payload);
            status = makeString(env, code, unescape(encodeURIComponent(payload)).length, source);
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
            inspecting = false;
            if (invoked) setImmediate(dispose);
        }
    }
};

function attachEventLoop() {
    // Windows x64 returns V8 Local/MaybeLocal through an explicit result
    // pointer. Resolve public exports; no QQ/V8 structure offsets are used.
    if (Process.platform !== 'windows' || Process.arch !== 'x64') return false;
    const entries = native.enumerateExports();
    const find = prefix => entries.find(entry => entry.name.startsWith(prefix));
    const specs = {
        current: ['?TryGetCurrent@Isolate@v8@@', 'pointer', []],
        open: ['??0HandleScope@v8@@', 'void', ['pointer', 'pointer']],
        close: ['??1HandleScope@v8@@', 'void', ['pointer']],
        catch: ['??0TryCatch@v8@@', 'void', ['pointer', 'pointer']],
        release: ['??1TryCatch@v8@@', 'void', ['pointer']],
        context: ['?GetCurrentContext@Isolate@v8@@', 'pointer', ['pointer', 'pointer']],
        string: ['?NewFromUtf8@String@v8@@', 'pointer', ['pointer', 'pointer', 'pointer', 'int', 'int']],
        compile: ['?Compile@Script@v8@@', 'pointer', ['pointer', 'pointer', 'pointer', 'pointer']],
        run: ['?Run@Script@v8@@QEAA?AV?$MaybeLocal@VValue@v8@@@2@V?$Local@VContext@v8@@@2@@Z',
            'pointer', ['pointer', 'pointer', 'pointer']],
        boolean: ['?BooleanValue@Value@v8@@', 'bool', ['pointer', 'pointer']]
    };
    const loop = native.findExportByName('uv_run');
    const writer = find('?WriteUtf8V2@String@v8@@') || find('?WriteUtf8@String@v8@@');
    if (!loop || !writer || Object.values(specs).some(([name]) => !find(name))) return false;
    const v8 = Object.fromEntries(Object.entries(specs).map(([key, [name, result, args]]) =>
        [key, new NativeFunction(find(name).address, result, args)]));
    const modern = writer.name.startsWith('?WriteUtf8V2@');
    const write = new NativeFunction(writer.address, modern ? 'size_t' : 'int', modern
        ? ['pointer', 'pointer', 'pointer', 'size_t', 'int', 'pointer']
        : ['pointer', 'pointer', 'pointer', 'int', 'pointer', 'int']);
    hooks.push(Interceptor.attach(loop, {onEnter() {
        if (disposed || invoked || inspecting) return;
        const isolate = v8.current();
        if (isolate.isNull()) return;
        inspecting = true;
        const scope = Memory.alloc(24), catcher = Memory.alloc(64);
        v8.open(scope, isolate);
        v8.catch(catcher, isolate);
        try {
            const context = Memory.alloc(8);
            v8.context(isolate, context);
            const ctx = context.readPointer();
            if (ctx.isNull()) return;
            const evaluate = code => {
                const source = Memory.alloc(8), script = Memory.alloc(8), result = Memory.alloc(8);
                v8.string(source, isolate, Memory.allocUtf8String(code), 0, -1);
                if (source.readPointer().isNull()) return ptr(0);
                v8.compile(script, ctx, source.readPointer(), ptr(0));
                if (script.readPointer().isNull()) return ptr(0);
                v8.run(script.readPointer(), result, ctx);
                return result.readPointer();
            };
            const main = evaluate('typeof process !== "undefined" && process.type === "browser" && !!process.mainModule');
            if (main.isNull() || !v8.boolean(main, isolate)) return;
            invoked = true;
            const result = evaluate(payload);
            if (result.isNull()) { send({error: 'execute', status: 'javascript_exception'}); return; }
            const output = Memory.alloc(65536);
            if (modern) write(result, isolate, output, 65535, 0, ptr(0));
            else write(result, isolate, output, 65535, ptr(0), 0);
            send({result: output.readUtf8String()});
        } finally {
            v8.release(catcher);
            v8.close(scope);
            inspecting = false;
            if (invoked) setImmediate(dispose);
        }
    }}));
    return true;
}
if (!attachEventLoop()) {
    for (const name of ['napi_get_cb_info', 'napi_call_function', 'napi_make_callback']) {
        const address = native.findExportByName(name);
        if (address) hooks.push(Interceptor.attach(address, callbacks));
    }
}
send({ready: true});
