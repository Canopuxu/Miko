// Shared capability discovery for account detection and the running adapter.
(() => {
    function renderer() {
        const services = [];
        const providers = [];
        const components = [];
        const seen = new Set();
        const read = (value, key) => {
            if (!value || (typeof value !== 'object' && typeof value !== 'function')) return;
            const descriptor = Object.getOwnPropertyDescriptor(value, key);
            return descriptor && 'value' in descriptor ? descriptor.value : undefined;
        };
        const ownValues = value => Reflect.ownKeys(value).flatMap(key => {
            const item = read(value, key);
            return item === undefined ? [] : [item];
        });
        const elements = [document.querySelector('#app'), ...document.querySelectorAll('[data-v-app]')];
        // QQ can move or rename its mount element. Inspect only DOM nodes for
        // Vue's own component markers, without walking arbitrary page globals.
        if (!elements.some(element => element && read(element, '__vue_app__'))) {
            elements.push(...document.querySelectorAll('*'));
        }
        let route = '';
        for (const element of elements) {
            if (!element) continue;
            const app = read(element, '__vue_app__');
            if (app) {
                const path = app.config?.globalProperties?.$route?.path;
                if (typeof path === 'string') route = path;
                components.push(app._instance, app._container?._vnode);
                if (app._context?.provides) providers.push(app._context.provides);
            }
            components.push(read(element, '__vueParentComponent'));
        }
        while (components.length && seen.size < 10000) {
            const value = components.pop();
            if (!value || typeof value !== 'object' || seen.has(value)) continue;
            seen.add(value);
            if (value.provides) providers.push(value.provides);
            if (value.appContext?.provides) providers.push(value.appContext.provides);
            components.push(value.component, value.subTree);
            if (Array.isArray(value.children)) components.push(...value.children);
        }
        // Provider names and DI-container fields are implementation details.
        // Find service instances by the methods/data used by this adapter.
        const objects = [];
        const registries = new Set();
        const seeds = new Set();
        for (let provider of new Set(providers)) {
            for (let depth = 0; provider && provider !== Object.prototype && depth < 8; depth++) {
                if (registries.has(provider)) break;
                registries.add(provider);
                for (const value of ownValues(provider)) seeds.add(value);
                provider = Object.getPrototypeOf(provider);
            }
        }
        // QQ's existing Inversify containers provide a direct route to service
        // instances. Use it first: walking router/component graphs can otherwise
        // exhaust the search before reaching the actual bindings.
        for (const value of seeds) {
            const container = read(value, 'container') || value;
            const bindings = read(read(container, '_bindingDictionary'), '_map');
            if (!(bindings instanceof Map)) continue;
            for (const entries of bindings.values()) {
                for (const binding of Array.isArray(entries) ? entries : [entries]) {
                    objects.push([read(binding, 'cache'), 0]);
                }
            }
        }
        objects.push(...[...seeds].map(value => [value, 0]));
        const visited = new Set();
        let cursor = 0;
        while (cursor < objects.length && visited.size < 12000) {
            const [value, depth] = objects[cursor++];
            if (!value || typeof value !== 'object' || visited.has(value)) continue;
            visited.add(value);
            if (typeof value.getMsgElements === 'function' || typeof value.getQQFaceLabel === 'function'
                    || value.buddyMap instanceof Map || (value.buddyMap && typeof value.buddyMap === 'object')) {
                services.push(value);
                if (services.some(item => !!item.buddyMap)
                        && services.some(item => typeof item.getMsgElements === 'function')
                        && services.some(item => typeof item.getQQFaceLabel === 'function')) break;
                continue;
            }
            if (depth >= 8) continue;
            const children = value instanceof Map ? [...value.values()]
                : Array.isArray(value) ? value : ownValues(value);
            for (const child of children) objects.push([child, depth + 1]);
        }
        return {services, route};
    }

    function account(auth) {
        const uin = String(auth?.uin || '');
        return /^[1-9]\d+$/.test(uin) ? uin : '';
    }

    function capabilities(environment) {
        const services = environment.services;
        return {friends: services.some(value => !!value.buddyMap),
                send_images: services.some(value => typeof value.getMsgElements === 'function'),
                faces: services.some(value => typeof value.getQQFaceLabel === 'function')};
    }

    // Public QQ IPC wire formats, including the earlier IPC_UP_2/3 family:
    // https://github.com/LLOneBot/LuckyLilliaBot/blob/v4.7.5/src/ntqqapi/ntcall.ts
    function transport(ipcMain, peerId) {
        const modern = `RM_IPCFROM_RENDERER${peerId}`;
        if (ipcMain.listenerCount(modern)) return {channel: modern, kind: 'renderer'};
        for (const number of [2, 3]) {
            const channel = `IPC_UP_${number}`;
            if (ipcMain.listenerCount(channel)) return {channel, kind: 'legacy', number};
        }
        return null;
    }

    function request(connection, callbackId, cmdName, payload, peerId) {
        return connection.kind === 'legacy'
            ? [{type: 'request', callbackId, eventName: `ns-ntApi-${connection.number}`}, [cmdName, ...payload]]
            : [{type: 'request', callbackId, eventName: 'ntApi', peerId},
                {cmdName, cmdType: 'invoke', payload: [...payload, undefined]}];
    }

    // Earlier releases batch notifications in IPC_DOWN_2/3; modern releases
    // send a single command. Both retain the same native message records.
    function notifications(connection, channel, meta, body) {
        if (connection.kind === 'legacy') {
            return /^IPC_DOWN_[23]$/.test(channel) && !meta?.callbackId && Array.isArray(body) ? body : [];
        }
        return meta?.type === 'request' && body?.cmdName ? [body] : [];
    }

    async function locate(electron) {
        const candidates = [];
        let ipcWindows = 0;
        let serviceWindows = 0;
        for (const win of electron.BrowserWindow.getAllWindows()) {
            const contents = win.webContents;
            if (contents.isDestroyed()) continue;
            const connection = transport(electron.ipcMain, contents.id);
            if (!connection) continue;
            ipcWindows++;
            try {
                const result = await contents.executeJavaScript(`(() => {
                    const environment = (${renderer.toString()})();
                    return {route: environment.route, capabilities: (${capabilities.toString()})(environment)};
                })()`);
                if (result.route || result.capabilities.send_images || result.capabilities.faces) serviceWindows++;
                if (result.capabilities.friends) candidates.push({contents, connection, ...result});
            } catch { /* A login/closing window is not a usable message view. */ }
        }
        if (!candidates.length) throw new Error(!ipcWindows ? 'MIKO_IPC_UNAVAILABLE'
            : serviceWindows ? 'MIKO_FRIENDS_UNAVAILABLE' : 'MIKO_UI_NOT_READY');
        const main = candidates.filter(value => /^\/main(?:\/|$)/.test(value.route));
        if (main.length === 1) return main[0];
        if (candidates.length === 1) return candidates[0];
        // Avoid binding to an arbitrary settings/chat window when QQ exposes
        // several independent contexts and no unique main view can be found.
        throw new Error('MIKO_UI_AMBIGUOUS');
    }

    return {renderer, account, capabilities, transport, request, notifications, locate};
})()
