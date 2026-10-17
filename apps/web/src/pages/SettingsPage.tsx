import { useContext, useEffect, useRef, useState } from 'react';
import { Check, LoaderCircle, PlugZap, Unplug, RefreshCw, Users, ChevronDown, FolderOpen, ArrowUpRight } from 'lucide-react';
import { AppShell, NavigationContext } from '../components/AppShell';
import { FriendManager } from '../components/FriendManager';
import { getJson, responseError } from '../api/chat';
import { useRuntime } from '../components/RuntimeStatus';
import '../styles/settings.css';
import { ModelFields, useModelCatalog, type Provider } from '../components/ModelFields';
import { DataBackups } from './DataBackups';
import { SearchIndexSettings } from '../components/SearchIndexSettings';
import { ProviderLinks } from '../components/ProviderLinks';

type Reranker = Provider;
type Qq = {account:string};
type Config = {provider:Provider; qq_provider:Provider|null; life_provider:Provider|null; memory_provider:Provider|null; rerank_shared:boolean; memory_model:string; life_model:string;
  embedding_model:string; embedding_provider:Provider|null; rerank_provider:Reranker|null; rerank_enabled:boolean; qq:Qq; configured:boolean; draft_pending?:boolean};
type Application = {version:string; data_folder:string; logs_folder:string};
type QqClient = {pid:number; account:string; nickname:string; version:string; executable:string; state:string; note:string;
  capabilities?:{send_images?:boolean}};
