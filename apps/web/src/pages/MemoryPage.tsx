import { useCharacterName, useOwnerName } from '../components/RuntimeStatus';
import * as AlertDialog from '@radix-ui/react-alert-dialog';
import { useContext, useEffect, useRef, useState } from 'react';
import { AppShell, NavigationContext } from '../components/AppShell';
import { ArrowUpRight, NotebookPen, Pause, Pencil, Play, RefreshCw, Save, Trash2 } from 'lucide-react';
import { EmptyState, useDiscardGuard, useNotice } from '../components/ui';
import { getJson, responseError } from '../api/chat';
import '../styles/memory.css';

type Person = {id:string;display_name:string};
type Item = {id:string;subject:string;peer:string;category:string;topic:string;content:string;automatic:string;manual:string;enabled:number;valid:number;revision:number;updated_at:number};
type Source = {id:number;author:string;content:string;timestamp:number};
type Detail = Item & {sources:Source[];history:{id:number;kind:string;snapshot:string;created_at:number}[]};
type Day = {id:string;subject:string;subjects?:string[];day:string;text:string;sources:Source[]};
type Note = {note:string;scope:string;enabled:number;revision:number;history?:{snapshot:string;created_at:number}[]};
type Reply = {id:string;text:string;title:string;created_at:number;annotation:Note|null;
  conversation_id:string;source:string;context:{author:string;text:string}[]};
type Scope = {id:number;name:string;conversation_id:string;total:number;processed:number;status:string};
type Listing = {control:{enabled:number};scopes:Scope[];index:{memory_engine:string;error:string|null}};
type Legacy = {id:number;automatic:string;manual:string;revision:number;valid:number;source_messages:Source[];history:{id:number;kind:string;body:string;created_at:number}[]};
const modes = {people:'人物记忆',days:'日期经历',review:'回复回顾',legacy:'关系与整理记录'};
type Mode = keyof typeof modes;
const today = () => new Date().toLocaleDateString('sv-SE');

