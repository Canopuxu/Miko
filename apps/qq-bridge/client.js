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
        ? ([1,2].includes(e.textElement.atType)
            ? ['at',e.textElement.atType,String(e.textElement.atUid)]
            : ['text',e.textElement.content]) : e.faceElement ? ['face',e.faceElement.faceIndex]
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
            uid: globalThis.authData.uid || '', capabilities: {...selected.capabilities, message_reactions:true}, transport: connection.kind, adapter_hash: config.adapter_hash};
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
                if (![1,2].includes(message.chatType)) continue;
                const send = sends.get(message.chatType+':'+message.peerUid);
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

    const invoke = (cmdName, payload, confirmed = null) => new Promise((resolve, reject) => {
        identity();
        const id = 'miko-' + crypto.randomUUID();
        // A send has its own receipt deadline. Its verified success event may
        // arrive before (or without) the method callback, especially for media.
        const timer = confirmed ? null : setTimeout(() => {pending.delete(id); reject(new Error('QQ native timeout'));}, 12000);
        pending.set(id, {resolve, reject, timer});
        if(confirmed) {
            const finish = error => {
                if(!pending.delete(id))return;
                if(error)reject(error);else resolve({result:0});
            };
            confirmed.then(()=>finish(),finish);
        }
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

    const groups = async () => contents.executeJavaScript(`(() => {
        const store=(${rendererServices}).find(v=>Array.isArray(v.groupList));
        if(!store)throw new Error('QQ group list unavailable');
        return store.groupList.map(v=>({group_id:String(v.groupCode),group_name:v.groupName||String(v.groupCode)}));
    })()`);
    const peer = async payload => {
        if(payload.group_id) {
            if(!(await groups()).some(g=>g.group_id===String(payload.group_id)))throw new Error('Not a joined QQ group');
            return {chatType:2,peerUid:String(payload.group_id),guildId:''};
        }
        const friend=(await friends()).find(f=>String(f.user_id)===String(payload.user_id));
        if(!friend)throw new Error('Not a QQ friend');
        return {chatType:1,peerUid:friend.uid,guildId:''};
    };

    const dispatch = async (action, payload) => {
        if (action === 'disconnect') {setTimeout(() => close(), 50); return {closing: true};}
        const self = identity();
        if (action === 'get_login_info') return self;
        if (action === 'get_friend_list') return friends();
        if (action === 'get_group_list') return groups();
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
            // Use dimensions from the existing Pillow validation. Electron's
            // nativeImage cannot decode all formats QQ accepts (GIF/WebP).
            const [width,height]=payload.dimensions || [];
            if(!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
                || width<=0 || height<=0 || width*height>12000000)throw new Error('Invalid image dimensions');
            const fs=require('fs'),path=require('path');
            if(action==='set_avatar') {
                const folder=path.join(require('os').tmpdir(),'miko-qq-bridge',config.account);
                fs.mkdirSync(folder,{recursive:true});
                const file=path.join(folder,crypto.createHash('sha256').update(raw).digest('hex')+'.'+payload.extension);
                fs.writeFileSync(file,raw);
                try {
                    const result=await invoke('nodeIKernelProfileService/setHeader',[{path:file}]);
                    if(result?.result!==0)throw new Error('QQ avatar update failed: '+JSON.stringify(result));
                    return {updated:true};
                } finally {fs.unlinkSync(file);}
            }
            const md5HexStr=crypto.createHash('md5').update(raw).digest('hex');
            const fileName=md5HexStr+'.'+payload.extension;
            const sourcePath=await invoke('nodeIKernelMsgService/getRichMediaFilePathForGuild',[{path_info:{
                md5HexStr,fileName,elementType:2,elementSubType:0,thumbSize:0,
                needCreate:true,downloadType:1,file_uuid:''}}]);
            if(typeof sourcePath!=='string' || !path.isAbsolute(sourcePath))throw new Error('QQ image cache path unavailable');
            fs.writeFileSync(sourcePath,raw);
            return [{elementType:2,elementId:'',picElement:{md5HexStr,fileName,sourcePath,
                picWidth:width,picHeight:height,fileSize:String(raw.length),original:true,
                picType:{jpg:1000,png:1001,webp:1002,gif:2000}[payload.extension],picSubType:0,summary:''}}];
        }
        if (action === 'prepare_reply') {
            const message=await dispatch('get_native_message',payload);
            if(!/^[1-9]\d*$/.test(String(message.msgSeq))
                || message.elements?.some(e=>e.grayTipElement?.revokeElement))throw new Error('Quoted message unavailable');
            return [{elementType:7,elementId:'',replyElement:{
                replayMsgId:message.msgId,replayMsgSeq:String(message.msgSeq)}}];
        }
        if (action === 'set_reaction') {
            if (!payload.group_id || typeof payload.set!=='boolean' || !['128077','128170','10068','128557'].includes(String(payload.emoji_id))) throw new Error('Invalid message reaction');
            const target=await peer(payload);
            const message=await dispatch('get_native_message',payload);
            const seq=String(message.msgSeq || '');
            if(message.chatType!==2 || !/^[1-9]\d*$/.test(seq) || seq!==String(payload.message_seq)
                || message.elements?.some(e=>e.grayTipElement?.revokeElement))throw new Error('Reaction target changed');
            const result=await invoke('nodeIKernelMsgService/setMsgEmojiLikes',[
                {peer:target,msgSeq:seq,emojiId:String(payload.emoji_id),emojiType:'2',setEmoji:payload.set}]);
            if(result?.result!==0)throw new Error('QQ rejected message reaction: '+JSON.stringify(result));
            return {accepted:true,message_seq:seq};
        }
        if (action === 'get_native_message' || action === 'read_image') {
            const target=await peer(payload);
            const reply=await invoke('nodeIKernelMsgService/getMsgsByMsgId',[
                {peer:target,msgIds:[String(payload.native_id)]}]);
            const message=reply?.msgList?.find(m=>m.msgId===String(payload.native_id));
            if (!message) throw new Error('QQ message unavailable');
            if(action==='get_native_message')return message;
            const element=message.elements.find(e=>e.elementId===String(payload.element_id));
            if(!element?.picElement && !element?.marketFaceElement)throw new Error('Not an image');
            const fs=require('fs');const image=element.picElement;
            if(element.marketFaceElement) {
                const face=element.marketFaceElement;
                const path=[face.dynamicFacePath,face.staticFacePath].find(path=>path && fs.existsSync(path));
                if(!path || fs.statSync(path).size>6*1024*1024)throw new Error('Market face cache unavailable');
                return {base64:fs.readFileSync(path).toString('base64')};
            }
            if(!image.sourcePath || Number(image.fileSize)>6*1024*1024)throw new Error('Image unavailable or too large');
            if(!fs.existsSync(image.sourcePath)) {
                await invoke('nodeIKernelMsgService/downloadRichMedia',[{getReq:{
                    fileModelId:'',downSourceType:0,triggerType:1,msgId:message.msgId,
                    chatType:target.chatType,peerUid:target.peerUid,elementId:element.elementId,
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
            const target=await peer(payload), sendKey=target.chatType+':'+target.peerUid;
            if(sends.has(sendKey))throw new Error('Send already in progress for this chat');
            let resolve,reject;const receipt=new Promise((ok,fail)=>{resolve=ok;reject=fail;});
            sends.set(sendKey,{resolve,fingerprint:fingerprint(payload.elements),id:null});
            const timer=setTimeout(()=>reject(new Error('Send receipt timeout')),45000);
            try {
                const accepted=await invoke('nodeIKernelMsgService/sendMsg', [{msgId: '0',
                    peer: target,
                    msgElements: payload.elements.map(element => element.picElement
                        ? {...element, picElement: {...element.picElement,
                            thumbPath: new Map(Object.entries(element.picElement.thumbPath || {}).map(([k,v])=>[Number(k),v]))}}
                        : element), msgAttributeInfos: new Map()}],receipt);
                if(accepted?.result!==0)throw new Error('QQ rejected send: '+JSON.stringify(accepted));
                return await receipt;
            } finally {clearTimeout(timer);sends.delete(sendKey);}
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
