import { useCharacterName } from './RuntimeStatus';
import * as Dialog from '@radix-ui/react-dialog';
import { useEffect, useState } from 'react';
import { LoaderCircle, Search, X } from 'lucide-react';
import { getJson, responseError } from '../api/chat';

type Friend = {friend:string; nickname:string; remark:string; added:boolean};

export function FriendManager({open,onClose,onAdded}:{open:boolean;onClose:()=>void;onAdded:(message:string)=>void}) {
  const characterName = useCharacterName();
  const [friends,setFriends]=useState<Friend[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [query,setQuery]=useState('');
  const [loading,setLoading]=useState(false);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{
    if(!open)return;
    let alive=true;
    setSelected([]);setQuery('');setError('');setLoading(true);setFriends([]);
    void getJson<{friends:Friend[]}>('/api/settings/qq/friends').then(r=>{if(alive)setFriends(r.friends);})
      .catch(e=>{if(alive)setError(String(e));}).finally(()=>{if(alive)setLoading(false);});
    return()=>{alive=false;};
  },[open]);
  const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const shown=friends.filter(f=>terms.every(term=>`${f.nickname} ${f.remark} ${f.friend}`.toLocaleLowerCase().includes(term)));
  async function add() {
    setSaving(true);setError('');
    try {
      const response=await fetch('/api/settings/qq/friends',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({friend_ids:selected})});
      if(!response.ok)throw new Error(await responseError(response));
      const result=await response.json() as {message:string};
      onAdded(result.message);onClose();
    } catch(e) {setError(e instanceof Error?e.message:String(e));} finally {setSaving(false);}
  }
  return <Dialog.Root open={open} onOpenChange={value=>{if(!value&&!saving)onClose();}}>
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="friend-manager-dialog">
      <header><Dialog.Title>管理好友</Dialog.Title><Dialog.Close className="friend-manager-close" disabled={saving} aria-label="关闭管理好友"><X size={18}/></Dialog.Close></header>
      <Dialog.Description>选择要添加到 {characterName} 的好友，添加后可在 QQ 好友页开启接话。</Dialog.Description>
      <label className="friend-manager-search"><Search size={18}/><input autoFocus aria-label="搜索好友昵称、备注或QQ号" placeholder="搜索昵称、备注或 QQ 号" value={query} onChange={e=>setQuery(e.target.value)}/></label>
      {error&&<p role="alert" className="error-notice">{error}</p>}
      <div className="friend-manager-list">
        {loading?<p role="status"><LoaderCircle size={18} className="spinning"/>读取好友列表…</p>:shown.map(f=><label className={'friend-manager-row'+(f.added?' added':'')} key={f.friend}>
          <input type="checkbox" aria-label={`${f.remark||f.nickname||f.friend} ${f.friend}`} disabled={saving||f.added} checked={f.added||selected.includes(f.friend)}
            onChange={e=>setSelected(current=>e.target.checked?[...current,f.friend]:current.filter(id=>id!==f.friend))}/>
          <span><strong>{f.remark||f.nickname||f.friend}</strong><small>{f.remark&&f.nickname&&f.remark!==f.nickname?`${f.nickname} · `:''}{f.friend}</small></span>
          {f.added&&<small>已添加</small>}
        </label>)}
        {!loading&&!error&&!shown.length&&<p>{friends.length?'没有匹配的好友':'暂无好友'}</p>}
      </div>
      <footer><span>已选 {selected.length} 位</span><Dialog.Close disabled={saving}>取消</Dialog.Close><button className="friend-manager-add" disabled={loading||saving||!selected.length} onClick={()=>void add()}>{saving?'正在添加…':'添加所选好友'}</button></footer>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}