async function request(path:string,method:string,body?:object) {
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  if(!r.ok)throw new Error(await responseError(r));
  return r.json() as Promise<unknown>;
}
function Sources({rows}:{rows:Source[]}) {
  const characterName = useCharacterName();
  return <details><summary>原文依据 · {rows.length} 条</summary><div className="context-material">{rows.map(r=><article key={r.id}>
    <small>{r.author==='owner'?'你':r.author==='miko'?characterName:'好友'} · {new Date(r.timestamp).toLocaleString()}</small><p>{r.content}</p>
  </article>)}</div></details>;
}
function ReplyCard({reply,save,open,onDirty,onDelete,showDate=false}:{reply:Reply;save:(path:string,method:string,body:object)=>Promise<void>;
  showDate?:boolean;
  open:()=>void;onDelete:()=>void;onDirty:(id:string,dirty:boolean)=>void}) {
  const characterName = useCharacterName();
  const [note,setNote]=useState(reply.annotation?.note ?? '');
  const [scope,setScope]=useState(reply.annotation?.scope ?? 'example');
  const [busy,setBusy]=useState(false);
  const [editing,setEditing]=useState(false);
  const editorRef=useRef<HTMLDivElement>(null);
  const dirty = note !== (reply.annotation?.note ?? '') || scope !== (reply.annotation?.scope ?? 'example');
  useEffect(()=>{onDirty(reply.id,editing&&dirty);return()=>onDirty(reply.id,false);},[reply.id,dirty,editing]);
  useEffect(()=>{
    if(!editing||busy)return;
    const dismiss=(event:PointerEvent)=>{
      if(event.target instanceof Node && !editorRef.current?.contains(event.target)){
        setNote(reply.annotation?.note ?? '');setScope(reply.annotation?.scope ?? 'example');setEditing(false);
      }
    };
    document.addEventListener('pointerdown',dismiss,true);
    return()=>document.removeEventListener('pointerdown',dismiss,true);
  },[editing,busy,reply.annotation]);
  async function submit(enabled:boolean) {
    setBusy(true);
    try{await save('/api/memory/review/'+encodeURIComponent(reply.id),'PUT',{text:note,scope,revision:reply.annotation?.revision ?? 0,enabled});}
    finally{setBusy(false);}
  }
  return <article className="review-card" id={'review-'+reply.id}>
    <header className="review-card-heading"><span className="review-avatar"><NotebookPen size={18}/></span><div>
      <strong>{reply.source==='qq' ? reply.title : `你与 ${characterName}`}</strong><small>{showDate ? new Date(reply.created_at).toLocaleString('zh-CN',{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : new Date(reply.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}
      {reply.annotation?.enabled ? ' · 已批注' : reply.annotation ? ' · 批注已撤销' : ''}</small></div>
      <button className="text-button" onClick={open}>原对话<ArrowUpRight size={14}/></button><button className="text-button" aria-label="删除回顾" title="删除回顾" disabled={busy} onClick={onDelete}><Trash2 size={16}/></button></header>
    {!!reply.context?.length && <div className="review-context">{reply.context.map((r,i)=><p key={i}><small>{r.author}</small>{r.text}</p>)}</div>}
    <p className="memory-body review-response">{reply.text}</p>
    {editing ? <div ref={editorRef} className="review-feedback-editor"><textarea autoFocus aria-label="回复批注" rows={3} placeholder="写下你对这条回复的反馈" value={note} onChange={e=>setNote(e.target.value)}/>
    <div className="profile-actions"><select aria-label="批注适用范围" value={scope} onChange={e=>setScope(e.target.value)}>
      <option value="example">相关表达示例</option><option value="friend">当前聊天</option><option value="global">通用表达</option></select>
      <button disabled={busy || !note.trim() || (!dirty && !!reply.annotation?.enabled)} className="send-button" onClick={()=>void submit(true)}>
        <Save size={14}/>{busy?'保存中…':'保存批注'}</button>
      {reply.annotation && <button disabled={busy} onClick={()=>void submit(!reply.annotation!.enabled)}>{reply.annotation.enabled?'撤销批注':'恢复批注'}</button>}
    </div></div> : <div className="review-feedback-summary">
      {reply.annotation && <p className="memory-body">{reply.annotation.note}</p>}
      <button className="text-button" onClick={()=>setEditing(true)}><Pencil size={14}/>{reply.annotation?'编辑反馈':'添加反馈'}</button>
    </div>}{reply.annotation && <small>{reply.annotation.enabled?'正在参考这条反馈':'已撤销，记录保留'}</small>}
    {reply.annotation?.history && <details><summary>批注历史</summary>{reply.annotation.history.map((h,i)=>{const previous=JSON.parse(h.snapshot) as Note;return <article key={i}><small>{new Date(h.created_at).toLocaleString()}</small><p>{previous.note}</p><button disabled={busy} onClick={()=>void save('/api/memory/review/'+encodeURIComponent(reply.id),'PUT',{text:previous.note,scope:previous.scope,revision:reply.annotation!.revision,enabled:true})}>恢复这份批注</button></article>;})}</details>}
  </article>;
}

export function MemoryPage({view='memory'}:{view?:'memory'|'review'}) {
  const navigation = useContext(NavigationContext)!;
  const characterName = useCharacterName();
  const ownerName = useOwnerName();
  const [mode,setMode]=useState<Mode>(view==='review'?'review':'people');
  const [listing,setListing]=useState<Listing|null>(null);
  const [people,setPeople]=useState<Person[]>([]);
  const [subject,setSubject]=useState('');
  const [q,setQ]=useState('');
  const [category,setCategory]=useState('');
  const [day,setDay]=useState(()=>view==='review'?navigation.search.get('day') ?? today():today());
  const [items,setItems]=useState<Item[]>([]);
  const [days,setDays]=useState<Day[]>([]);
  const [replies,setReplies]=useState<Reply[]>([]);
  const [selected,setSelected]=useState('');
  const [detail,setDetail]=useState<Detail|null>(null);
  const [manual,setManual]=useState('');
  const [legacy,setLegacy]=useState<Legacy|null>(null);
  const [editCategory,setEditCategory]=useState('');
  const [editTopic,setEditTopic]=useState('');
  const [itemsLoading,setItemsLoading]=useState(true);
  const {notice,setNotice,setFailure,failed}=useNotice();
  const [recordsLoading,setRecordsLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [deleting,setDeleting]=useState<Reply|null>(null);
  const [deletingMemory,setDeletingMemory]=useState<{id:string;kind:'item'|'day'}|null>(null);
  const [refresh,setRefresh]=useState(0);
  const [peer,setPeer]=useState(view==='review' ? navigation.search.get('peer') ?? '' : '');
  const [relation,setRelation]=useState(view==='memory'?navigation.search.get('relation') ?? '':'');
  const [drafts,setDrafts]=useState<Record<string,boolean>>({});
  const [discardVersion,setDiscardVersion]=useState(0);
  const baseline = mode==='legacy' ? legacy?.manual ?? '' : detail?.manual || detail?.automatic || '';
  const manualDirty = mode==='legacy' ? manual!==baseline : mode==='people' && !!detail && (manual!==baseline || editCategory!==detail.category || editTopic!==detail.topic);
  const guard = useDiscardGuard(manualDirty || Object.values(drafts).some(Boolean),()=>{
    setManual(baseline);setEditCategory(detail?.category ?? '');setEditTopic(detail?.topic ?? '');setDrafts({});setDiscardVersion(v=>v+1);
  });
  const onReplyDirty = (id:string,dirty:boolean)=>setDrafts(previous=>previous[id]===dirty?previous:{...previous,[id]:dirty});
  function openReply(reply:Reply) {
    const params = reply.source==='qq' ? {account:reply.conversation_id.split(':')[1],friend:reply.conversation_id.split(':')[2],message:reply.id}
      : {conversation:reply.conversation_id,message:reply.id};
    navigation.navigate(reply.source==='qq'?'qq':'chat',params);
  }
  useEffect(()=>{
    if(navigation.active!==view)return;
    const routeDay=navigation.search.get('day');const routePeer=navigation.search.get('peer');
    if(view==='review' && ((routeDay && routeDay!==day) || (routePeer!==null && routePeer!==peer))) {
      guard.proceed(()=>{if(routeDay)setDay(routeDay);if(routePeer!==null)setPeer(routePeer);});
    }
    const routePerson=navigation.search.get('person');const routeRelation=navigation.search.get('relation') ?? '';
    if(view==='memory' && ((routePerson && routePerson!==subject) || routeRelation!==relation)) {
      guard.proceed(()=>{if(routePerson)setSubject(routePerson);setRelation(routeRelation);setSelected('');setDetail(null);setManual('');});
    }
  },[navigation.active,navigation.search]);
  useEffect(()=>{
    const focus=navigation.search.get('reply');
    if(navigation.active===view && view==='review' && focus && replies.length) document.getElementById('review-'+focus)?.scrollIntoView({block:'center'});
  },[replies,navigation.active,navigation.search]);
  const name=(id:string)=>id==='miko' ? characterName : id==='owner' ? ownerName : people.find(p=>p.id===id)?.display_name ?? id;
  useEffect(()=>{
    let alive=true;
    setItemsLoading(true);
    void Promise.all([getJson<Listing>('/api/memory'),getJson<{people:Person[]}>('/api/memory/objects'),
      getJson<{items:Item[]}>('/api/memory/items?q='+encodeURIComponent(q)+'&subject='+encodeURIComponent(subject)+(relation?'&peer='+encodeURIComponent(relation):''))])
      .then(([a,b,c])=>{if(alive){setListing(a);setPeople(b.people);setItems(c.items);}}).catch(e=>{if(alive)setFailure(e);}).finally(()=>{if(alive)setItemsLoading(false);});
    return ()=>{alive=false;};
  },[q,subject,refresh,relation,characterName,ownerName]);
  useEffect(()=>{
    let alive=true;
    setRecordsLoading(true);
    if(mode==='days')void getJson<{days:Day[]}>('/api/memory/days?day='+day+'&subject='+encodeURIComponent(subject)).then(r=>{if(alive)setDays(r.days);}).catch(e=>{if(alive)setFailure(e);}).finally(()=>{if(alive)setRecordsLoading(false);});
    if(mode==='review')void getJson<{replies:Reply[]}>('/api/memory/review?day='+day).then(r=>{if(alive)setReplies(r.replies);}).catch(e=>{if(alive)setFailure(e);}).finally(()=>{if(alive)setRecordsLoading(false);});
    return ()=>{alive=false;};
  },[mode,day,subject,refresh,characterName,ownerName]);
  useEffect(()=>{
    let alive=true;setDetail(null);
    if(selected && mode==='people')void getJson<Detail>('/api/memory/items/'+selected).then(r=>{if(alive){setDetail(r);setManual(r.content);setEditCategory(r.category);setEditTopic(r.topic);}}).catch(e=>{if(alive)setFailure(e);});
    return ()=>{alive=false;};
  },[selected,mode,refresh]);
  async function mutate(path:string,method:string,body?:object) {
    setBusy(true);setNotice('');
    try{await request(path,method,body);setRefresh(v=>v+1);setNotice('已保存');
      if(mode==='legacy' && legacy)setLegacy(await getJson<Legacy>('/api/memory/scopes/'+legacy.id));}
    catch(e){setFailure(e);}finally{setBusy(false);}
  }
  async function removeReview() {
    if (!deleting) return;
    setBusy(true);
    try {
      await request('/api/memory/review/'+encodeURIComponent(deleting.id),'DELETE');
      onReplyDirty(deleting.id,false);setDeleting(null);setRefresh(v=>v+1);setNotice('回顾及批注已删除');
    } catch(e) {setFailure(e);} finally {setBusy(false);}
  }
  async function removeMemory() {
    if (!deletingMemory) return;
    setBusy(true);setNotice('');
    try {
      await request('/api/memory/'+(deletingMemory.kind==='item'?'items/':'days/')+encodeURIComponent(deletingMemory.id),'DELETE');
      if (deletingMemory.kind==='item') {setSelected('');setDetail(null);setManual('');}
      setDeletingMemory(null);setRefresh(v=>v+1);setNotice('记忆已删除');
    } catch(e) {setFailure(e);} finally {setBusy(false);}
  }
  async function openScope(id:number) {
    try{const r=await getJson<Legacy>('/api/memory/scopes/'+id);setLegacy(r);setManual(r.manual);}
    catch(e){setFailure(e);}
  }
  return <AppShell view={view} sidebar={<section className="profile-browser" aria-label={view==='review'?'回顾筛选':'记忆导航'}>
    {view==='memory' && <div className="memory-modes">{Object.entries(modes).filter(([id])=>id!=='review').map(([id,label])=><button key={id} className={mode===id?'active':''}
      onClick={()=>guard.proceed(()=>{setMode(id as Mode);setManual('');setDetail(null);setSelected('');})}>{label}</button>)}</div>}
    {relation && <div className="relation-filter"><span>当前好友：{name('qq:'+relation.split(':')[2])}</span>
      <button className="text-button" onClick={()=>guard.proceed(()=>{setRelation('');navigation.replace({relation:undefined});})}>查看全部</button></div>}
    {mode==='people' && <><div className="profile-filters"><input aria-label="搜索人物记忆" placeholder="搜索记忆" value={q} onChange={e=>setQ(e.target.value)}/>
      <select aria-label="记忆人物" value={subject} onChange={e=>{const value=e.target.value;guard.proceed(()=>{setSubject(value);setCategory('');setSelected('');setDetail(null);setManual('');});}}><option value="">全部人物</option>{people.map(p=><option key={p.id} value={p.id}>{name(p.id)}</option>)}</select>
      </div>
      <nav className="memory-category-list" aria-label="记忆分类">
        {[...new Set(items.map(r=>r.category))].map(c=><button key={c} className={category===c?'active':''}
          onClick={()=>guard.proceed(()=>{setCategory(category===c?'':c);setSelected('');setDetail(null);setManual('');})}>
          <span>{c}</span><small>{items.filter(r=>r.category===c).length}</small></button>)}
      </nav></>}

    {(mode==='days' || mode==='review') && <div className="profile-filters"><div className="review-date-filter"><label>{day==='all'?'全部日期':'日期'}<input aria-label="回顾日期" type="date" value={day==='all'?'':day} onChange={e=>{const value=e.target.value || (mode==='review'?'all':'');if(value)guard.proceed(()=>{setDay(value);if(view==='review')navigation.replace({day:value,reply:undefined});});}}/></label>
      {mode==='review' && <button type="button" aria-pressed={day==='all'} onClick={()=>guard.proceed(()=>{setDay('all');navigation.replace({day:'all',reply:undefined});})}>全部</button>}</div>
      {mode==='review' && <><label>对话<select aria-label="回顾对话" value={peer} onChange={e=>{const value=e.target.value;guard.proceed(()=>{setPeer(value);navigation.replace({peer:value,reply:undefined});});}}>
        <option value="">全部对话</option>{peer && !replies.some(r=>r.conversation_id===peer) && <option value={peer}>当前对话</option>}
        {[...new Map(replies.map(r=>[r.conversation_id,r.title])).entries()].map(([id,title])=><option key={id} value={id}>{title}</option>)}</select></label>
        <div className="review-day-summary"><NotebookPen size={20}/><strong>{replies.filter(r=>!peer || r.conversation_id===peer).length}</strong><span>条回复</span></div></>}
      {mode==='days' && <select aria-label="经历人物" value={subject} onChange={e=>setSubject(e.target.value)}><option value="">全部人物</option>{people.map(p=><option key={p.id} value={p.id}>{name(p.id)}</option>)}</select>}
      </div>}
    {mode==='legacy' && listing?.scopes.map(s=><button key={s.id} className={'profile-card '+(legacy?.id===s.id?'active':'')} onClick={()=>guard.proceed(()=>void openScope(s.id))}><strong>{s.name}</strong><small>{s.conversation_id.startsWith('owner|')?'旧版关系记录':'待整理 '+(s.total-s.processed)+' 条 · '+(s.status==='updating'?'正在整理':s.status==='update_failed'?'整理失败':'收集中')}</small></button>)}
  </section>}><main className="main-panel memory-panel"><header className="page-header"><h1>{modes[mode]}</h1>
    <div className="profile-actions"><button disabled={busy} onClick={()=>guard.proceed(()=>setRefresh(v=>v+1))}><RefreshCw size={14}/>刷新</button>
      {view==='memory' && <button disabled={busy || !listing} className="send-button" onClick={()=>void mutate('/api/memory/control','POST',{enabled:!listing?.control.enabled})}>
      {listing?.control.enabled?<Pause size={14}/>:<Play size={14}/>} {listing?.control.enabled?'暂停自动记忆':'开启自动记忆'}</button>}</div></header>
    {(notice || listing?.index.error) && <p className={failed || listing?.index.error?'profile-notice error-notice':'profile-notice'}
      role={failed || listing?.index.error?'alert':'status'}>{notice || listing?.index.error}</p>}
    <div className="profile-workspace"><section className="profile-editor">
      {mode==='people' && (itemsLoading ? <EmptyState loading title="正在读取记忆…"/> : !items.length ? <EmptyState title="暂无人物记忆"/> :
        [...new Set(items.filter(r=>!category || r.category===category).map(r=>r.category))].map(group=><section className="memory-entry-group" key={group}>
          <h2>{group}<small>{items.filter(r=>r.category===group).length}</small></h2>
          <div className="memory-entry-cloud">{items.filter(r=>r.category===group).map(r=>selected===r.id ?
            <article className="memory-entry-edit" key={r.id}>{!detail ? <EmptyState loading title="正在读取条目…"/> : <>
              <form onSubmit={e=>{e.preventDefault();void mutate('/api/memory/items/'+detail.id,'PATCH',
                {text:manual,category:editCategory,topic:editTopic,revision:detail.revision,enabled:!!detail.enabled});}}>
                <small>{name(detail.subject)}</small>
                <label>内容<textarea aria-label="记忆内容" rows={3} value={manual} onChange={e=>setManual(e.target.value)}/></label>
                <div className="memory-entry-fields"><label>分类<input aria-label="记忆分类" list="memory-categories" maxLength={80} value={editCategory} onChange={e=>setEditCategory(e.target.value)}/></label>
                  <label>条目名称<input aria-label="记忆条目名称" maxLength={100} value={editTopic} onChange={e=>setEditTopic(e.target.value)}/></label></div>
                <datalist id="memory-categories">{[...new Set(items.map(i=>i.category))].map(c=><option key={c} value={c}/>)}</datalist>
                <div className="profile-actions"><button className="send-button" disabled={busy || !manual.trim() || !editCategory.trim() || !editTopic.trim() || !manualDirty} type="submit"><Save size={14}/>{busy?'保存中…':'保存'}</button>
                  <button type="button" disabled={busy} onClick={()=>guard.proceed(()=>{setSelected('');setDetail(null);setManual('');})}>关闭</button>
                  <button type="button" disabled={busy} onClick={()=>void mutate('/api/memory/items/'+detail.id,'PATCH',{text:detail.manual,revision:detail.revision,enabled:!detail.enabled})}>{detail.enabled?'停用':'恢复使用'}</button>
                  <button type="button" className="text-button" disabled={busy} onClick={()=>setDeletingMemory({id:detail.id,kind:'item'})}><Trash2 size={16}/>删除</button></div>
              </form>
              <Sources rows={detail.sources}/>
              <details><summary>版本记录 · {detail.history.length}</summary>{detail.history.map(h=>{const previous=JSON.parse(h.snapshot) as Item;return <div className="memory-version" key={h.id}>
                <small>{new Date(h.created_at).toLocaleString()} · {previous.category} / {previous.topic}</small><p>{previous.manual || previous.automatic}</p>
                <button disabled={busy} onClick={()=>guard.proceed(()=>void mutate('/api/memory/items/'+detail.id+'/restore','POST',{history_id:h.id,revision:detail.revision}))}>恢复此版本</button></div>;})}</details>
              {!detail.valid && !detail.manual && <p className="retrieval-note">原文已变化，这条记忆暂未用于聊天。</p>}
            </>}</article> : <article className={'memory-entry-tile'+(!r.enabled?' inactive':'')} key={r.id}>
              <button className="memory-entry-content" title={`${name(r.subject)} · ${r.topic}`} onClick={()=>guard.proceed(()=>{setManual('');setDetail(null);setSelected(r.id);})}>
                <small>{name(r.subject)}{!r.enabled?' · 已停用':''}</small><span>{r.content}</span></button>
              <div className="memory-entry-overlay"><button aria-label="编辑记忆" title="编辑" disabled={busy} onClick={()=>guard.proceed(()=>{setManual('');setDetail(null);setSelected(r.id);})}><Pencil size={16}/></button>
                <button aria-label="删除记忆" title="删除" disabled={busy} onClick={()=>setDeletingMemory({id:r.id,kind:'item'})}><Trash2 size={16}/></button></div>
            </article>)}</div>
        </section>))}
      {mode==='days' && (days.length?days.map(r=><article className="review-card" key={r.id}><header className="review-card-heading"><h2>{(r.subjects || [r.subject]).map(name).join('、')}</h2><button className="text-button" disabled={busy} aria-label="删除经历" title="删除经历" onClick={()=>setDeletingMemory({id:r.id,kind:'day'})}><Trash2 size={16}/></button></header><p className="memory-body">{r.text}</p><Sources rows={r.sources}/></article>):<p>这一天尚无整理出的经历。</p>)}
      {mode==='review' && (recordsLoading?<EmptyState loading title="正在读取回复…"/>:replies.filter(r=>!peer || r.conversation_id===peer).length?
        replies.filter(r=>!peer || r.conversation_id===peer).map(r=><ReplyCard key={r.id+':'+(r.annotation?.revision ?? 0)+':'+discardVersion}
          reply={r} showDate={day==='all'} save={mutate} open={()=>openReply(r)} onDirty={onReplyDirty} onDelete={()=>setDeleting(r)}/>):
        <EmptyState title={day==='all'?'暂无已完成的回复':'这一天暂无已完成的回复'}/>)}
      {mode==='legacy' && (!legacy?<p>选择一段关系，查看整理进度和原有记录。</p>:<><h2>关系整理记录</h2><p className="memory-body">{legacy.automatic || '尚无整理记录'}</p>
        <h3>我的关系补充</h3><textarea rows={5} value={manual} onChange={e=>setManual(e.target.value)}/><div className="profile-actions"><button disabled={busy} className="send-button" onClick={()=>void mutate('/api/memory/scopes/'+legacy.id,'PATCH',{text:manual,revision:legacy.revision})}>保存补充</button>
          <button disabled={busy || !listing?.control.enabled || !listing.scopes.find(s=>s.id===legacy.id)?.conversation_id.startsWith('miko|')} onClick={()=>void mutate('/api/memory/scopes/'+legacy.id+'/consolidate','POST')}>现在整理待处理消息</button></div>
        <Sources rows={legacy.source_messages}/>{legacy.history.map(h=><details key={h.id}><summary>{h.kind==='manual'?'关系补充':'自动整理'} · {new Date(h.created_at*1000).toLocaleString()}</summary><p className="memory-body">{h.body}</p></details>)}</>)}
    </section></div>{guard.dialog}<AlertDialog.Root open={!!deleting || !!deletingMemory} onOpenChange={open=>{if(!open && !busy){setDeleting(null);setDeletingMemory(null);}}}>
      <AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay"/><AlertDialog.Content className="confirm-dialog">
        <AlertDialog.Title>{deletingMemory ? deletingMemory.kind==='item'?'删除这条记忆？':'删除这条经历？' : '删除这条回顾？'}</AlertDialog.Title>
        <AlertDialog.Description>{deletingMemory ? '删除后不再参与检索，原聊天记录会保留。' : '这条回顾及其批注将被删除。聊天记录会保留，已发送的 QQ 消息不会撤回。'}</AlertDialog.Description>
        {failed && <p role="alert">{notice}</p>}
        <div className="dialog-actions"><AlertDialog.Cancel className="ui-button" disabled={busy}>取消</AlertDialog.Cancel>
          <button className="ui-button warning" disabled={busy} onClick={()=>void (deletingMemory ? removeMemory() : removeReview())}>{busy?'正在删除…':'确认删除'}</button></div>
      </AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root></main></AppShell>;
}