type BridgeStatus = {state:string; note:string; account:string; version:string};
const cleanProvider = (p:Provider)=>({api_base:p.api_base.trim(),api_key:p.api_key,model:p.model.trim()});
const emptyProvider = ():Provider=>({api_base:'',api_key:'',model:''});
type TestResult = {state:'running'|'success'|'error'; message:string};
function ModelTest({label,ready,result,test}:{label:string;ready:boolean;result?:TestResult;test:()=>void}){
  const [overlay,setOverlay]=useState<TestResult['state']|null>(null);
  const [leaving,setLeaving]=useState(false);
  useEffect(()=>{
    setOverlay(result?.state??null);setLeaving(false);
    if(!result||result.state==='running')return;
    const hold=result.state==='success'?1300:2200;
    const fade=window.setTimeout(()=>setLeaving(true),hold);
    const finish=window.setTimeout(()=>{setOverlay(null);setLeaving(false);},hold+220);
    return()=>{window.clearTimeout(fade);window.clearTimeout(finish);};
  },[result]);
  return <button className={'settings-model-test'+(overlay?' has-feedback':'')+(leaving?' is-leaving':'')}
    disabled={!ready||result?.state==='running'} aria-busy={result?.state==='running'}
    aria-label={`测试${label}连接`} title={result?.state==='error'?result.message:undefined} onClick={test}>
    <span className="settings-test-label"><PlugZap size={16}/>测试连接</span>
    {overlay&&<span key={overlay} className={'settings-test-overlay '+overlay} aria-hidden="true">
      {overlay==='running'?<LoaderCircle size={19} className="spinning"/>:overlay==='success'?
        <svg className="settings-test-check" width="29" height="29" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="16" cy="16" r="13.5"/><path d="M9 16.5l4.5 4.5L23 11"/>
        </svg>:<><RefreshCw size={15}/>请重试</>}
    </span>}
    {result&&<span className="sr-only" role={result.state==='error'?'alert':'status'}>{result.state==='running'?'正在测试连接':result.state==='success'?'连接成功':result.message}</span>}
  </button>;
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
  const revision=useRef(0);
  const [saving,setSaving]=useState(false);
  const [saveState,setSaveState]=useState<'idle'|'saved'|'draft'|'waiting'|'error'>('idle');
  const [saveError,setSaveError]=useState('');
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
    if(!value.rerank_enabled&&value.rerank_provider)value.rerank_provider={...value.rerank_provider,model:''};
    setConfig(value);setApplication(app);setError('');
    if(value.draft_pending){setDirtySections(['models']);setSaveState('draft');}
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
  useEffect(()=>{if(!dirty||saveState==='waiting')return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty,saveState]);
  function markDirty(){revision.current++;setSaveState('idle');setSaveError('');setTestResults({});setBusy(current=>current==='test'||current.startsWith('test-')?'':current);setDirtySections(items=>items.includes(section)?items:[...items,section]);setNotice('');}
  function edit(value:Config){setConfig(value);markDirty();}
  async function action(name:string,path:string,body:unknown,method='POST',saved=false){
    const testing=name==='test'||name.startsWith('test-');
    const testedRevision=revision.current;
    setBusy(name);setError('');setNotice('');
    if(testing)setTestResults(current=>({...current,[name]:{state:'running',message:'正在测试…'}}));
    try{const response=await fetch('/api/settings'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));const result=await response.json();
      if(testing){if(revision.current===testedRevision)setTestResults(current=>({...current,[name]:{state:'success',message:result.message}}));}else setNotice(result.message);
      if(saved){const next=await getJson<Config>('/api/settings');setConfig(current=>current?(path==='/models'?{...next,qq:current.qq}:{...current,qq:next.qq}):next);setDirtySections(items=>items.filter(item=>item!==(path==='/models'?'models':'qq')));await runtime.refresh();}
    }catch(e){const message=e instanceof Error?e.message:String(e);
      if(testing){if(revision.current===testedRevision)setTestResults(current=>({...current,[name]:{state:'error',message}}));}else setError(message);
    }finally{if(!testing||revision.current===testedRevision)setBusy(current=>current===name?'':current);}
  }
  const models=config?{provider:cleanProvider(config.provider),qq_provider:config.qq_provider?cleanProvider(config.qq_provider):null,
    life_provider:config.life_provider?cleanProvider(config.life_provider):null,
    memory_provider:config.memory_provider?cleanProvider(config.memory_provider):null,
    memory_model:config.memory_provider?.model??'',life_model:config.life_provider?.model??'',embedding_model:config.embedding_model,
    embedding_provider:config.embedding_provider?cleanProvider(config.embedding_provider):null,
    rerank_shared:config.rerank_shared,rerank_enabled:!!config.rerank_provider?.model.trim(),rerank_provider:config.rerank_provider?cleanProvider(config.rerank_provider):null,
    use_shared_model:!(config.qq_provider||config.life_provider||config.memory_provider)}:null;
  const modelPayload=JSON.stringify(models);
  useEffect(()=>{
    if(!dirtySections.includes('models')||!models||busy||saving||saveState==='error')return;
    const timer=window.setTimeout(async()=>{
      const currentRevision=revision.current;
      setSaving(true);
      try{
        const response=await fetch('/api/settings/models?draft=true',{method:'PUT',headers:{'Content-Type':'application/json'},body:modelPayload});
        if(!response.ok)throw new Error(await responseError(response));
        const result=await response.json() as {state:'applied'|'draft'|'waiting'};
        if(revision.current===currentRevision){
          setSaveState(result.state==='applied'?'saved':result.state);
          if(result.state!=='waiting')setDirtySections(items=>items.filter(item=>item!=='models'));
        }
        if(result.state==='applied')await runtime.refresh();
      }catch(e){if(revision.current===currentRevision){setSaveState('error');setSaveError(e instanceof Error?e.message:String(e));}}
      finally{setSaving(false);}
    },saveState==='waiting'?3000:800);
    return()=>window.clearTimeout(timer);
  },[modelPayload,dirtySections,busy,saving,saveState]);
  const selectedClient=qqClients.find(row=>String(row.pid)===selectedPid&&row.state==='ready');
  const qq=config?{account:selectedClient?.account??config.qq.account.trim(),
    ...(selectedClient?{client_pid:selectedClient.pid}:{})}:null;
  const qqConnected=runtime.qq?.receiver.state==='connected';
  const differentAccount=!!config?.qq.account&&!!selectedClient&&config.qq.account!==selectedClient.account;
  const embedding=config?.embedding_provider??(config?{...config.provider,model:config.embedding_model}:emptyProvider());
  const catalog=useModelCatalog(config?.provider??null,'provider');
  const reranker=config?.rerank_shared?{...config.provider,model:config.rerank_provider?.model??''}:config?.rerank_provider??emptyProvider();
  const ready=(value:Provider)=>!!(value.api_base.trim()&&value.model.trim()&&(value.api_key.trim()||value.has_key));
  function changeSearch(kind:'embedding'|'rerank',value:Provider){
    if(!config)return;
    const current=kind==='embedding'?embedding:reranker;
    const connectionChanged=value.api_base!==current.api_base||value.api_key!==current.api_key;
    if(kind==='embedding')edit({...config,embedding_model:value.model,
      embedding_provider:connectionChanged||config.embedding_provider?value:null});
    else edit({...config,rerank_provider:value,rerank_shared:connectionChanged?false:config.rerank_shared});
  }
  function chooseSection(next:typeof section){setSection(next);setNotice('');setError('');}
  return <AppShell view="settings" sidebar={<nav className="settings-navigation" aria-label="设置分类">
    <button className={section==='models'?'selected':''} onClick={()=>chooseSection('models')}>模型与 API</button>
    <button className={section==='qq'?'selected':''} onClick={()=>chooseSection('qq')}>QQ 连接</button>
    <button className={section==='data'?'selected':''} onClick={()=>chooseSection('data')}>数据与日志</button>
  </nav>}><main className="main-panel settings-panel" aria-label="设置">
    <div className="settings-content">
      {error&&<p className="error-notice" role="alert">{error}</p>}{notice&&<p className="settings-notice" role="status"><Check size={16}/>{notice}</p>}
      {!config&&!error&&<p role="status">读取设置…</p>}{!config&&error&&<button onClick={()=>void load()}>重新加载</button>}
      {config&&<fieldset disabled={!!busy} className="settings-body">{section==='models'?<>
        <section className="settings-model-section"><div className="settings-section-heading"><h2>聊天与日常</h2><ProviderLinks floating/>
          {(dirty||saving||saveState!=='idle')&&<span className={'settings-autosave'+(saveState==='error'?' error':'')} role="status">
            {saving?<><LoaderCircle size={13} className="spinning"/>正在保存…</>:saveState==='error'?<>保存失败<button type="button" onClick={()=>setSaveState('idle')}>重试</button></>:saveState==='waiting'?'已保存 · 待生效':saveState==='draft'?'已保存 · 待完善':dirty?'等待保存…':'已保存'}
          </span>}</div>
          {saveError&&<p className="error-notice" role="alert">{saveError}</p>}
          <ModelFields value={config.provider} change={provider=>edit({...config,provider})} catalog={catalog}
            testAction={<ModelTest label="聊天" ready={ready(config.provider)} result={testResults.test}
              test={()=>void action('test','/models/test',cleanProvider(config.provider))}/>}/>
          <details className="settings-optional settings-task-settings"><summary><span><strong>分功能设置</strong>
            <small>{config.qq_provider||config.life_provider||config.memory_provider?'部分功能已单独设置':'默认沿用上面的配置'}</small></span><ChevronDown size={17}/></summary>
          <div className="settings-optional-content"><div className="settings-task-list" aria-label="各项功能的模型设置">
            {(['qq','life','memory'] as const).map(task=>{
              const key=task==='qq'?'qq_provider':task==='life'?'life_provider':'memory_provider';
              const label=task==='qq'?'QQ 聊天':task==='life'?'生活生成':'记忆整理';
              const description=task==='qq'?'用于回复 QQ 好友和群聊。':task==='life'?'用于生成角色的日常经历和目标进度。':'将聊天中值得记住的信息整理为资料和经历。';
              const value=config[key];
              return <div className="settings-task" key={task}><div className="settings-task-heading"><div className="settings-task-title"><h3>{label}</h3>{task==='memory'&&<span className="settings-model-recommendation">推荐使用旗舰模型</span>}</div>
                <label className="settings-check"><input type="checkbox" checked={!!value} onChange={event=>edit({...config,[key]:event.target.checked?{...config.provider}:null})}/>单独设置</label></div>
                <p className="settings-task-description">{description}</p>
                {value&&<ModelFields value={value} target={task} change={next=>edit({...config,[key]:next})}
                  testAction={<ModelTest label={label} ready={ready(value)} result={testResults['test-'+task]}
                    test={()=>void action('test-'+task,'/models/test?target='+task,cleanProvider(value))}/>}/>}
                {task==='memory'&&<SearchIndexSettings/>}
              </div>;
            })}
          </div></div></details>
        </section>
        <section className="settings-model-section"><div className="settings-section-heading"><h2>记忆检索</h2><ProviderLinks kind="embedding" floating/></div>
          <p className="settings-section-description">用向量模型按意思查找资料和记忆。模型留空时，继续使用本地检索。</p>
          {config.embedding_provider&&<div className="settings-connection-note"><button type="button" onClick={()=>edit({...config,embedding_provider:null})}>恢复共用 API</button></div>}
          <ModelFields kind="embedding" target="embedding" value={embedding} inherited={!config.embedding_provider} catalog={config.embedding_provider?undefined:catalog}
            change={value=>changeSearch('embedding',value)}
            testAction={<ModelTest label="记忆检索" ready={ready(embedding)} result={testResults['test-embedding']}
              test={()=>void action('test-embedding','/models/test?target=embedding'+(config.embedding_provider?'':'&shared=true'),cleanProvider(embedding))}/>}/>
          <p className="settings-footnote">更换模型会重新建立检索索引。</p>
          <details className="settings-optional"><summary><span><strong>相关性筛选</strong><span className="settings-optional-tag">可选</span>
            <small>进一步筛选检索结果，优先选取贴近当前话题的资料。</small></span><ChevronDown size={17}/></summary>
            <div className="settings-optional-content">
              <p>留空时使用默认排序。</p>
              {!config.rerank_shared&&<div className="settings-connection-note"><button type="button" onClick={()=>edit({...config,rerank_shared:true})}>恢复共用 API</button></div>}
              <ModelFields kind="rerank" target="rerank" value={reranker} inherited={config.rerank_shared} catalog={config.rerank_shared?catalog:undefined}
                change={value=>changeSearch('rerank',value)}
                testAction={<ModelTest label="相关性筛选" ready={ready(reranker)} result={testResults['test-rerank']}
                  test={()=>void action('test-rerank','/models/test?target=rerank'+(config.rerank_shared?'&shared=true':''),cleanProvider(reranker))}/>}/>
              <ProviderLinks kind="rerank"/>
            </div>
          </details>
        </section>
      </>:section==='qq'?<>
        <section className="settings-basic"><h2>QQ 连接</h2><p>登录电脑版 QQ 后，选择账号连接。</p>
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
        <section><h2>资料与记忆</h2><div className="settings-actions"><button onClick={()=>navigation.navigate('profiles',{focus:'profile-name'})}>修改称呼和资料<ArrowUpRight size={16}/></button><button onClick={()=>navigation.navigate('memory')}>管理记忆<ArrowUpRight size={16}/></button></div></section>
        <DataBackups/>
        <section><h2>本地数据</h2><p>聊天、资料和记忆保存在本机的数据文件夹中。</p><p className="settings-path">{application?.data_folder}</p><button onClick={()=>void action('open-data','/open-folder',{folder:'data'})}><FolderOpen size={16}/>打开数据文件夹</button></section>
        <section><h2>运行日志</h2><p className="settings-path">{application?.logs_folder}</p><button onClick={()=>void action('open-logs','/open-folder',{folder:'logs'})}><FolderOpen size={16}/>打开日志文件夹</button></section>
        <p>版本 {application?.version??'—'}</p>
      </>}</fieldset>}{busy&&busy!=='test'&&!busy.startsWith('test-')&&<p role="status" className="settings-notice"><LoaderCircle size={16}/>正在处理…</p>}
    </div><FriendManager open={managingFriends} onClose={()=>setManagingFriends(false)} onAdded={message=>{setNotice(message);void runtime.refresh();}}/></main></AppShell>;
}
