import { useState } from 'react';
import { ChevronDown, LoaderCircle, RefreshCw } from 'lucide-react';
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
      if(path==='rebuild')setNotice('已加入后台队列，可继续聊天。');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <details className="settings-memory-index search-expansion"><summary><span>口语检索增强</span><ChevronDown size={15}/></summary>
    <div className="settings-memory-index-content"><div className="settings-task-heading">
    <label className="settings-check"><input type="checkbox" disabled={busy||!status} checked={status?.enabled??true}
      onChange={e=>void action('control',{enabled:e.target.checked})}/>自动生成</label></div>
    <p>资料更新时，使用记忆整理模型补充日常问法，帮助本地检索找到原资料。无需另配 API。</p>
    {status&&<><div className="search-expansion-progress" role="status">
      {status.running&&<LoaderCircle size={16} className="spinning"/>}
      <strong>{!status.configured?'配置聊天模型后可自动生成':status.running?'正在生成检索表达':!status.enabled?'已暂停生成':status.pending?'等待后台补建':'检索表达已就绪'}</strong>
      <span>{status.ready} / {status.total} 条资料已处理{status.failed?` · ${status.failed} 条待重试`:''}</span>
    </div><progress max={Math.max(1,status.total)} value={status.ready}/>
    <p className="settings-footnote">使用模型：{status.model||'尚未配置'}。表达保存在本机，匹配时不再调用该模型；暂停生成后，已有表达继续可用。</p>
    {status.error&&<p className="error-notice" role="alert">{status.error}</p>}
    <div className="settings-actions"><button disabled={busy||status.running||!status.configured} onClick={()=>void action('rebuild',{rebuild_all:false})}>
      <RefreshCw size={16}/>补建 / 重试</button></div></>}
    {error&&<p className="error-notice" role="alert">{error}</p>}{notice&&<p className="settings-notice" role="status">{notice}</p>}
    <details onToggle={e=>{if(e.currentTarget.open)void load();}}><summary>查看生成的检索表达</summary>
      <p className="settings-footnote">这里只用于查找资料，聊天时提供的仍是原资料内容。</p>
      <button disabled={busy} onClick={()=>void load()}><RefreshCw size={14}/>刷新列表</button>
      <ul className="search-expression-list">{items.map(item=><li key={item.id}><p>{item.text}</p>
        {item.expressions.length?<ul>{item.expressions.map((text,i)=><li key={i}>{text}</li>)}</ul>:<small>{item.state==='ready'?'无需补充表达':item.state==='failed'?'等待重试':'等待生成'}</small>}</li>)}</ul>
      <button disabled={busy||status?.running||!status?.configured} onClick={()=>void action('rebuild',{rebuild_all:true})}><RefreshCw size={14}/>重新生成全部表达</button>
    </details>
  </div></details>;
}
