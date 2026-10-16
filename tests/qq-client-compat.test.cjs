// These fixtures exercise public IPC contracts, not other installed QQ releases.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const folder = path.join(__dirname, '../apps/qq-bridge');
const compat = fs.readFileSync(path.join(folder, 'compat.js'), 'utf8');

async function fixture(t, kind) {
    const number=kind==='legacy3'?3:2, legacy=kind!=='renderer';
    const channel=legacy?`IPC_UP_${number}`:'RM_IPCFROM_RENDERER8';
    const calls=[],writes=[],forwarded=[],records=new Map(),files=new Map();
    const provider={builder:{getMsgElements:({items})=>items.map(item=>item.type==='pic'
        ? {picElement:{sourcePath:item.src}} : {replyElement:{fixture:true}})},groups:{groupList:[{groupCode:'900',groupName:'朋友群'}]},friends:{buddyMap:new Map([
        ['u_friend',{coreInfo:{uin:'67890',uid:'u_friend',nick:'新版好友',remark:'备注'}}],
        ['u_older',{uin:'67891',uid:'u_older',nick:'旧版好友',remark:'旧备注'}]])}};
    const document={querySelector:()=>({__vue_app__:{
        config:{globalProperties:{$route:{path:'/main/message'}}},_context:{provides:provider}}}),
        querySelectorAll:()=>[]};
    const rendererContext=vm.createContext({document,Map,Set,Object,Reflect});
    const contents={id:8,mainFrame:{},isDestroyed:()=>false,
        send:(...args)=>forwarded.push(args),
        executeJavaScript:code=>Promise.resolve(vm.runInContext(code,rendererContext))};
    const notify=batch=>contents.send(legacy?`IPC_DOWN_${number}`:'RM_IPCTO_RENDERER8',
        {type:'request'},legacy?batch:batch[0]);
    const ipcMain={listenerCount:name=>name===channel?1:0,emit:(name,event,meta,body)=>{
        assert.equal(name,channel);assert.equal(event.sender,contents);
        assert.equal(meta.eventName,legacy?`ns-ntApi-${number}`:'ntApi');
        const cmd=legacy?body[0]:body.cmdName;
        const args=legacy?body.slice(1):body.payload.slice(0,-1);
        calls.push({cmd,args});
        if(cmd==='nodeIKernelMsgService/getMsgsByMsgId') {
            contents.send('reply',{callbackId:meta.callbackId,...(!legacy?{type:'response',promiseStatue:'full'}:{})},
                {msgList:[records.get(args[0].msgIds[0]) || {msgId:args[0].msgIds[0],chatType:1}]});
        } else if(cmd==='nodeIKernelMsgService/sendMsg') {
            const record={msgId:'901',chatType:args[0].peer.chatType,peerUid:args[0].peer.peerUid,
                peerUin:args[0].peer.chatType===2?'900':'67890',senderUin:'12345',
                msgTime:'1792139000',elements:args[0].msgElements.map(element=>element.textElement?.atType===2
                    ? {...element,textElement:{...element.textElement,content:element.textElement.content.trimEnd()}}
                    : element)};
            contents.send('reply',{callbackId:meta.callbackId,...(!legacy?{type:'response',promiseStatue:'full'}:{})},{result:0});
            queueMicrotask(()=>{
                notify([{cmdName:'nodeIKernelMsgListener/onAddSendMsg',payload:{msgList:[{...record,sendStatus:1}]}}]);
                notify([{cmdName:'nodeIKernelMsgListener/onMsgInfoListUpdate',payload:{msgList:[{...record,sendStatus:2}]}}]);
            });
        } else if(cmd==='nodeIKernelMsgService/setMsgEmojiLikes') {
            contents.send('reply',{callbackId:meta.callbackId,...(!legacy?{type:'response',promiseStatue:'full'}:{})},{result:0});
        } else throw new Error('unexpected command '+cmd);
        return true;
    }};
    let handler;
    const server={once(){return this;},listen(port,address,resolve){assert.equal(address,'127.0.0.1');resolve();},
        close:resolve=>resolve()};
    const electron={ipcMain,BrowserWindow:{getAllWindows:()=>[{webContents:contents}]},app:{getVersion:()=> 'fixture'}};
    const source=fs.readFileSync(path.join(folder,'client.js'),'utf8')
        .replace('__MIKO_CONFIG__',JSON.stringify({account:'12345',token:'fixture-key',port:17971}))
        .replace('__MIKO_COMPAT__',compat);
    const context={authData:{uin:'12345'},Buffer,URL,Map,Set,Reflect,Object,JSON,
        setTimeout,clearTimeout,setInterval,clearInterval,
        process:{pid:42,mainModule:{require:name=>name==='electron'?electron:name==='crypto'?crypto:
            name==='http'?{createServer:callback=>{handler=callback;return server;}}:
            name==='fs'?{existsSync:path=>files.has(path),statSync:path=>({size:files.get(path).length}),
                readFileSync:path=>files.get(path),mkdirSync(){},writeFileSync:(path,raw)=>files.set(path,raw),
                unlinkSync:path=>files.delete(path)}:name==='path'?path:name==='os'?{tmpdir:()=>'/fixture-temp'}:null}}};
    await vm.runInNewContext(source,context);
    t.after(()=>context.__mikoClientBridge.close());
    async function rpc(action,payload={}) {
        let result,status;
        const request={method:'POST',url:'/rpc',headers:{authorization:'Bearer fixture-key'},
            async *[Symbol.asyncIterator](){yield Buffer.from(JSON.stringify({action,payload}));}};
        const response={writeHead(code){status=code;return this;},end(body){result=JSON.parse(body);}};
        await handler(request,response);
        assert.equal(status,200,result?.error);
        return result.result;
    }
    await handler({method:'GET',url:'/events?after=0',headers:{authorization:'Bearer fixture-key'},on(){}},
        {writeHead(){return this;},flushHeaders(){},write(line){writes.push(JSON.parse(line));return true;},end(){}});
    return {rpc,notify,calls,writes,forwarded,records,files,provider};
}

