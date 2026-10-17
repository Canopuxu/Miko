import { useState } from 'react';
import { LoaderCircle, RefreshCw } from 'lucide-react';
import { getJson, responseError } from '../api/chat';
import { useRuntime } from './RuntimeStatus';

type Item = {id:string; text:string; expressions:string[]; state:string};

export function SearchIndexSettings(){
  const runtime=useRuntime();
  const status=runtime.status?.memory.search_expansion;
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [items,setItems]=useState<Item[]>([]);
  async function load(){
    try{setItems((await getJson<{items:Item[]}>('/api/memory/search-index')).items);setError('');}
    catch(e){setError(String(e));}
  }
  async function action(path:string,body:object){
    setBusy(true);setError('');setNotice('');
    try{
      const response=await fetch('/api/memory/search-index/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));
      await runtime.refresh();await load();
      if(path==='rebuild')setNotice('已加入处理队列。');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <div className="settings-memory-index search-expansion" role="group" aria-label="记忆整理的口语检索增强">
    <div className="search-expansion-heading"><h4>口语检索增强</h4>
    <label className="settings-check"><input type="checkbox" disabled={busy||!status} checked={status?.enabled??true}
      onChange={e=>void action('control',{enabled:e.target.checked})}/>自动生成</label></div>
    <p className="search-expansion-description">资料更新时，在记忆整理时使用 AI 补充检索信息，增强本地检索。</p>
    {status&&<><div className="search-expansion-progress" role="status">
      <span className="search-expansion-state">
      {status.running&&<LoaderCircle size={16} className="spinning"/>}
      <strong>{!status.configured?'配置模型后自动生成':status.running?'正在生成检索信息':!status.enabled?'已暂停生成':!status.total?'等待新增资料':status.pending?'等待后台处理':'检索信息已就绪'}</strong></span>
      {status.total>0&&<span>{status.ready} / {status.total} 条已处理{status.failed?` · ${status.failed} 条待重试`:''}</span>}
    </div>{status.total>0&&<progress aria-label="检索信息处理进度" max={status.total} value={status.ready}/>}
    {status.error&&<p className="error-notice" role="alert">{status.error}</p>}
    <div className="search-expansion-footer"><span>{status.model?'使用记忆整理模型 · '+status.model:'共用记忆整理模型'}</span>
    <button disabled={busy||status.running||!status.configured} onClick={()=>void action('rebuild',{rebuild_all:false})}>
      <RefreshCw size={16}/>补建 / 重试</button></div></>}
    {error&&<p className="error-notice" role="alert">{error}</p>}{notice&&<p className="settings-notice" role="status">{notice}</p>}
    <details className="search-expansion-records" onToggle={e=>{if(e.currentTarget.open)void load();}}><summary>查看生成的检索表达</summary>
      <p className="settings-footnote">仅用于检索。</p>
      <button disabled={busy} onClick={()=>void load()}><RefreshCw size={14}/>刷新列表</button>
      <ul className="search-expression-list">{items.map(item=><li key={item.id}><p>{item.text}</p>
        {item.expressions.length?<ul>{item.expressions.map((text,i)=><li key={i}>{text}</li>)}</ul>:<small>{item.state==='ready'?'无需补充表达':item.state==='failed'?'等待重试':'等待生成'}</small>}</li>)}</ul>
      <button disabled={busy||status?.running||!status?.configured} onClick={()=>void action('rebuild',{rebuild_all:true})}><RefreshCw size={14}/>重新生成全部表达</button>
    </details>
  </div>;
}
