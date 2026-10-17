import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronDown } from 'lucide-react';
import { responseError } from '../api/chat';

type PlatformLink = {name:string;url:string;note?:string};
const platforms:PlatformLink[] = [
  {name:'阿里云百炼',url:'https://bailian.console.aliyun.com/'},
  {name:'火山方舟',url:'https://www.volcengine.com/product/ark'},
  {name:'DeepSeek',url:'https://platform.deepseek.com/'},
  {name:'硅基流动',url:'https://cloud.siliconflow.cn/'},
  {name:'智谱',url:'https://open.bigmodel.cn/'},
  {name:'百度千帆',url:'https://qianfan.cloud.baidu.com/'},
  {name:'腾讯混元',url:'https://cloud.tencent.com/product/hunyuan'},
  {name:'Kimi',url:'https://platform.kimi.com/'},
  {name:'MiniMax',url:'https://platform.minimax.cn/'},
  {name:'阶跃星辰',url:'https://platform.stepfun.com/'},
];
const embedding:PlatformLink[] = [
  {name:'阿里云百炼',url:'https://help.aliyun.com/zh/model-studio/model-list-embedding-reranking/',note:'千问文本向量、text-embedding-v4'},
  {name:'硅基流动',url:'https://docs.siliconflow.cn/docs/api/embeddings-post',note:'Qwen3-Embedding、BGE 系列'},
  {name:'智谱',url:'https://docs.bigmodel.cn/cn/guide/models/embedding/embedding-3',note:'Embedding-3'},
  {name:'百度千帆',url:'https://cloud.baidu.com/doc/qianfan-api/s/Dmba8k71y',note:'BGE 等向量模型'},
];
const rerank:PlatformLink[] = [
  {name:'阿里云百炼',url:'https://help.aliyun.com/zh/model-studio/model-list-embedding-reranking/',note:'千问文本重排、gte-rerank'},
  {name:'硅基流动',url:'https://docs.siliconflow.cn/docs/api/rerank-post',note:'Qwen3-Reranker、BGE、BCE 系列'},
  {name:'百度千帆',url:'https://qianfan.cloud.baidu.com/qianfandev/model/106',note:'BCE 等重排模型'},
];

export function ProviderLinks({kind='chat',floating=false}:{kind?:'chat'|'embedding'|'rerank';floating?:boolean}){
  const container=useRef<HTMLDetailsElement>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    const dismiss=(event:PointerEvent)=>{if(container.current&&!container.current.contains(event.target as Node))container.current.open=false;};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&container.current?.open){container.current.open=false;container.current.querySelector('summary')?.focus();}};
    document.addEventListener('pointerdown',dismiss,true);
    document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',dismiss,true);document.removeEventListener('keydown',escape);};
  },[]);
  async function openLink(url:string){
    setError('');
    try{
      const response=await fetch('/api/settings/open-link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
      if(!response.ok)throw new Error(await responseError(response));
      if(container.current)container.current.open=false;
    }catch(e){setError(e instanceof Error?e.message:String(e));}
  }
  const rows=kind==='embedding'?embedding:kind==='rerank'?rerank:platforms;
  return <details ref={container} className={'settings-provider-links'+(floating?' floating':'')}><summary>
    {kind==='embedding'?'哪些平台提供向量模型？':kind==='rerank'?'哪些平台提供相关性筛选模型？':'获取 API'}<ChevronDown size={14}/>
  </summary><div className={'settings-provider-list'+(kind==='chat'?' compact':'')}>
    {rows.map(row=><a key={row.name} href={row.url} target="_blank" rel="noopener noreferrer" aria-label={row.name}
      onClick={event=>{event.preventDefault();void openLink(row.url);}}>
      <span>{row.name}<ArrowUpRight size={13}/></span>{row.note&&<small>{row.note}</small>}
    </a>)}
    {error&&<p className="settings-provider-error" role="alert">{error}</p>}
  </div></details>;
}
