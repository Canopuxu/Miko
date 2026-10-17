import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { ImagePlus, LoaderCircle, RotateCcw, X } from 'lucide-react';
import { getJson, responseError } from '../api/chat';
import '../styles/qq-avatar.css';

type Avatars = {account:string; enabled:boolean; connected:boolean; using_default:boolean};
const base = '/api/settings/qq/avatar';

export function AvatarSettingsDialog({onClose}:{onClose:()=>void}) {
  const [data,setData]=useState<Avatars|null>(null);
  const [version,setVersion]=useState(0);
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  const [saved,setSaved]=useState(false);
  useEffect(()=>{
    let alive=true;
    getJson<Avatars>(base).then(value=>{if(alive)setData(value);}).catch(cause=>{if(alive)setError(String(cause));});
    return()=>{alive=false;};
  },[]);

  async function update(action:string,path:string,body?:unknown,method='PUT') {
    setBusy(action);setError('');setSaved(false);
    try {
      const response=await fetch(base+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      if(!response.ok)throw new Error(await responseError(response));
      setData(await response.json());setVersion(value=>value+1);setSaved(true);
    } catch(cause) {setError(cause instanceof Error?cause.message:String(cause));}
    finally {setBusy('');}
  }
  async function upload(file:File|undefined) {
    if(!file)return;
    if(file.size>6*1024*1024){setError('图片超过 6 MiB 上限');return;}
    setBusy('image');setError('');setSaved(false);
    try {
      const image=await new Promise<string>((resolve,reject)=>{
        const reader=new FileReader();
        reader.onload=()=>resolve(String(reader.result).split(',')[1]);
        reader.onerror=()=>reject(new Error('图片读取失败'));reader.readAsDataURL(file);
      });
      await update('image','/image/role',{image_base64:image});
    } catch(cause) {setError(cause instanceof Error?cause.message:String(cause));setBusy('');}
  }
  return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose();}}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="runtime-dialog profile-tools-dialog avatar-settings-dialog">
      <header className="runtime-heading"><Dialog.Title>头像设置</Dialog.Title>
        <button className="icon-button" aria-label="关闭头像设置" disabled={!!busy} onClick={onClose}><X size={18}/></button></header>
      <Dialog.Description>选择角色头像，修改后自动保存。</Dialog.Description>
      <div className="qq-avatar-picture"><div className="qq-avatar-image">
        {data?<img src={`${base}/image/role?v=${version}&account=${data.account}`} alt="角色头像"/>:<LoaderCircle size={24} className="spinning"/>}
      </div><div className="qq-avatar-copy"><strong>角色头像</strong><small>{data?.using_default?'默认头像':'自定义头像'}</small>
        <div className="qq-avatar-actions"><label className={'ui-button qq-avatar-upload'+(!!busy?' disabled':'')}>
          {busy==='image'?<LoaderCircle size={14} className="spinning"/>:<ImagePlus size={14}/>}更换图片
          <input type="file" aria-label="更换角色头像" accept="image/png,image/jpeg,image/gif,image/webp" disabled={!!busy}
            onChange={event=>{const file=event.target.files?.[0];event.target.value='';void upload(file);}}/>
        </label>{data&&!data.using_default&&<button className="text-button" type="button" disabled={!!busy} onClick={()=>void update('default','/default',undefined,'POST')}><RotateCcw size={13}/>恢复默认</button>}</div>
      </div></div>
      <div className="qq-avatar-switch-card"><div className="qq-avatar-heading"><h3>QQ 接话时切换头像</h3>
        <button type="button" role="switch" aria-label="QQ 接话时切换头像" aria-checked={!!data?.enabled} className="qq-idle-switch"
          disabled={!data||!!busy||(!data.enabled&&!data.connected)} onClick={()=>void update('switch','',{enabled:!data!.enabled})}><span/></button></div>
        <p>接话时使用角色头像，结束后自动恢复。你的 QQ 头像由程序自动保存。</p>
        {data&&!data.connected&&!data.enabled&&<small>连接 QQ 后可开启。</small>}
        {busy==='switch'&&<small role="status"><LoaderCircle size={13} className="spinning"/>正在保存…</small>}
      </div>
      {error&&<p className="error-notice" role="alert">{error}</p>}
      <footer className="dialog-actions">{saved&&<span className="qq-avatar-saved" role="status">已保存</span>}
        <button className="ui-button" disabled={!!busy} onClick={onClose}>关闭</button></footer>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
