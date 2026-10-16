const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const folder = path.join(__dirname, '../apps/qq-bridge');
const source = fs.readFileSync(path.join(folder, 'compat.js'), 'utf8');
function documentFor(provides, {renamed=false, route='/main/message', appContext=false}={}) {
    const component = {provides};
    const app = {config:{globalProperties:{$route:{path:route}}},
        _container:{_vnode:{component:{subTree:{component}}}}};
    if (appContext) {
        app._context={provides};delete component.provides;
    }
    const element={__vue_app__:app};
    return {querySelector:selector=>selector==='#app'&&!renamed?element:null,
        querySelectorAll:selector=>selector==='*'||(!renamed&&selector==='[data-v-app]')?[element]:[]};
}
function context(document) {return {document,Map,Set,Reflect,Object};}
function compatible(document) {return vm.runInNewContext(source,context(document));}
function buddy() {return {buddyMap:new Map([[123,{uin:123,uid:'u_123'}]])};}

test('finds current nested service bindings',()=>{
    const friends=buddy(), media={getMsgElements(){}}, faces={getQQFaceLabel(){}};
    const provider={qq_container__vue:{container:{_bindingDictionary:{_map:new Map([
        ['friends',[{cache:friends}]],['media',[{cache:media}]],['faces',[{cache:faces}]]])}}}};
    const compat=compatible(documentFor(provider));
    const environment=compat.renderer();
    assert.equal(environment.services.length,3);
    assert.deepEqual({...compat.capabilities(environment)},{friends:true,send_images:true,faces:true});
});

test('finds renamed mount, symbol provider and changed container shape',()=>{
    const provider={[Symbol('renamed-provider')]:{registry:new Map([['buddy',buddy()],
        ['composer',{getMsgElements(){}}]])}};
    const compat=compatible(documentFor(provider,{renamed:true,route:'/workspace',appContext:true}));
    assert.equal(compat.capabilities(compat.renderer()).friends,true);
    assert.equal(compat.capabilities(compat.renderer()).send_images,true);
});

test('prioritizes live DI bindings over large unrelated page graphs',()=>{
    const friends=buddy();
    const provider={router:{children:Array.from({length:13000},()=>({route:{}}))},
        currentContainer:{container:{_bindingDictionary:{_map:new Map([
            ['friends',[{cache:friends}]],['media',[{cache:{getMsgElements(){}}}]],
            ['faces',[{cache:{getQQFaceLabel(){}}}]]])}}}};
    const document=documentFor(provider);
    const root=document.querySelector('#app').__vue_app__._container._vnode;
    root.children=Array.from({length:1500},()=>({component:{provides:provider}}));
    const compat=compatible(document);
    assert.deepEqual({...compat.capabilities(compat.renderer())},
        {friends:true,send_images:true,faces:true});
});

test('reads inherited providers without evaluating unrelated getters',()=>{
    const inherited={renamedContainer:{entries:[{singleton:buddy()}]}};
    Object.defineProperty(inherited.renamedContainer,'unrelated',{get(){throw new Error('must not run');}});
    const compat=compatible(documentFor(Object.create(inherited)));
    assert.equal(compat.capabilities(compat.renderer()).friends,true);
});

test('finds the AIO image builder exposed through component accessors',()=>{
    const media={getMsgElements(){return 'native builder';}};
    const document=documentFor({friends:buddy()});
    const state={};
    Object.defineProperty(state,'messageSendService',{get:()=>({get msgService(){return media;}})});
    Object.defineProperty(state,'unrelated',{get(){throw new Error('must not run');}});
    document.querySelector('#app').__vue_app__._instance={setupState:state};
    const compat=compatible(document);
    assert.equal(compat.capabilities(compat.renderer()).send_images,true);
    assert.equal(compat.renderer().services.find(v=>v.getMsgElements).getMsgElements(),'native builder');
});

test('resolves AIO getter descriptors without invoking unrelated state accessors',()=>{
    const media={getMsgElements(){}};
    const state={messageSendService:{get:()=>({msgService:media}),set:undefined}};
    Object.defineProperty(state,'unrelated',{get(){throw new Error('must not run');}});
    const document=documentFor({friends:buddy()});
    document.querySelector('#app').__vue_app__._instance={ctx:state};
    const compat=compatible(document);
    assert.equal(compat.capabilities(compat.renderer()).send_images,true);
});

function electronFor(windows) {
    return {BrowserWindow:{getAllWindows:()=>windows.map(row=>({webContents:{id:row.id,
        isDestroyed:()=>false,getURL:()=>row.url,
        executeJavaScript:code=>Promise.resolve(vm.runInNewContext(code,context(row.document)))}}))},
        ipcMain:{listenerCount:channel=>windows.some(row=>channel===`RM_IPCFROM_RENDERER${row.id}`)?1:0}};
}

