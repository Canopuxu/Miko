import { useEffect, useId, useState } from 'react';
import { ChevronDown, RefreshCw } from 'lucide-react';
import { responseError } from '../api/chat';

export type Provider = {api_base:string; api_key:string; model:string; has_key?:boolean};
type Kind = 'chat'|'embedding'|'rerank';
type Target = 'provider'|'qq'|'life'|'memory'|'embedding'|'rerank';
type Candidate = {id:string; kind:Kind|'other'};
export type Catalog = {models:Candidate[]; state:'idle'|'loading'|'ready'|'error'; error:string; refresh:()=>void};

export function useModelCatalog(provider:Provider|null, target:Target, enabled=true):Catalog {
  const [result,setResult]=useState<Pick<Catalog,'models'|'state'|'error'>>({models:[],state:'idle',error:''});
  const [revision,setRevision]=useState(0);
  const apiBase=provider?.api_base.trim()??'', apiKey=provider?.api_key??'', hasKey=provider?.has_key??false;
  useEffect(()=>{
    setResult({models:[],state:'idle',error:''});
    if(!enabled||!apiBase||(!apiKey.trim()&&!hasKey))return;
    const controller=new AbortController();
    const timer=window.setTimeout(async()=>{
      setResult({models:[],state:'loading',error:''});
      try{
        const response=await fetch(`/api/settings/models/list?target=${target}`,{method:'POST',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({api_base:apiBase,api_key:apiKey,model:''}),signal:controller.signal});
        if(!response.ok)throw new Error(await responseError(response));
        const value=await response.json();
        if(!controller.signal.aborted)setResult({models:value.models,state:'ready',error:''});
      }catch(error){if(!controller.signal.aborted)setResult({models:[],state:'error',error:error instanceof Error?error.message:String(error)});}
    },700);
    return()=>{window.clearTimeout(timer);controller.abort();};
  },[apiBase,apiKey,hasKey,target,enabled,revision]);
  return {...result,refresh:()=>setRevision(value=>value+1)};
}

function ModelPicker({value,change,kind,catalog}:{value:string;change:(value:string)=>void;kind:Kind;catalog:Catalog}){
  const id=useId();
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[active,setActive]=useState(-1);
  const candidates=catalog.models.filter(row=>row.kind===kind);
  const shown=candidates.filter(row=>row.id.toLowerCase().includes(search.toLowerCase()));
  function select(model:string){if(model!==value)change(model);setOpen(false);setActive(-1);}
  return <div className="settings-model-field" onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setOpen(false);}}>
    <label htmlFor={id}>{kind==='embedding'?'向量模型':kind==='rerank'?'重排模型':'模型'}</label>
    <div className="settings-model-input"><input id={id} role="combobox" aria-autocomplete="list" aria-expanded={open}
      aria-controls={id+'-options'} aria-activedescendant={open&&active>=0?id+'-option-'+active:undefined}
      value={value} placeholder="选择或直接填写模型名称" autoComplete="off"
      onFocus={()=>{setSearch('');setActive(-1);setOpen(true);}}
      onChange={event=>{change(event.target.value);setSearch(event.target.value);setActive(-1);setOpen(true);}}
      onKeyDown={event=>{
        if(event.key==='Escape'){setOpen(false);return;}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();setOpen(true);
          setActive(index=>shown.length?Math.max(0,Math.min(shown.length-1,index+(event.key==='ArrowDown'?1:-1))):-1);
        }
        if(event.key==='Enter'&&open&&active>=0){event.preventDefault();select(shown[active].id);}
      }}/><button type="button" aria-label="展开模型候选" aria-expanded={open} onClick={()=>{setSearch('');setActive(-1);setOpen(!open);}}><ChevronDown size={16}/></button></div>
    {open&&<div className="settings-model-options" id={id+'-options'} role="listbox" aria-label="模型候选">
      {shown.map((row,index)=><button type="button" role="option" id={id+'-option-'+index} key={row.id}
        aria-selected={row.id===value} className={active===index?'active':''} onMouseDown={event=>event.preventDefault()} onClick={()=>select(row.id)}>{row.id}</button>)}
      {!shown.length&&<p>{catalog.state==='loading'?'正在获取模型…':catalog.state==='ready'?'没有匹配的候选，可直接填写模型名称':'可直接填写模型名称'}</p>}
    </div>}
    {catalog.state==='ready'&&<small>{candidates.length?`${candidates.length} 个候选 · 可搜索或直接填写`:'未识别到此类模型，可直接填写后测试'}</small>}
  </div>;
}

export function ModelFields({value,change,target='provider',kind='chat',catalog,connection=true,inherited=false}:{
  value:Provider;change:(value:Provider)=>void;target?:Target;kind?:Kind;catalog?:Catalog;connection?:boolean;inherited?:boolean;
}){
  const ownCatalog=useModelCatalog(value,target,!catalog&&connection);
  const source=catalog??ownCatalog;
  return <div className={'settings-fields settings-provider-fields'+(connection?'':' settings-shared-fields')}>
    {connection&&<><label className="settings-address">API 地址<input value={value.api_base} placeholder="https://…/v1" onChange={event=>change({...value,api_base:event.target.value})}/>
      <small>{kind==='rerank'?'填写 API 基础地址，程序自动连接对应的筛选接口。':'填写 OpenAI 兼容的 API 基础地址，例如 https://服务商域名/v1。'}</small></label>
      <label>API 密钥<input type="password" autoComplete="new-password" value={value.api_key} placeholder={value.has_key?(inherited?'共用聊天密钥；填写可更换':'已保存，留空保留'):'填写密钥'} onChange={event=>change({...value,api_key:event.target.value})}/></label></>}
    <ModelPicker value={value.model} change={model=>change({...value,model})} kind={kind} catalog={source}/>
    {connection&&<div className="settings-catalog-status" aria-live="polite">
      <button type="button" disabled={source.state==='loading'||!value.api_base.trim()||(!value.api_key.trim()&&!value.has_key)} onClick={source.refresh}><RefreshCw size={14} className={source.state==='loading'?'spinning':''}/>刷新模型列表</button>
      <span className={source.state==='error'?'error':''}>{source.state==='loading'?'正在获取模型列表…':source.state==='error'?source.error:source.state==='ready'?`已获取 ${source.models.length} 个模型，按用途筛选候选`:'填写地址和密钥后自动获取模型'}</span>
    </div>}
  </div>;
}
