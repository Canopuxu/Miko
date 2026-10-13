import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';

type Person = {id:string;display_name:string};
type Item = {id:string;subject:string;peer:string;category:string;topic:string;content:string;automatic:string;manual:string;enabled:number;valid:number;revision:number;updated_at:number};
type Source = {id:number;author:string;content:string;timestamp:number};
type Detail = Item & {sources:Source[];history:{id:number;kind:string;snapshot:string;created_at:number}[]};
type Day = {id:string;subject:string;day:string;text:string;sources:Source[]};
type Note = {note:string;scope:string;enabled:number;revision:number;history?:{snapshot:string;created_at:number}[]};
type Reply = {id:string;text:string;title:string;created_at:number;annotation:Note|null};
type Scope = {id:number;name:string;conversation_id:string;total:number;processed:number;status:string};
type Listing = {control:{enabled:number};scopes:Scope[];index:{memory_engine:string;error:string|null}};
type Legacy = {id:number;automatic:string;manual:string;revision:number;valid:number;source_messages:Source[];history:{id:number;kind:string;body:string;created_at:number}[]};
type Proposal = {subject:string;peer:string;category:string;items:{sub_topic:string;memo:string}[];revisions:Record<string,number>};
const modes = {people:'人物记忆',days:'日期经历',review:'回复回顾',legacy:'关系与整理记录'};
type Mode = keyof typeof modes;
const today = () => new Date().toLocaleDateString('sv-SE');

async function request(path:string,method:string,body?:object) {
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
  if(!r.ok)throw new Error(await responseError(r));
  return r.json() as Promise<unknown>;
}
function Sources({rows}:{rows:Source[]}) {
  return <details><summary>原文依据 · {rows.length} 条</summary><div className="context-material">{rows.map(r=><article key={r.id}>
    <small>{r.author==='owner'?'你':r.author==='miko'?'Miko':'好友'} · {new Date(r.timestamp).toLocaleString()}</small><p>{r.content}</p>
  </article>)}</div></details>;
}
function ReplyCard({reply,save}:{reply:Reply;save:(path:string,method:string,body:object)=>Promise<void>}) {
  const [note,setNote]=useState(reply.annotation?.note ?? '');
  const [scope,setScope]=useState(reply.annotation?.scope ?? 'example');
  const [busy,setBusy]=useState(false);
  async function submit(enabled:boolean) {
    setBusy(true);
    try{await save('/api/memory/review/'+encodeURIComponent(reply.id),'PUT',{text:note,scope,revision:reply.annotation?.revision ?? 0,enabled});}
    finally{setBusy(false);}
  }
  return <article className="review-card"><small>{reply.title} · {new Date(reply.created_at).toLocaleTimeString()}</small><p className="memory-body">{reply.text}</p>
    <textarea aria-label="回复批注" rows={3} placeholder="写下你对这条回复的反馈" value={note} onChange={e=>setNote(e.target.value)}/>
    <div className="profile-actions"><select aria-label="批注适用范围" value={scope} onChange={e=>setScope(e.target.value)}>
      <option value="example">相关表达示例</option><option value="friend">当前聊天</option><option value="global">通用表达</option></select>
      <button disabled={busy || !note.trim()} className="send-button" onClick={()=>void submit(true)}>保存批注</button>
      {reply.annotation && <button disabled={busy} onClick={()=>void submit(!reply.annotation!.enabled)}>{reply.annotation.enabled?'撤销批注':'恢复批注'}</button>}
    </div>{reply.annotation && <small>{reply.annotation.enabled?'正在参考这条反馈':'已撤销，记录保留'}</small>}
    {reply.annotation?.history && <details><summary>批注历史</summary>{reply.annotation.history.map((h,i)=>{const previous=JSON.parse(h.snapshot) as Note;return <article key={i}><small>{new Date(h.created_at).toLocaleString()}</small><p>{previous.note}</p><button disabled={busy} onClick={()=>void save('/api/memory/review/'+encodeURIComponent(reply.id),'PUT',{text:previous.note,scope:previous.scope,revision:reply.annotation!.revision,enabled:true})}>恢复这份批注</button></article>;})}</details>}
  </article>;
}

