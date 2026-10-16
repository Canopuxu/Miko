import { useContext, useEffect, useState } from 'react';
import { Check, LoaderCircle, Save, PlugZap, Unplug, RefreshCw, Users, ChevronDown, FolderOpen, ArrowUpRight } from 'lucide-react';
import { AppShell, NavigationContext } from '../components/AppShell';
import { FriendManager } from '../components/FriendManager';
import { getJson, responseError } from '../api/chat';
import { useRuntime } from '../components/RuntimeStatus';
import '../styles/settings.css';
import { workbenchEnabled } from '../features';
import { ModelFields, useModelCatalog, type Provider } from '../components/ModelFields';
import { DataBackups } from './DataBackups';

type Reranker = Provider;
type Qq = {account:string};
type Config = {provider:Provider; qq_provider:Provider|null; life_provider:Provider|null; memory_provider:Provider|null; rerank_shared:boolean; memory_model:string; life_model:string;
  embedding_model:string; embedding_provider:Provider|null; rerank_provider:Reranker|null; rerank_enabled:boolean; qq:Qq; configured:boolean};
type Application = {version:string; data_folder:string; logs_folder:string};
type QqClient = {pid:number; account:string; nickname:string; version:string; executable:string; state:string; note:string;
  capabilities?:{send_images?:boolean}};
