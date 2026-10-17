import { ArrowUpRight, ChevronDown } from 'lucide-react';

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

export function ProviderLinks({kind='chat'}:{kind?:'chat'|'embedding'|'rerank'}){
  const rows=kind==='embedding'?embedding:kind==='rerank'?rerank:platforms;
  return <details className="settings-provider-links"><summary>
    {kind==='embedding'?'哪些平台提供向量模型？':kind==='rerank'?'哪些平台提供相关性筛选模型？':'获取 API · 国内平台入口'}<ChevronDown size={14}/>
  </summary><div className={'settings-provider-list'+(kind==='chat'?' compact':'')}>
    {rows.map(row=><a key={row.name} href={row.url} target="_blank" rel="noopener noreferrer">
      <span>{row.name}<ArrowUpRight size={13}/></span>{row.note&&<small>{row.note}</small>}
    </a>)}
  </div>{kind!=='chat'&&<p className="settings-footnote">以上链接前往官方模型说明；可用模型也会从你填写的 API 中自动获取。</p>}</details>;
}