export function MemoryPage() {
  const [mode,setMode]=useState<Mode>('people');
  const [listing,setListing]=useState<Listing|null>(null);
  const [people,setPeople]=useState<Person[]>([]);
  const [subject,setSubject]=useState('');
  const [q,setQ]=useState('');
  const [category,setCategory]=useState('');
  const [day,setDay]=useState(today);
  const [items,setItems]=useState<Item[]>([]);
  const [days,setDays]=useState<Day[]>([]);
  const [replies,setReplies]=useState<Reply[]>([]);
  const [selected,setSelected]=useState('');
  const [detail,setDetail]=useState<Detail|null>(null);
  const [manual,setManual]=useState('');
  const [legacy,setLegacy]=useState<Legacy|null>(null);
  const [brief,setBrief]=useState('');
  const [proposal,setProposal]=useState<Proposal|null>(null);
  const [notice,setNotice]=useState('');
  const [busy,setBusy]=useState(false);
  const [refresh,setRefresh]=useState(0);
  const name=(id:string)=>people.find(p=>p.id===id)?.display_name ?? id;
  useEffect(()=>{
    let alive=true;
    void Promise.all([getJson<Listing>('/api/memory'),getJson<{people:Person[]}>('/api/memory/objects'),
      getJson<{items:Item[]}>('/api/memory/items?q='+encodeURIComponent(q)+'&subject='+encodeURIComponent(subject))])
      .then(([a,b,c])=>{if(alive){setListing(a);setPeople(b.people);setItems(c.items);}}).catch(e=>setNotice(String(e)));
    return ()=>{alive=false;};
  },[q,subject,refresh]);
  useEffect(()=>{
    let alive=true;
    if(mode==='days')void getJson<{days:Day[]}>('/api/memory/days?day='+day+'&subject='+encodeURIComponent(subject)).then(r=>{if(alive)setDays(r.days);}).catch(e=>setNotice(String(e)));
    if(mode==='review')void getJson<{replies:Reply[]}>('/api/memory/review?day='+day).then(r=>{if(alive)setReplies(r.replies);}).catch(e=>setNotice(String(e)));
    return ()=>{alive=false;};
  },[mode,day,subject,refresh]);
  useEffect(()=>{
    let alive=true;setDetail(null);setBrief('');
    if(selected && mode==='people')void getJson<Detail>('/api/memory/items/'+selected).then(r=>{if(alive){setDetail(r);setManual(r.manual || r.automatic);}}).catch(e=>setNotice(String(e)));
    return ()=>{alive=false;};
  },[selected,mode,refresh]);
  async function mutate(path:string,method:string,body?:object) {
    setBusy(true);setNotice('');
    try{await request(path,method,body);setRefresh(v=>v+1);setNotice('已保存');
      if(mode==='legacy' && legacy)setLegacy(await getJson<Legacy>('/api/memory/scopes/'+legacy.id));}
    catch(e){setNotice(String(e));}finally{setBusy(false);}
  }
  async function special(path:string,body?:object) {
    setBusy(true);setNotice('');
    try{const r=await request(path,'POST',body);if(path==='/api/memory/organize')setProposal(r as Proposal);else setBrief((r as {summary:string}).summary);}
    catch(e){setNotice(String(e));}finally{setBusy(false);}
  }
  async function openScope(id:number) {
    try{const r=await getJson<Legacy>('/api/memory/scopes/'+id);setLegacy(r);setManual(r.manual);}
    catch(e){setNotice(String(e));}
  }
  return <AppShell view="memory" sidebar={<section className="profile-browser" aria-label="记忆导航">
    <div className="memory-modes">{Object.entries(modes).map(([id,label])=><button key={id} className={mode===id?'active':''} onClick={()=>{setMode(id as Mode);setProposal(null);}}>{label}</button>)}</div>
    {mode==='people' && <><div className="profile-filters"><input aria-label="搜索人物记忆" placeholder="搜索记忆" value={q} onChange={e=>setQ(e.target.value)}/>
      <select aria-label="记忆人物" value={subject} onChange={e=>{setSubject(e.target.value);setCategory('');}}><option value="">全部人物</option>{people.map(p=><option key={p.id} value={p.id}>{p.display_name}</option>)}</select>
      <select aria-label="记忆分类" value={category} onChange={e=>setCategory(e.target.value)}><option value="">全部分类</option>{[...new Set(items.map(r=>r.category))].map(c=><option key={c}>{c}</option>)}</select></div>
      {items.filter(r=>!category || r.category===category).map(r=><button key={r.id} className={'profile-card '+(selected===r.id?'active':'')} onClick={()=>setSelected(r.id)}><strong>{name(r.subject)} · {r.topic}</strong><small>{r.category}{r.manual?' · 已有你的修正':''}{!r.enabled?' · 已停用':''}</small><small>{r.content.slice(0,70)}</small></button>)}
      {!items.length && <p>聊天整理后，人物记忆会出现在这里。核心设定仍在“设定与资料”中。</p>}</>}
    {(mode==='days' || mode==='review') && <div className="profile-filters"><label>日期<input aria-label="回顾日期" type="date" value={day} onChange={e=>setDay(e.target.value)}/></label>
      {mode==='days' && <select aria-label="经历人物" value={subject} onChange={e=>setSubject(e.target.value)}><option value="">全部人物</option>{people.map(p=><option key={p.id} value={p.id}>{p.display_name}</option>)}</select>}
      <p>{mode==='days'?'按提及日期查看经历，正文保留事件发生时间。':'查看当天已完成的 Miko 回复，并留下可撤销的批注。'}</p></div>}
    {mode==='legacy' && listing?.scopes.map(s=><button key={s.id} className={'profile-card '+(legacy?.id===s.id?'active':'')} onClick={()=>void openScope(s.id)}><strong>{s.name}</strong><small>{s.conversation_id.startsWith('owner|')?'旧版关系记录':'待整理 '+(s.total-s.processed)+' 条 · '+(s.status==='updating'?'正在整理':s.status==='update_failed'?'整理失败':'收集中')}</small></button>)}
  </section>}><main className="main-panel memory-panel"><header className="page-header"><div><h1>{modes[mode]}</h1><p>人物认识、共同经历与原文依据</p></div>
    <div className="profile-actions"><button disabled={busy} onClick={()=>setRefresh(v=>v+1)}>刷新</button><button disabled={busy || !listing} className="send-button" onClick={()=>void mutate('/api/memory/control','POST',{enabled:!listing?.control.enabled})}>{listing?.control.enabled?'暂停自动记忆':'开启自动记忆'}</button></div></header>
    {(notice || listing?.index.error) && <p className="profile-notice" role="status">{notice || listing?.index.error}</p>}
    <div className="profile-workspace"><section className="profile-editor">
      {mode==='people' && (!detail?<p>选择人物条目，查看自动认识、原文和历史。</p>:<>
        <h2>{name(detail.subject)} · {detail.category} / {detail.topic}</h2><small>更新于 {new Date(detail.updated_at).toLocaleString()}</small>
        <h3>自动认识</h3><p className="memory-body">{detail.automatic}</p>{!detail.valid && <p>原文已变化，自动认识暂不用于聊天。你的修正仍保留。</p>}
        <h3>我的修正</h3><textarea aria-label="人物记忆修正" rows={5} value={manual} onChange={e=>setManual(e.target.value)}/>
        <div className="profile-actions"><button disabled={busy} className="send-button" onClick={()=>void mutate('/api/memory/items/'+detail.id,'PATCH',{text:manual,revision:detail.revision,enabled:true})}>保存修正</button>
          <button disabled={busy} onClick={()=>void mutate('/api/memory/items/'+detail.id,'PATCH',{text:'',revision:detail.revision,enabled:!!detail.enabled})}>撤销人工修正</button>
          <button disabled={busy} onClick={()=>void mutate('/api/memory/items/'+detail.id,'PATCH',{text:detail.manual,revision:detail.revision,enabled:!detail.enabled})}>{detail.enabled?'停用条目':'恢复条目'}</button></div>
        <Sources rows={detail.sources}/><h3>版本记录</h3>{detail.history.map(h=>{const r=JSON.parse(h.snapshot) as Item;return <details key={h.id}><summary>{h.kind.includes('manual')?'人工修正':'整理记录'} · {new Date(h.created_at).toLocaleString()}</summary><p className="memory-body">{r.manual || r.automatic}</p><button disabled={busy} onClick={()=>void mutate('/api/memory/items/'+detail.id+'/restore','POST',{history_id:h.id,revision:detail.revision})}>恢复此版本为人工修正</button></details>;})}
        <div className="profile-actions"><button disabled={busy} onClick={()=>void special('/api/memory/items/'+detail.id+'/summary')}>生成浏览摘要</button>
          <button disabled={busy} onClick={()=>void special('/api/memory/organize',{subject:detail.subject,peer:detail.peer,category:detail.category})}>整理此分类</button></div>
        {brief && <article><h3>浏览摘要</h3><p>{brief}</p><small>原条目仍保留，聊天使用完整资料。</small></article>}
        {proposal && <article><h3>分类整理方案</h3>{proposal.items.map(r=><p key={r.sub_topic}><strong>{r.sub_topic}</strong> · {r.memo}</p>)}<button disabled={busy || !proposal.items.length} className="send-button" onClick={()=>{void mutate('/api/memory/organize/apply','POST',proposal);setProposal(null);}}>采用方案并保留旧版本</button></article>}
      </>)}
      {mode==='days' && (days.length?days.map(r=><article className="review-card" key={r.id}><h2>{name(r.subject)}</h2><p className="memory-body">{r.text}</p><Sources rows={r.sources}/></article>):<p>这一天尚无整理出的经历。</p>)}
      {mode==='review' && (replies.length?replies.map(r=><ReplyCard key={r.id+':'+(r.annotation?.revision ?? 0)} reply={r} save={mutate}/>):<p>这一天尚无已完成的回复。</p>)}
      {mode==='legacy' && (!legacy?<p>选择一段关系，查看整理进度和原有记录。</p>:<><h2>关系整理记录</h2><p className="memory-body">{legacy.automatic || '尚无整理记录'}</p>
        <h3>我的关系补充</h3><textarea rows={5} value={manual} onChange={e=>setManual(e.target.value)}/><div className="profile-actions"><button disabled={busy} className="send-button" onClick={()=>void mutate('/api/memory/scopes/'+legacy.id,'PATCH',{text:manual,revision:legacy.revision})}>保存补充</button>
          <button disabled={busy || !listing?.control.enabled || !listing.scopes.find(s=>s.id===legacy.id)?.conversation_id.startsWith('miko|')} onClick={()=>void mutate('/api/memory/scopes/'+legacy.id+'/consolidate','POST')}>现在整理待处理消息</button></div>
        <Sources rows={legacy.source_messages}/>{legacy.history.map(h=><details key={h.id}><summary>{h.kind==='manual'?'关系补充':'自动整理'} · {new Date(h.created_at*1000).toLocaleString()}</summary><p className="memory-body">{h.body}</p></details>)}</>)}
    </section></div></main></AppShell>;
}
