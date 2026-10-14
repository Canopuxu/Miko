import { useContext, useEffect, useRef, useState } from 'react';
import { Check, LoaderCircle, Save, PlugZap, Users } from 'lucide-react';
import { AppShell, NavigationContext } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';
import { useRuntime } from '../components/RuntimeStatus';
import '../styles/settings.css';

type Provider = {api_base: string; api_key: string; model: string; has_key?: boolean};
type Qq = {account: string; http_url: string; http_token: string; ws_url: string; ws_token: string; has_http_token?: boolean; has_ws_token?: boolean};
type Config = {provider: Provider; qq_provider: Provider | null; memory_model: string; life_model: string;
  embedding_model: string; rerank_model: string; qq: Qq; configured: boolean};
const emptyProvider = (): Provider => ({api_base:'',api_key:'',model:''});
const cleanProvider = (p: Provider) => ({api_base:p.api_base.trim(),api_key:p.api_key,model:p.model.trim()});

function ProviderFields({value, change}: {value: Provider; change:(value:Provider)=>void}) {
  return <div className="settings-fields">
    <label>API 地址<input value={value.api_base} placeholder="https://…/v1" onChange={e=>change({...value,api_base:e.target.value})}/></label>
    <label>API Key<input type="password" autoComplete="new-password" value={value.api_key} placeholder={value.has_key?'已保存，留空保留':'填写密钥'} onChange={e=>change({...value,api_key:e.target.value})}/></label>
    <label>模型名称<input value={value.model} placeholder="填写服务商提供的模型名称" onChange={e=>change({...value,model:e.target.value})}/></label>
  </div>;
}

