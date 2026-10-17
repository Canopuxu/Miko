import { useEffect, useState } from 'react';
import { ImagePlus, LoaderCircle, RotateCcw } from 'lucide-react';
import { getJson, responseError } from '../api/chat';
import '../styles/qq-avatar.css';

type Avatars = {account:string; enabled:boolean; connected:boolean; has_owner:boolean; using_default:boolean};
const base = '/api/settings/qq/avatar';

export function QqAvatarSettings({account,connected}:{account:string;connected:boolean}) {
  const [data,setData]=useState<Avatars|null>(null);
  const [version,setVersion]=useState(0);
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{
    let alive=true;
    setData(null);setError('');
    getJson<Avatars>(base).then(value=>{if(alive)setData(value);}).catch(cause=>{if(alive)setError(String(cause));});
    return()=>{alive=false;};
  },[account,connected]);

  async function update(action:string,path:string,body?:unknown,method='PUT') {
    setBusy(action);setError('');
    try {
      const response=await fetch(base+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));
      setData(await response.json());setVersion(value=>value+1);
    } catch(cause) {setError(cause instanceof Error?cause.message:String(cause));}
    finally {setBusy('');}
  }
  async function upload(kind:'role'|'owner',file:File|undefined) {
    if(!file)return;
    if(file.size>6*1024*1024){setError('图片超过 6 MiB 上限');return;}
    setBusy(kind);setError('');
    try {
      const image=await new Promise<string>((resolve,reject)=>{
        const reader=new FileReader();
        reader.onload=()=>resolve(String(reader.result).split(',')[1]);
        reader.onerror=()=>reject(new Error('图片读取失败'));reader.readAsDataURL(file);
      });
      await update(kind,'/image/'+kind,{image_base64:image});
    } catch(cause) {setError(cause instanceof Error?cause.message:String(cause));setBusy('');}
  }
  return <section className="qq-avatar-settings">
    <div className="qq-avatar-heading"><h3>接话时切换头像</h3>
      <label className="qq-avatar-toggle"><button type="button" role="switch" aria-label="接话时切换头像"
        aria-checked={!!data?.enabled} className="qq-idle-switch" disabled={!data||!!busy||(!data.enabled&&!connected)}
        onClick={()=>void update('switch','',{enabled:!data!.enabled})}><span/></button>
        {busy==='switch'?<LoaderCircle size={14} className="spinning"/>:data?.enabled?'已开启':'未开启'}</label></div>
    <p>全部接话结束一分钟后恢复原头像；你主动发消息时提前恢复。其他会话仍在接话时，继续使用角色头像。</p>
    <div className="qq-avatar-pictures">{(['role','owner'] as const).map(kind=><div className="qq-avatar-picture" key={kind}>
      <div className="qq-avatar-image">{data&&(kind==='role'||data.has_owner)?
        <img src={`${base}/image/${kind}?v=${version}&account=${data.account}`} alt={kind==='role'?'角色头像':'恢复头像'}/>:<ImagePlus size={24}/>}</div>
      <div className="qq-avatar-copy"><strong>{kind==='role'?'角色头像':'你的 QQ 头像'}</strong>
        <small>{kind==='role'?(data?.using_default?'默认头像':'自定义头像'):(data?.has_owner?'结束接话后恢复':'首次开启时自动保存当前头像')}</small>
        <div className="qq-avatar-actions"><label className={'qq-avatar-upload'+(!account||!!busy?' disabled':'')}>
          {busy===kind?<LoaderCircle size={13} className="spinning"/>:<ImagePlus size={13}/>}{kind==='role'?'更换图片':'选择恢复头像'}
          <input type="file" aria-label={kind==='role'?'更换角色头像':'选择恢复头像'} accept="image/png,image/jpeg,image/gif,image/webp" disabled={!account||!!busy}
            onChange={event=>{const file=event.target.files?.[0];event.target.value='';void upload(kind,file);}}/>
        </label>{kind==='role'&&data&&!data.using_default&&<button type="button" disabled={!!busy} onClick={()=>void update('default','/default',undefined,'POST')}><RotateCcw size={12}/>恢复默认</button>}</div>
      </div>
    </div>)}</div>
    {error&&<p className="error-notice" role="alert">{error}</p>}
  </section>;
}