type BridgeStatus = {state:string; note:string; account:string; version:string};
const cleanProvider = (p:Provider)=>({api_base:p.api_base.trim(),api_key:p.api_key,model:p.model.trim()});
const emptyProvider = ():Provider=>({api_base:'',api_key:'',model:''});
type TestResult = {state:'running'|'success'|'error'; message:string};
function TestFeedback({result}:{result?:TestResult}){
  if(!result)return null;
  return <span className={`settings-test-feedback ${result.state}`} role={result.state==='error'?'alert':'status'} aria-live="polite">
    {result.state==='running'?<LoaderCircle size={16} className="spinning"/>:result.state==='success'?<Check size={16}/>:null}{result.message}
  </span>;
}
export function SettingsPage(){
  const navigation=useContext(NavigationContext)!;
  const runtime=useRuntime();
  const [config,setConfig]=useState<Config|null>(null);
  const [application,setApplication]=useState<Application|null>(null);
  const [section,setSection]=useState<'models'|'qq'|'data'>(()=>new URLSearchParams(location.search).get('section')==='data'?'data':'models');
  const [busy,setBusy]=useState('');
  const [testResults,setTestResults]=useState<Record<string,TestResult>>({});
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [dirtySections,setDirtySections]=useState<string[]>([]);
  const [managingFriends,setManagingFriends]=useState(false);
  const [qqClients,setQqClients]=useState<QqClient[]>([]);
  const [selectedPid,setSelectedPid]=useState('');
  const [scanning,setScanning]=useState(false);
  const [scanError,setScanError]=useState('');
  const [scanned,setScanned]=useState(false);
  const [bridge,setBridge]=useState<BridgeStatus|null>(null);
  const dirty=dirtySections.length>0;
  async function load(){try{
    const [value,app]=await Promise.all([getJson<Config>('/api/settings'),getJson<Application>('/api/settings/application')]);
    if(!value.life_provider&&value.life_model&&value.life_model!==value.provider.model)value.life_provider={...value.provider,model:value.life_model};
    if(!value.memory_provider&&value.memory_model&&value.memory_model!==value.provider.model)value.memory_provider={...value.provider,model:value.memory_model};
    setConfig(value);setApplication(app);setError('');
  }catch(e){setError(String(e));}}
  useEffect(()=>{void load();},[]);
  async function scanQq(){
    setScanning(true);setScanError('');
    try{
      const result=await getJson<{clients:QqClient[];bridge:BridgeStatus|null}>('/api/settings/qq/accounts');
      setQqClients(result.clients);setBridge(result.bridge);setScanned(true);
      setSelectedPid(previous=>{
        if(result.clients.some(row=>String(row.pid)===previous&&row.state==='ready'))return previous;
        const ready=result.clients.filter(row=>row.state==='ready');
        const saved=ready.filter(row=>row.account===config?.qq.account);
        return saved.length===1?String(saved[0].pid):ready.length===1?String(ready[0].pid):'';
      });
    }catch(e){setQqClients([]);setSelectedPid('');setScanError(e instanceof Error?e.message:String(e));}
    finally{setScanning(false);}
  }
  useEffect(()=>{if(section==='qq')void scanQq();},[section]);
  useEffect(()=>{
    if(section!=='qq')return;
    let active=true;
    const update=()=>void getJson<{bridge:BridgeStatus|null}>('/api/settings/qq/connection')
      .then(value=>{if(active)setBridge(value.bridge);}).catch(()=>{});
    update();const timer=window.setInterval(update,3000);
    return()=>{active=false;window.clearInterval(timer);};
  },[section]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  function markDirty(){setTestResults({});setDirtySections(items=>items.includes(section)?items:[...items,section]);setNotice('');}
  function edit(value:Config){setConfig(value);markDirty();}
  async function action(name:string,path:string,body:unknown,method='POST',saved=false){
    const testing=name==='test'||name.startsWith('test-');
    setBusy(name);setError('');setNotice('');
    if(testing)setTestResults(current=>({...current,[name]:{state:'running',message:'正在测试…'}}));
    try{const response=await fetch('/api/settings'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));const result=await response.json();
      if(testing)setTestResults(current=>({...current,[name]:{state:'success',message:result.message}}));else setNotice(result.message);
      if(saved){const next=await getJson<Config>('/api/settings');setConfig(current=>current?(path==='/models'?{...next,qq:current.qq}:{...current,qq:next.qq}):next);setDirtySections(items=>items.filter(item=>item!==(path==='/models'?'models':'qq')));await runtime.refresh();}
    }catch(e){const message=e instanceof Error?e.message:String(e);
      if(testing)setTestResults(current=>({...current,[name]:{state:'error',message}}));else setError(message);
    }finally{setBusy('');}
  }
  const models=config?{provider:cleanProvider(config.provider),qq_provider:config.qq_provider?cleanProvider(config.qq_provider):null,
    life_provider:config.life_provider?cleanProvider(config.life_provider):null,
    memory_provider:config.memory_provider?cleanProvider(config.memory_provider):null,
    memory_model:config.memory_provider?.model??'',life_model:config.life_provider?.model??'',embedding_model:config.embedding_model,
    embedding_provider:config.embedding_provider?cleanProvider(config.embedding_provider):null,
    rerank_shared:config.rerank_shared,rerank_enabled:config.rerank_enabled,rerank_provider:config.rerank_provider?cleanProvider(config.rerank_provider):null,
    use_shared_model:!(config.qq_provider||config.life_provider||config.memory_provider)}:null;
  const selectedClient=qqClients.find(row=>String(row.pid)===selectedPid&&row.state==='ready');
  const qq=config?{account:selectedClient?.account??config.qq.account.trim(),
    ...(selectedClient?{client_pid:selectedClient.pid}:{})}:null;
  const qqConnected=runtime.qq?.receiver.state==='connected';
  const differentAccount=!!config?.qq.account&&!!selectedClient&&config.qq.account!==selectedClient.account;
  const embedding=config?.embedding_provider??(config?{...config.provider,model:config.embedding_model}:emptyProvider());
  const catalog=useModelCatalog(config?.provider??null,'provider');
  const reranker=config?.rerank_shared?{...config.provider,model:config.rerank_provider?.model??''}:config?.rerank_provider??emptyProvider();
  function chooseSection(next:typeof section){setSection(next);setNotice('');setError('');}
  return <AppShell view="settings" sidebar={<nav className="settings-navigation" aria-label="设置分类">
    <button className={section==='models'?'selected':''} onClick={()=>chooseSection('models')}>模型与 API</button>
    <button className={section==='qq'?'selected':''} onClick={()=>chooseSection('qq')}>QQ 连接</button>
    <button className={section==='data'?'selected':''} onClick={()=>chooseSection('data')}>数据与日志</button>
  </nav>}><main className="main-panel settings-panel"><header className="page-header"><h1>设置</h1><div className="settings-header-actions">{dirty&&<span>尚未保存</span>}{config&&section==='models'&&<button className="settings-save" disabled={!!busy} onClick={()=>void action('save-models','/models',models,'PUT',true)}><Save size={16}/>保存设置</button>}</div></header>
    <div className="settings-content">
      {error&&<p className="error-notice" role="alert">{error}</p>}{notice&&<p className="settings-notice" role="status"><Check size={16}/>{notice}</p>}
      {!config&&!error&&<p role="status">读取设置…</p>}{!config&&error&&<button onClick={()=>void load()}>重新加载</button>}
      {config&&<fieldset disabled={!!busy} className="settings-body">{section==='models'?<>
        <section className="settings-basic"><h2>聊天与日常</h2><p>下面的 API 和模型用于“聊天”，QQ 聊天、生活生成和记忆整理默认跟随它。</p>
          <ModelFields value={config.provider} change={provider=>edit({...config,provider})} catalog={catalog}/>
          <div className="settings-actions"><button onClick={()=>void action('test','/models/test',cleanProvider(config.provider))}><PlugZap size={16}/>测试聊天模型</button><TestFeedback result={testResults.test}/></div>
          <details className="settings-task-overrides"><summary>QQ 聊天、生活生成、记忆整理<ChevronDown size={16}/></summary>
            <div className="settings-task-list">
              {(['qq','life','memory'] as const).map(task=>{
                const key=task==='qq'?'qq_provider':task==='life'?'life_provider':'memory_provider';
                const label=task==='qq'?'QQ 聊天':task==='life'?'生活生成':'记忆整理';
                const value=config[key];
                return <div className="settings-task" key={task}><div className="settings-task-heading"><h3>{label}</h3>
                  <label className="settings-check"><input type="checkbox" checked={!!value} onChange={event=>edit({...config,[key]:event.target.checked?{...config.provider}:null})}/>单独设置</label></div>
                  {value?<><ModelFields value={value} target={task} change={next=>edit({...config,[key]:next})}/>
                    <div className="settings-actions"><button onClick={()=>void action('test-'+task,'/models/test?target='+task,cleanProvider(value))}><PlugZap size={16}/>测试{label}模型</button><TestFeedback result={testResults['test-'+task]}/></div>
                  </>:<p>跟随聊天 · {config.provider.model||'尚未选择模型'}</p>}
                </div>;
              })}
            </div>
          </details>
        </section>
        <section className="settings-search-section"><h2>记忆检索</h2><p>向量模型用于按意思查找资料和记忆。留空时使用关键词检索。</p>
          <label className="settings-check"><input type="checkbox" checked={!!config.embedding_provider} onChange={event=>edit({...config,embedding_provider:event.target.checked?{...config.provider,model:config.embedding_model}:null})}/>使用其他 API</label>
          <ModelFields kind="embedding" target="embedding" value={embedding} connection={!!config.embedding_provider} catalog={config.embedding_provider?undefined:catalog}
            change={value=>edit({...config,embedding_model:value.model,embedding_provider:config.embedding_provider?value:null})}/>
          {!config.embedding_provider&&<p className="settings-footnote">共用聊天 API 的地址和密钥，需要支持 OpenAI 兼容的 Embeddings 接口。</p>}
          <div className="settings-actions"><button disabled={!embedding.model.trim()} onClick={()=>void action('test-embedding','/models/test?target=embedding'+(config.embedding_provider?'':'&shared=true'),cleanProvider(embedding))}><PlugZap size={16}/>测试记忆检索</button><TestFeedback result={testResults['test-embedding']}/></div>
          <p className="settings-footnote">更换向量模型或 API 后会重建索引，原始记忆保留。</p>
        </section>
        <section className="settings-search-section"><div className="settings-task-heading"><h2>相关性筛选</h2>
          <label className="settings-check"><input type="checkbox" checked={config.rerank_enabled} onChange={event=>edit({...config,rerank_enabled:event.target.checked,rerank_provider:config.rerank_provider??emptyProvider()})}/>启用</label></div>
          <p>用重排模型进一步筛选检索结果。关闭后仍可正常聊天和读取记忆。</p>
          {config.rerank_enabled&&<><label className="settings-check"><input type="checkbox" checked={!config.rerank_shared} onChange={event=>edit({...config,rerank_shared:!event.target.checked})}/>使用其他 API</label>
            <ModelFields kind="rerank" target="rerank" value={reranker} connection={!config.rerank_shared} catalog={config.rerank_shared?catalog:undefined}
              change={value=>edit({...config,rerank_provider:config.rerank_shared?{...config.rerank_provider??emptyProvider(),model:value.model}:value})}/>
            {config.rerank_shared&&<p className="settings-footnote">共用聊天 API 的地址和密钥，自动连接对应的重排接口。</p>}
            <div className="settings-actions"><button onClick={()=>void action('test-rerank','/models/test?target=rerank'+(config.rerank_shared?'&shared=true':''),cleanProvider(reranker))}><PlugZap size={16}/>测试相关性筛选</button><TestFeedback result={testResults['test-rerank']}/></div>
          </>}
        </section>
        {workbenchEnabled&&<p className="settings-footnote">DSH 工作台的模型在其原生设置中管理。</p>}
      </>:section==='qq'?<>
        <section className="settings-basic"><h2>QQ 连接</h2><p>先在电脑上登录 QQ，再选择要让 Miko 参与聊天的账号。连接信息由程序自动准备。</p>
          <div className="qq-account-picker"><label htmlFor="qq-account">已登录的 QQ</label><div>
            <select id="qq-account" value={selectedPid} disabled={scanning} onChange={e=>{setSelectedPid(e.target.value);setNotice('');setError('');}}>
              <option value="">{scanning?'正在检测 QQ…':qqClients.some(row=>row.state==='ready')?'请选择账号':'未发现可连接的账号'}</option>
              {qqClients.map(row=><option key={row.pid} value={String(row.pid)} disabled={row.state!=='ready'}>
                {row.nickname?row.nickname+' · ':''}{row.account||'QQ 客户端'} · {row.state==='ready'?'已登录':row.note}{row.version?' · '+row.version:''}
              </option>)}
            </select><button disabled={scanning} onClick={()=>void scanQq()}>{scanning?<LoaderCircle size={16} className="spinning"/>:<RefreshCw size={16}/>}重新检测</button>
          </div></div>
          {scanError&&<p className="error-notice" role="alert">{scanError}</p>}
          {!scanning&&scanned&&!qqClients.length&&!scanError&&<p role="status">未发现运行中的 QQ。请启动电脑版 QQ、完成登录，再点击重新检测。</p>}
          {qqClients.filter(row=>row.state!=='ready').map(row=><p key={row.pid} className="settings-footnote">{row.account||'QQ 客户端'}{row.version?'（'+row.version+'）':''}：{row.note}</p>)}
          {selectedClient?.capabilities?.send_images===false&&<p role="status">此 QQ 客户端可连接文字聊天，图片和表情包发送暂不可用。</p>}
          <div className="settings-actions"><button className="settings-primary" disabled={!selectedClient||scanning||differentAccount} onClick={()=>void action('save-qq','/qq',qq,'PUT',true)}><PlugZap size={16}/>{qqConnected?'重新连接':'连接 QQ'}</button>
            {config.qq.account&&<button onClick={()=>void action('disconnect-qq','/qq/disconnect',{},'POST',true)}><Unplug size={16}/>断开连接</button>}
            <button disabled={!qqConnected} onClick={()=>setManagingFriends(true)}><Users size={16}/>管理好友</button></div>
          {differentAccount&&<p>当前配置为 {config.qq.account}。先断开连接，再连接所选账号，已有记录会保留。</p>}
          <div className={`qq-connection-state ${qqConnected?'connected':''}`} role="status">
            {qqConnected?<Check size={17}/>:<PlugZap size={17}/>}<span><strong>{qqConnected?'消息通道已连接'+(config.qq.account?' · '+config.qq.account:''):'消息通道未连接'}</strong>
            <small>{qqConnected?(runtime.qq?.receiver.note!=='QQ 已连接'?runtime.qq?.receiver.note:'可在“管理好友”中选择好友，再到 QQ 好友页开启接话。'):(config.qq.account&&bridge?.account===config.qq.account?bridge.note:runtime.qq?.receiver.note)||'选择账号后点击连接 QQ'}</small></span>
          </div>
          <p className="settings-footnote">目前适配 Windows 电脑版 QQ NT。程序按实际接口检测兼容性，QQ 更新后如提示不兼容，请重新检测。</p>
        </section>
      </>:<>
        <section><h2>资料与记忆</h2><div className="settings-actions"><button onClick={()=>navigation.navigate('profiles')}>修改称呼和资料<ArrowUpRight size={16}/></button><button onClick={()=>navigation.navigate('memory')}>管理记忆<ArrowUpRight size={16}/></button></div></section>
        <DataBackups/>
        <section><h2>本地数据</h2><p>聊天、资料和记忆保存在本机的数据文件夹中。</p><p className="settings-path">{application?.data_folder}</p><button onClick={()=>void action('open-data','/open-folder',{folder:'data'})}><FolderOpen size={16}/>打开数据文件夹</button></section>
        <section><h2>运行日志</h2><p className="settings-path">{application?.logs_folder}</p><button onClick={()=>void action('open-logs','/open-folder',{folder:'logs'})}><FolderOpen size={16}/>打开日志文件夹</button></section>
        <p>版本 {application?.version??'—'}</p>
      </>}</fieldset>}{busy&&busy!=='test'&&!busy.startsWith('test-')&&<p role="status" className="settings-notice"><LoaderCircle size={16}/>正在处理…</p>}
    </div><FriendManager open={managingFriends} onClose={()=>setManagingFriends(false)} onAdded={message=>{setNotice(message);void runtime.refresh();}}/></main></AppShell>;
}