export function SettingsPage() {
  const navigation = useContext(NavigationContext)!;
  const runtime = useRuntime();
  const content = useRef<HTMLDivElement>(null);
  const [config,setConfig] = useState<Config|null>(null);
  const [section,setSection] = useState<'models'|'qq'>('models');
  const [busy,setBusy] = useState('');
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const [firstRun,setFirstRun] = useState(false);
  const [step,setStep] = useState(1);
  const [dirtySections,setDirtySections] = useState<string[]>([]);
  const dirty = dirtySections.length > 0;
  async function load() {
    try { const value=await getJson<Config>('/api/settings');setConfig(value);setFirstRun(!value.configured);setError(''); }
    catch(e){setError(String(e));}
  }
  useEffect(()=>{void load();},[]);
  useEffect(()=>{
    if(!dirty)return;
    const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
    window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);
  },[dirty]);
  function edit(value:Config){setConfig(value);setDirtySections(items=>items.includes(section)?items:[...items,section]);setNotice('');}
  function selectSection(value:'models'|'qq'){setSection(value);setNotice('');setError('');content.current?.scrollTo({top:0});}
  async function action(name:string,path:string,body:unknown,method='POST',saved=false) {
    setBusy(name);setError('');setNotice('');
    try {
      const response=await fetch('/api/settings'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));
      const result=await response.json();setNotice(result.message);
      if(saved){
        const next=await getJson<Config>('/api/settings');
        setConfig(current=>current ? path==='/models'?{...next,qq:current.qq}:{...current,qq:next.qq} : next);
        setDirtySections(items=>items.filter(item=>item!==(path==='/models'?'models':'qq')));
        await runtime.refresh();
        if(firstRun && path==='/models'){setStep(2);setSection('qq');}
        if(firstRun && path==='/qq')setStep(3);
      }
    }catch(e){setError(e instanceof Error?e.message:String(e));}
    finally{setBusy('');}
  }
  const models=config?{provider:cleanProvider(config.provider),qq_provider:config.qq_provider?cleanProvider(config.qq_provider):null,
    memory_model:config.memory_model,life_model:config.life_model,embedding_model:config.embedding_model}:null;
  const qq=config?{account:config.qq.account.trim(),http_url:config.qq.http_url.trim(),ws_url:config.qq.ws_url.trim(),http_token:config.qq.http_token,ws_token:config.qq.ws_token}:null;
  return <AppShell view="settings" sidebar={<nav className="settings-navigation" aria-label="设置分类">
    <button className={section==='models'?'selected':''} onClick={()=>selectSection('models')}>模型与 API</button>
    <button className={section==='qq'?'selected':''} onClick={()=>selectSection('qq')}>QQ 连接</button>
    <p>DSH 工作台的模型和插件在其原生设置中管理。</p>
  </nav>}>
    <main className="main-panel settings-panel"><header className="page-header"><h1>{firstRun?'首次配置':'设置'}</h1>{dirty&&<span>尚未保存</span>}</header>
    <div className="settings-content" ref={content}>
      {firstRun&&<div className="setup-progress" aria-label="配置进度">{['连接模型','连接 QQ（可选）','开始使用'].map((label,i)=><span key={label} aria-current={step===i+1?'step':undefined}>{i+1}. {label}</span>)}</div>}
      {error&&<p className="error-notice" role="alert">{error}</p>}
      {notice&&<p className="settings-notice" role="status"><Check size={16}/>{notice}</p>}
      {!config&&!error&&<p role="status">读取设置…</p>}
      {!config&&error&&<button onClick={()=>void load()}>重新加载</button>}
      {config&&<fieldset disabled={!!busy} className="settings-body">
      {firstRun&&step===3?<section><h2>配置完成</h2><p>可以开始聊天。QQ 好友需要逐个开启接话。</p><button className="settings-primary" onClick={()=>{setFirstRun(false);navigation.navigate('chat');}}>开始聊天</button><button onClick={()=>{setFirstRun(false);navigation.navigate('qq');}}>选择 QQ 好友</button></section>:
      section==='models'?<>
        <section><h2>默认模型连接</h2><p>用于 Web 聊天；生活和记忆可指定同一服务商下的其他模型。连接测试会发起一次简短调用。</p>
          <ProviderFields value={config.provider} change={provider=>edit({...config,provider})}/>
          <button onClick={()=>void action('test','/models/test',cleanProvider(config.provider))}><PlugZap size={16}/>测试模型</button>
        </section>
        <section><h2>模型用途</h2><div className="settings-fields">
          <label>生活生成<input value={config.life_model} placeholder="使用默认模型" onChange={e=>edit({...config,life_model:e.target.value})}/></label>
          <label>记忆整理<input value={config.memory_model} placeholder="使用默认模型" onChange={e=>edit({...config,memory_model:e.target.value})}/></label>
          <label>向量检索<input value={config.embedding_model} placeholder="留空使用关键词检索" onChange={e=>edit({...config,embedding_model:e.target.value})}/></label>
        </div><p>向量模型共用默认 API。更换后自动重建索引，会产生调用费用；原始记忆保留。</p>
          <p>重排：{config.rerank_model}，沿用当前专用接口适配。</p>
        </section>
        <section><h2>QQ 回复</h2><label className="settings-check"><input type="checkbox" checked={!!config.qq_provider} onChange={e=>edit({...config,qq_provider:e.target.checked?emptyProvider():null})}/>单独配置 QQ 模型连接</label>
          {config.qq_provider?<><ProviderFields value={config.qq_provider} change={qq_provider=>edit({...config,qq_provider})}/><button onClick={()=>void action('test-qq-model','/models/test?target=qq',cleanProvider(config.qq_provider!))}>测试 QQ 模型</button></>:<p>使用默认模型连接。</p>}
        </section>
        <button className="settings-primary" onClick={()=>void action('save-models','/models',models,'PUT',true)}><Save size={16}/>{firstRun?'保存并继续':'保存模型设置'}</button>
      </>:<>
        <section><h2>QQ 连接</h2><p>先启动并登录兼容的 OneBot 服务，再填写本机接口。读取好友不会自动开启接话。</p>
          <div className="settings-fields">
            <label>登录 QQ 号<input inputMode="numeric" value={config.qq.account} onChange={e=>edit({...config,qq:{...config.qq,account:e.target.value}})}/></label>
            <label>HTTP 地址<input placeholder="http://127.0.0.1:端口" value={config.qq.http_url} onChange={e=>edit({...config,qq:{...config.qq,http_url:e.target.value}})}/></label>
            <label>HTTP Token<input type="password" autoComplete="new-password" placeholder={config.qq.has_http_token?'已保存，留空保留':'无鉴权时可留空'} value={config.qq.http_token} onChange={e=>edit({...config,qq:{...config.qq,http_token:e.target.value}})}/></label>
            <label>WebSocket 地址<input placeholder="ws://127.0.0.1:端口" value={config.qq.ws_url} onChange={e=>edit({...config,qq:{...config.qq,ws_url:e.target.value}})}/></label>
            <label>WebSocket Token<input type="password" autoComplete="new-password" placeholder={config.qq.has_ws_token?'已保存，留空保留':'无鉴权时可留空'} value={config.qq.ws_token} onChange={e=>edit({...config,qq:{...config.qq,ws_token:e.target.value}})}/></label>
          </div>
          <div className="settings-actions"><button onClick={()=>void action('test-qq','/qq/test',qq)}><PlugZap size={16}/>测试连接</button><button className="settings-primary" onClick={()=>void action('save-qq','/qq',qq,'PUT',true)}><Save size={16}/>保存并连接</button></div>
          <p>连接状态：{runtime.qq?.receiver.note||'尚未连接'}</p>
        </section>
        <section><h2>好友</h2><div className="settings-actions"><button onClick={()=>void action('friends','/qq/friends',undefined)}><Users size={16}/>读取已连接账号的好友</button><button onClick={()=>navigation.navigate('qq')}>管理接话好友</button></div></section>
        {firstRun&&<button onClick={()=>setStep(3)}>稍后连接 QQ</button>}
      </>}
      </fieldset>}
      {busy&&<p role="status" className="settings-notice"><LoaderCircle size={16}/>正在处理…</p>}
    </div></main>
  </AppShell>;
}