test('market face image reads QQ cached resource without accepting a caller file path',async t=>{
    const bridge=await fixture(t,'renderer');
    bridge.records.set('face',{msgId:'face',chatType:2,elements:[{elementId:'11',marketFaceElement:{
        dynamicFacePath:'QQ/cache/face.gif',staticFacePath:'QQ/cache/face.png'}}]});
    bridge.files.set('QQ/cache/face.gif',Buffer.from('fixture-image-bytes'));
    const result=await bridge.rpc('read_image',{group_id:'900',native_id:'face',element_id:'11'});
    assert.equal(Buffer.from(result.base64,'base64').toString(),'fixture-image-bytes');
});

test('prepares the original GIF without relying on Electron GIF decoding or flattening frames',async t=>{
    const bridge=await fixture(t,'renderer');
    const raw=Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7','base64');
    const elements=await bridge.rpc('prepare_image',{base64:raw.toString('base64'),extension:'gif'});
    assert.ok(elements[0].picElement.sourcePath.endsWith('.gif'));
    assert.deepEqual(bridge.files.get(elements[0].picElement.sourcePath),raw);
});

for(const kind of ['renderer','legacy2','legacy3']) {
    test(`${kind}: actual adapter handles friend formats, message reads, incoming events and send receipts`,async t=>{
        const bridge=await fixture(t,kind);
        const friends=await bridge.rpc('get_friend_list');
        assert.equal(friends[0].user_id,67890);assert.equal(friends[0].uid,'u_friend');
        assert.equal(friends[0].nickname,'新版好友');assert.equal(friends[1].nickname,'旧版好友');
        const read=await bridge.rpc('get_native_message',{user_id:'67890',native_id:'55'});
        assert.equal(read.msgId,'55');
        const first={cmdName:'nodeIKernelMsgListener/onRecvActiveMsg',payload:{msgList:[{msgId:'41',chatType:1}]}},
            second={cmdName:'nodeIKernelMsgListener/onActiveMsgInfoUpdate',payload:{msgRecord:{msgId:'42',chatType:1}}};
        if(kind==='renderer'){bridge.notify([first]);bridge.notify([second]);}
        else bridge.notify([first,second]);
        assert.deepEqual(bridge.writes.map(value=>value.payload.msgList[0].msgId),['41','42']);
        assert.ok(bridge.forwarded.length>0,'normal QQ UI notifications must still be delivered');
        const sent=await bridge.rpc('send_native',{user_id:'67890',elements:[{textElement:{content:'测试'}}]});
        assert.equal(sent.msgId,'901');assert.equal(sent.sendStatus,2);
        assert.equal(bridge.calls.filter(value=>value.cmd.endsWith('/sendMsg')).length,1);
        assert.equal(bridge.calls[0].args[0].peer.peerUid,'u_friend');
        assert.equal(bridge.calls[1].args[0].msgElements[0].textElement.content,'测试');
        const groups=await bridge.rpc('get_group_list');
        assert.equal(groups[0].group_id,'900');
        const grouped=await bridge.rpc('send_native',{group_id:'900',elements:[{textElement:{content:'群聊测试'}}]});
        assert.equal(grouped.sendStatus,2);
        assert.equal(bridge.calls[2].args[0].peer.chatType,2);
        assert.equal(bridge.calls[2].args[0].peer.peerUid,'900');
        const mentioned=await bridge.rpc('send_native',{group_id:'900',elements:[
            {textElement:{content:'@测试成员 ',atType:2,atUid:'67890',atNtUid:'u_friend'}},
            {textElement:{content:'晚饭还没想好'}}]});
        assert.equal(mentioned.sendStatus,2,'QQ may normalize mention display text before returning its receipt');
        bridge.records.set('reaction-source',{msgId:'reaction-source',chatType:2,msgSeq:'101',elements:[]});
        const reaction=await bridge.rpc('set_reaction',{group_id:'900',native_id:'reaction-source',message_seq:'101',emoji_id:'128077',set:true});
        assert.equal(reaction.accepted,true);
        const command=bridge.calls.find(value=>value.cmd.endsWith('/setMsgEmojiLikes'));
        assert.equal(command.args[0].peer.chatType,2);assert.equal(command.args[0].peer.peerUid,'900');
        assert.deepEqual(JSON.parse(JSON.stringify(command.args)),[{peer:{chatType:2,peerUid:'900',guildId:''},msgSeq:'101',emojiId:'128077',emojiType:'2',setEmoji:true}]);
        await bridge.rpc('set_reaction',{group_id:'900',native_id:'reaction-source',message_seq:'101',emoji_id:'128077',set:false});
        assert.equal(bridge.calls.filter(value=>value.cmd.endsWith('/setMsgEmojiLikes')).at(-1).args[0].setEmoji,false);
        bridge.records.get('reaction-source').msgSeq='102';
        await assert.rejects(()=>bridge.rpc('set_reaction',{group_id:'900',native_id:'reaction-source',message_seq:'101',emoji_id:'128077',set:true}));
        assert.equal(bridge.calls.filter(value=>value.cmd.endsWith('/setMsgEmojiLikes')).length,2,'stale sequence must not be dispatched');
    });
}