test('locates by capabilities despite changed renderer URL and route',async()=>{
    const document=documentFor({renamed:buddy()},{route:'/conversation'});
    const compat=compatible(document);
    const view=await compat.locate(electronFor([{id:25,url:'file:///new/path/shell.html',document}]));
    assert.equal(view.contents.id,25);
    assert.equal(view.capabilities.friends,true);
    assert.equal(view.capabilities.send_images,false);
});

test('prefers unique main view and rejects ambiguous contexts',async()=>{
    const document=documentFor({friends:buddy()});const compat=compatible(document);
    const windows=[{id:1,document},{id:2,document:documentFor({friends:buddy()},{route:'/settings'})}];
    assert.equal((await compat.locate(electronFor(windows))).contents.id,1);
    windows[0].document=documentFor({friends:buddy()},{route:'/unknown'});
    await assert.rejects(()=>compat.locate(electronFor(windows)),/MIKO_UI_AMBIGUOUS/);
});

test('account discovery permits text when optional image preparation is absent',async()=>{
    const document=documentFor({friends:buddy()});
    const script=fs.readFileSync(path.join(folder,'discover.js'),'utf8').replace('__MIKO_COMPAT__',source);
    const electron={...electronFor([{id:1,document}]),app:{getVersion:()=> 'fixture'}};
    const result=await vm.runInNewContext(script,{...context(document),authData:{uin:'12345'},
        process:{pid:12,execPath:'Q:/QQ.exe',mainModule:{require:()=>electron}}});
    assert.equal(result.state,'ready');assert.equal(result.capabilities.send_images,false);
    assert.equal(result.account,'12345');
});

test('recognizes both documented older IPC channels and prefers the current renderer channel',async()=>{
    const document=documentFor({friends:buddy()});
    const compat=compatible(document);
    for (const number of [2,3]) {
        const electron=electronFor([{id:8,document}]);
        electron.ipcMain.listenerCount=name=>name===`IPC_UP_${number}`?1:0;
        const view=await compat.locate(electron);
        assert.equal(view.connection.channel,`IPC_UP_${number}`);
        assert.equal(view.connection.kind,'legacy');
    }
    assert.equal(compat.transport({listenerCount:()=>1},8).kind,'renderer');
});

test('uses documented legacy namespace and argument envelope without changing native method arguments',()=>{
    const compat=compatible(documentFor({}));
    const payload=[{peer:{chatType:1,peerUid:'u_friend'},msgIds:['55']}];
    const [meta,args]=compat.request({kind:'legacy',number:3},'callback','nodeIKernelMsgService/getMsgsByMsgId',payload,8);
    assert.equal(meta.eventName,'ns-ntApi-3');assert.equal(meta.callbackId,'callback');
    assert.equal(args[0],'nodeIKernelMsgService/getMsgsByMsgId');assert.equal(args[1],payload[0]);
    assert.equal(args.length,2);
});

test('decodes legacy notification batches separately from method replies',()=>{
    const compat=compatible(documentFor({}));
    const batch=[{cmdName:'nodeIKernelMsgListener/onRecvMsg',payload:{msgList:[]}},
        {cmdName:'nodeIKernelMsgListener/onMsgInfoListUpdate',payload:{msgList:[]}}];
    assert.equal(compat.notifications({kind:'legacy'},'IPC_DOWN_2',{},batch),batch);
    assert.equal(compat.notifications({kind:'legacy'},'IPC_DOWN_2',{callbackId:'reply'},batch).length,0);
    assert.equal(compat.notifications({kind:'legacy'},'unrelated',{},batch).length,0);
});

test('native loader locates complete exported API without a fixed DLL filename',()=>{
    const required=['napi_open_handle_scope','napi_close_handle_scope','napi_create_string_utf8',
        'napi_run_script','napi_get_value_string_utf8','napi_get_and_clear_last_exception','napi_get_cb_info'];
    const module={name:'renamed-wrapper.node',findExportByName:name=>required.includes(name)?name:null,
        getExportByName:name=>{assert.ok(required.includes(name));return name;}};
    const attached=[];const messages=[];
    vm.runInNewContext(fs.readFileSync(path.join(folder,'loader.js'),'utf8'),{
        Process:{enumerateModules:()=>[module]},__MIKO_PAYLOAD__:'test',
        NativeFunction:function(){return ()=>0;},Interceptor:{attach:(address)=>{attached.push(address);return {detach(){}};}},
        send:message=>messages.push(message)});
    assert.deepEqual(attached,['napi_get_cb_info']);assert.equal(messages[0].ready,true);
});

test('native loader reports missing exported API before attaching',()=>{
    assert.throws(()=>vm.runInNewContext(fs.readFileSync(path.join(folder,'loader.js'),'utf8'),{
        Process:{enumerateModules:()=>[{name:'QQ.exe',findExportByName:()=>null}]}}),/MIKO_NAPI_UNAVAILABLE/);
});
