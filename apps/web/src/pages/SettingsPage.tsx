import { useContext, useEffect, useState } from 'react';
import { Check, LoaderCircle, Save, PlugZap, Users, ChevronDown } from 'lucide-react';
import { AppShell, NavigationContext } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';
import { useRuntime } from '../components/RuntimeStatus';
import '../styles/settings.css';

type Provider = {api_base:string; api_key:string; model:string; has_key?:boolean};
type Qq = {account:string; http_url:string; http_token:string; ws_url:string; ws_token:string; has_http_token?:boolean; has_ws_token?:boolean};
type Config = {provider:Provider; qq_provider:Provider|null; memory_model:string; life_model:string; embedding_model:string; rerank_model:string; qq:Qq; configured:boolean};
const cleanProvider = (p:Provider)=>({api_base:p.api_base.trim(),api_key:p.api_key,model:p.model.trim()});
function ProviderFields({value,change}:{value:Provider;change:(value:Provider)=>void}){
  return <div className="settings-fields">
    <label>API 地址<input value={value.api_base} placeholder="https://…/v1" onChange={e=>change({...value,api_base:e.target.value})}/></label>
    <label>API Key<input type="password" autoComplete="new-password" value={value.api_key} placeholder={value.has_key?'已保存，留空保留':'填写密钥'} onChange={e=>change({...value,api_key:e.target.value})}/></label>
    <label>模型名称<input value={value.model} placeholder="服务商提供的模型名称" onChange={e=>change({...value,model:e.target.value})}/></label>
  </div>;
}
export function SettingsPage(){
  const navigation=useContext(NavigationContext)!;
  const runtime=useRuntime();
  const [config,setConfig]=useState<Config|null>(null);
  const [customModels,setCustomModels]=useState(false);
  const [manualQq,setManualQq]=useState(false);
  const [section,setSection]=useState<'models'|'qq'>('models');
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [dirtySections,setDirtySections]=useState<string[]>([]);
  const dirty=dirtySections.length>0;
  async function load(){try{
    const value=await getJson<Config>('/api/settings');setConfig(value);
    setCustomModels(!!(value.memory_model||value.life_model||value.qq_provider));setError('');
  }catch(e){setError(String(e));}}
  useEffect(()=>{void load();},[]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[dirty]);
  function markDirty(){setDirtySections(items=>items.includes(section)?items:[...items,section]);setNotice('');}
  function edit(value:Config){setConfig(value);markDirty();}
  async function action(name:string,path:string,body:unknown,method='POST',saved=false){
    setBusy(name);setError('');setNotice('');
    try{const response=await fetch('/api/settings'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));const result=await response.json();setNotice(result.message);
      if(saved){const next=await getJson<Config>('/api/settings');setConfig(current=>current?(path==='/models'?{...next,qq:current.qq}:{...current,qq:next.qq}):next);setDirtySections(items=>items.filter(item=>item!==(path==='/models'?'models':'qq')));await runtime.refresh();}
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}
  }
  const models=config?{provider:cleanProvider(config.provider),qq_provider:config.qq_provider?cleanProvider(config.qq_provider):null,memory_model:config.memory_model,life_model:config.life_model,embedding_model:config.embedding_model,use_shared_model:!customModels}:null;
  const qq=config?{account:config.qq.account.trim(),managed:!manualQq,...(manualQq?{http_url:config.qq.http_url.trim(),ws_url:config.qq.ws_url.trim(),http_token:config.qq.http_token,ws_token:config.qq.ws_token}:{})}:null;
  return <AppShell view="settings" sidebar={<nav className="settings-navigation" aria-label="设置分类">
    <button className={section==='models'?'selected':''} onClick={()=>{setSection('models');setNotice('');setError('');}}>模型与 API</button>
    <button className={section==='qq'?'selected':''} onClick={()=>{setSection('qq');setNotice('');setError('');}}>QQ 连接</button>
  </nav>}><main className="main-panel settings-panel"><header className="page-header"><h1>设置</h1>{dirty&&<span>尚未保存</span>}</header>
    <div className="settings-content">
      {error&&<p className="error-notice" role="alert">{error}</p>}{notice&&<p className="settings-notice" role="status"><Check size={16}/>{notice}</p>}
      {!config&&!error&&<p role="status">读取设置…</p>}{!config&&error&&<button onClick={()=>void load()}>重新加载</button>}
      {config&&<fieldset disabled={!!busy} className="settings-body">{section==='models'?<>
        <section className="settings-basic"><h2>模型与 API</h2><ProviderFields value={config.provider} change={provider=>edit({...config,provider})}/>
          <p>{customModels?'部分功能已单独配置模型，可在高级设置中调整。':'聊天、QQ 回复、生活生成和记忆整理共用此模型。'}</p>
          <div className="settings-actions"><button onClick={()=>void action('test','/models/test',cleanProvider(config.provider))}><PlugZap size={16}/>测试连接</button><button className="settings-primary" onClick={()=>void action('save-models','/models',models,'PUT',true)}><Save size={16}/>保存</button></div>
        </section>
        <details className="settings-advanced"><summary>高级设置<ChevronDown size={16}/></summary><div className="settings-advanced-content">
          <label className="settings-check"><input type="checkbox" checked={customModels} onChange={e=>{setCustomModels(e.target.checked);markDirty();}}/>为不同功能指定模型</label>
          {customModels&&<><div className="settings-fields"><label>生活生成<input value={config.life_model} placeholder="使用基础模型" onChange={e=>edit({...config,life_model:e.target.value})}/></label><label>记忆整理<input value={config.memory_model} placeholder="使用基础模型" onChange={e=>edit({...config,memory_model:e.target.value})}/></label></div>
            <label className="settings-check"><input type="checkbox" checked={!!config.qq_provider} onChange={e=>edit({...config,qq_provider:e.target.checked?{...config.provider,api_key:''}:null})}/>单独配置 QQ 模型连接</label>
            {config.qq_provider&&<ProviderFields value={config.qq_provider} change={qq_provider=>edit({...config,qq_provider})}/>}</>}
          <div className="settings-fields"><label>向量检索模型<input value={config.embedding_model} placeholder="留空使用关键词检索" onChange={e=>edit({...config,embedding_model:e.target.value})}/></label></div>
          <p>向量检索需要专用模型，共用基础 API。更换后重建索引，会产生调用费用。重排沿用 {config.rerank_model}。</p>
          <p>DSH 工作台的模型在其原生设置中管理。</p>
          <button className="settings-primary" onClick={()=>void action('save-models','/models',models,'PUT',true)}><Save size={16}/>保存设置</button>
        </div></details>
      </>:<>
        <section className="settings-basic"><h2>QQ 连接</h2><div className="settings-fields"><label>QQ 号<input inputMode="numeric" value={config.qq.account} onChange={e=>edit({...config,qq:{...config.qq,account:e.target.value}})}/></label></div>
          <div className="settings-actions"><button className="settings-primary" onClick={()=>void action('save-qq','/qq',qq,'PUT',true)}><PlugZap size={16}/>连接 QQ</button><button onClick={()=>void action('friends','/qq/friends',undefined)}><Users size={16}/>同步好友</button><button onClick={()=>navigation.navigate('qq')}>管理好友</button></div>
          <p role="status">{runtime.qq?.receiver.note||'尚未连接'}</p>
        </section>
        <details className="settings-advanced"><summary>高级设置<ChevronDown size={16}/></summary><div className="settings-advanced-content">
          <label className="settings-check"><input type="checkbox" checked={manualQq} onChange={e=>{setManualQq(e.target.checked);markDirty();}}/>手动连接外部 OneBot 服务</label>
          {manualQq&&<><div className="settings-fields">
            <label>HTTP 地址<input value={config.qq.http_url} onChange={e=>edit({...config,qq:{...config.qq,http_url:e.target.value}})}/></label>
            <label>HTTP Token<input type="password" autoComplete="new-password" placeholder={config.qq.has_http_token?'已保存，留空保留':''} value={config.qq.http_token} onChange={e=>edit({...config,qq:{...config.qq,http_token:e.target.value}})}/></label>
            <label>WebSocket 地址<input value={config.qq.ws_url} onChange={e=>edit({...config,qq:{...config.qq,ws_url:e.target.value}})}/></label>
            <label>WebSocket Token<input type="password" autoComplete="new-password" placeholder={config.qq.has_ws_token?'已保存，留空保留':''} value={config.qq.ws_token} onChange={e=>edit({...config,qq:{...config.qq,ws_token:e.target.value}})}/></label>
          </div><div className="settings-actions"><button onClick={()=>void action('test-qq','/qq/test',qq)}><PlugZap size={16}/>测试连接</button><button className="settings-primary" onClick={()=>void action('save-qq','/qq',qq,'PUT',true)}><Save size={16}/>保存并连接</button></div></>}
        </div></details>
      </>}</fieldset>}{busy&&<p role="status" className="settings-notice"><LoaderCircle size={16}/>正在处理…</p>}
    </div></main></AppShell>;
}
