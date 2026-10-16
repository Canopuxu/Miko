// Runs in QQ's existing main process. Uses its IPC handlers and logged-in session.
(async () => {
    const config = __MIKO_CONFIG__;
    const require = process.mainModule.require.bind(process.mainModule);
    const {ipcMain, BrowserWindow} = require('electron');
    const http = require('http');
    const crypto = require('crypto');
    const compat = __MIKO_COMPAT__;
    if (compat.account(globalThis.authData) !== config.account) throw new Error('QQ account mismatch');
    const selected = await compat.locate({BrowserWindow, ipcMain});
    const contents = selected.contents;
    if (globalThis.__mikoClientBridge) await globalThis.__mikoClientBridge.close();
    const peerId = contents.id;
    const connection = selected.connection;
    const pending = new Map();
    const streams = new Set();
    const eventBuffer = [];
    const sends = new Map();
    const fingerprint = elements => JSON.stringify(elements.map(e => e.textElement
        ? ['text',e.textElement.content] : e.faceElement ? ['face',e.faceElement.faceIndex]
        : e.picElement ? ['image',String(e.picElement.md5HexStr).toLowerCase()]
        : e.replyElement ? ['reply',e.replyElement.replayMsgId] : ['unknown',e.elementType]));
    let sequence = 0;
    const session = crypto.randomUUID();
    let closed = false;
    let lastFailure = '';
    const originalSend = contents.send;
    const identity = () => {
        if (compat.account(globalThis.authData) !== config.account || contents.isDestroyed()) throw new Error('QQ session changed');
        return {user_id: Number(config.account), nickname: globalThis.authData.nickName || '',
            pid: process.pid, version: require('electron').app.getVersion(),
            capabilities: selected.capabilities, transport: connection.kind, adapter_hash: config.adapter_hash};
    };
    const publish = (name, payload) => {
        try { identity(); } catch { return; }
        const event = {sequence: ++sequence, name, payload};
        eventBuffer.push(event);
        if (eventBuffer.length > 256) eventBuffer.shift();
        const line = JSON.stringify(event) + '\n';
        for (const stream of streams) {
            if (!stream.write(line)) stream.destroy();
        }
    };
    const wrappedSend = function(eventChannel, ...args) {
        const meta = args[0]; const body = args[1];
        if (meta?.callbackId && pending.has(meta.callbackId)) {
            const request = pending.get(meta.callbackId);
            pending.delete(meta.callbackId); clearTimeout(request.timer);
            if (meta.promiseStatue === 'full' || (connection.kind === 'legacy' && meta.promiseStatue == null)) request.resolve(body);
            else request.reject(new Error('QQ native request failed: ' + String(body?.message || JSON.stringify(body))));
            return;
        }
        for (const notification of compat.notifications(connection, eventChannel, meta, body)) {
            if (!['nodeIKernelMsgListener/onRecvMsg', 'nodeIKernelMsgListener/onRecvActiveMsg',
                    'nodeIKernelMsgListener/onAddSendMsg', 'nodeIKernelMsgListener/onMsgInfoListUpdate',
                    'nodeIKernelMsgListener/onActiveMsgInfoUpdate'].includes(notification?.cmdName)) continue;
            const records = notification.payload?.msgList || (notification.payload?.msgRecord ? [notification.payload.msgRecord] : []);
            for (const message of records) {
                if (message.chatType !== 1) continue;
                const send = sends.get(message.peerUid);
                if (send && String(message.senderUin) === config.account) {
                    if (!send.id && notification.cmdName.endsWith('/onAddSendMsg') && fingerprint(message.elements) === send.fingerprint) send.id=message.msgId;
                    if (send.id === message.msgId && message.sendStatus === 2) send.resolve(message);
                }
                publish(notification.cmdName, {msgList:[message]});
            }
        }
        return originalSend.call(this, eventChannel, ...args);
    };
    contents.send = wrappedSend;

    const invoke = (cmdName, payload) => new Promise((resolve, reject) => {
        identity();
        const id = 'miko-' + crypto.randomUUID();
        const timer = setTimeout(() => {pending.delete(id); reject(new Error('QQ native timeout'));}, 12000);
        pending.set(id, {resolve, reject, timer});
        try {
            ipcMain.emit(connection.channel, {sender: contents, senderFrame: contents.mainFrame},
                ...compat.request(connection, id, cmdName, payload, peerId));
        } catch (error) {pending.delete(id); clearTimeout(timer); reject(error);}
    });

    const rendererServices = `((${compat.renderer.toString()})()).services`;
    const friends = async () => contents.executeJavaScript(`(() => {
        const store=(${rendererServices}).find(v=>!!v.buddyMap);
        if(!store) throw new Error('QQ friend store unavailable');
        const buddies=store.buddyMap instanceof Map?[...store.buddyMap.values()]:Object.values(store.buddyMap);
        return buddies.map(v=>({user_id:Number(v.uin||v.coreInfo?.uin),uid:v.uid||v.coreInfo?.uid,
            nickname:v.coreInfo?.nick||v.nick||'',remark:v.coreInfo?.remark||v.remark||''}));
    })()`);

    const prepare = async item => JSON.parse(await contents.executeJavaScript(`(async () => {
        const services=${rendererServices};
        const service=services.find(v=>typeof v.getMsgElements==='function');
        const elements=await service.getMsgElements({items:[${JSON.stringify(item)}]});
        return JSON.stringify(elements,(_,v)=>v instanceof Map?Object.fromEntries(v):v);
    })()`));

    const dispatch = async (action, payload) => {
        if (action === 'disconnect') {setTimeout(() => close(), 50); return {closing: true};}
        const self = identity();
        if (action === 'get_login_info') return self;
        if (action === 'get_friend_list') return friends();
        if (action === 'fetch_sys_faces') return contents.executeJavaScript(`(() => {
            const store=(${rendererServices}).find(v=>typeof v.getQQFaceLabel==='function');
            if(!store)throw new Error('QQ face catalog unavailable');
            const emojis=Object.entries(store.dataMap).filter(([id])=>/^\\d+$/.test(id))
                .map(([id,v])=>({q_sid:id,q_des:v.describe||v.name||''}));
            return {packs:[{emojis}]};
        })()`);
        if (action === 'diagnostics') return {account: self.user_id, window_id: peerId, session, sequence, pending: pending.size, lastFailure};
        if (action === 'prepare_image' || action === 'set_avatar') {
            const raw=Buffer.from(payload.base64,'base64');
            if(!raw.length || raw.length>6*1024*1024 || !['png','jpg','gif','webp'].includes(payload.extension))throw new Error('Invalid image');
            const size=require('electron').nativeImage.createFromBuffer(raw).getSize();
            if(!size.width || size.width*size.height>12000000)throw new Error('Invalid image dimensions');
            const fs=require('fs'),path=require('path');
            const folder=path.join(require('os').tmpdir(),'miko-qq-bridge',config.account);
            fs.mkdirSync(folder,{recursive:true});
            const file=path.join(folder,crypto.createHash('sha256').update(raw).digest('hex')+'.'+payload.extension);
            fs.writeFileSync(file,raw);
            if(action==='set_avatar') {
                try {
                    const result=await invoke('nodeIKernelProfileService/setHeader',[{path:file}]);
                    if(result?.result!==0)throw new Error('QQ avatar update failed: '+JSON.stringify(result));
                    return {updated:true};
                } finally {fs.unlinkSync(file);}
            }
            const elements=await prepare({type:'pic',src:file});
            if(!elements[0]?.picElement?.sourcePath)throw new Error('QQ could not prepare image');
            if(elements[0].picElement.sourcePath!==file)fs.unlinkSync(file);
            return elements;
        }
        if (action === 'prepare_reply') {
            const message=await dispatch('get_native_message',payload);
            return prepare({type:'reply',reply:message});
        }
        if (action === 'get_native_message' || action === 'read_image') {
            const friend=(await friends()).find(f=>String(f.user_id)===String(payload.user_id));
            if (!friend) throw new Error('Not a QQ friend');
            const reply=await invoke('nodeIKernelMsgService/getMsgsByMsgId',[
                {peer:{chatType:1,peerUid:friend.uid,guildId:''},msgIds:[String(payload.native_id)]}]);
            const message=reply?.msgList?.find(m=>m.msgId===String(payload.native_id));
            if (!message) throw new Error('QQ message unavailable');
            if(action==='get_native_message')return message;
            const element=message.elements.find(e=>e.elementId===String(payload.element_id));
            if(!element?.picElement)throw new Error('Not an image');
            const fs=require('fs');const image=element.picElement;
            if(!image.sourcePath || Number(image.fileSize)>6*1024*1024)throw new Error('Image unavailable or too large');
            if(!fs.existsSync(image.sourcePath)) {
                await invoke('nodeIKernelMsgService/downloadRichMedia',[{getReq:{
                    fileModelId:'',downSourceType:0,triggerType:1,msgId:message.msgId,
                    chatType:1,peerUid:friend.uid,elementId:element.elementId,
                    thumbSize:0,downloadType:1,filePath:image.sourcePath}}]);
                const deadline=Date.now()+10000;
                while(Date.now()<deadline && (!fs.existsSync(image.sourcePath) || !fs.statSync(image.sourcePath).size))await new Promise(r=>setTimeout(r,100));
                await new Promise(r=>setTimeout(r,300));
            }
            const bytes=fs.readFileSync(image.sourcePath);
            // QQ may decode/re-encode downloaded originals, changing size and MD5.
            // The Python receiver validates the actual image bytes before use.
            if(!bytes.length || bytes.length>6*1024*1024)throw new Error('Image unavailable or too large');
            return {base64:bytes.toString('base64')};
        }
        if (action === 'send_native') {
            if (config.test_targets && !config.test_targets.includes(String(payload.user_id))) throw new Error('Target not enabled for bridge test');
            const friend = (await friends()).find(f => String(f.user_id) === String(payload.user_id));
            if (!friend) throw new Error('Not a QQ friend');
            if(sends.has(friend.uid))throw new Error('Send already in progress for this friend');
            let resolve;const receipt=new Promise(r=>resolve=r);
            sends.set(friend.uid,{resolve,fingerprint:fingerprint(payload.elements),id:null});
            let timer;
            try {
                const accepted=await invoke('nodeIKernelMsgService/sendMsg', [{msgId: '0',
                    peer: {chatType: 1, peerUid: friend.uid, guildId: ''},
                    msgElements: payload.elements.map(element => element.picElement
                        ? {...element, picElement: {...element.picElement,
                            thumbPath: new Map(Object.entries(element.picElement.thumbPath || {}).map(([k,v])=>[Number(k),v]))}}
                        : element), msgAttributeInfos: new Map()}]);
                if(accepted?.result!==0)throw new Error('QQ rejected send: '+JSON.stringify(accepted));
                return await Promise.race([receipt,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Send receipt timeout')),12000);})]);
            } finally {clearTimeout(timer);sends.delete(friend.uid);}
        }
        throw new Error('Unsupported bridge action');
    };

    const authorized = request => {
        const actual=Buffer.from(request.headers.authorization || '');
        const expected=Buffer.from('Bearer ' + config.token);
        return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
    };
    const server = http.createServer(async (request, response) => {
        if (!authorized(request)) { response.writeHead(401).end(); return; }
        if (closed) {response.writeHead(503).end();return;}
        if (request.method === 'GET' && request.url.startsWith('/events?after=')) {
            const params=new URL(request.url,'http://localhost').searchParams;
            const after=Number(params.get('after'));
            if(params.has('session') && params.get('session')!==session){response.writeHead(409).end();return;}
            if (!Number.isSafeInteger(after) || after < 0) {response.writeHead(400).end();return;}
            if (eventBuffer.length && after < eventBuffer[0].sequence-1) {response.writeHead(409).end();return;}
            response.writeHead(200, {'Content-Type':'application/x-ndjson','Cache-Control':'no-store'});
            response.flushHeaders();
            for(const event of eventBuffer) if(event.sequence>after) response.write(JSON.stringify(event)+'\n');
            streams.add(response); request.on('close',()=>streams.delete(response)); return;
        }
        if (request.method !== 'POST' || request.url !== '/rpc') {response.writeHead(404).end();return;}
        try {
            const chunks=[];let size=0;
            for await(const chunk of request) {
                size+=chunk.length;if(size>9*1024*1024)throw new Error('Request too large');chunks.push(chunk);
            }
            const {action,payload={}}=JSON.parse(Buffer.concat(chunks).toString());
            const result=await dispatch(action,payload);
            response.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({result}));
        } catch (error) {
            lastFailure = String(error).slice(0,500);
            response.writeHead(502,{'Content-Type':'application/json'}).end(JSON.stringify({error:'QQ bridge request failed'}));
        }
    });
    const heartbeat=setInterval(()=>publish('bridge/heartbeat',{online:String(globalThis.authData?.uin)===config.account}),15000);
    const close = async () => {
        closed=true; clearInterval(heartbeat);
        if (!contents.isDestroyed() && contents.send === wrappedSend) contents.send=originalSend;
        for(const item of pending.values()){clearTimeout(item.timer);item.reject(new Error('Bridge stopped'));}
        pending.clear();for(const stream of streams)stream.end();streams.clear();
        await new Promise(resolve=>server.close(resolve));
        delete globalThis.__mikoClientBridge;
    };
    globalThis.__mikoClientBridge={close};
    try {await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(config.port,'127.0.0.1',resolve);});}
    catch(error){await close();throw error;}
    return {account:config.account,port:config.port,window_id:peerId};
})()
