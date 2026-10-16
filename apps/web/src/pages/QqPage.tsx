import { AppShell, NavigationContext } from '../components/AppShell';
import { useContext, useEffect, useRef, useState } from 'react';
import { BookOpen, ImagePlus, NotebookPen, X } from 'lucide-react';
import { EmptyState, useDiscardGuard } from '../components/ui';
import { getJson, responseError, type Message } from '../api/chat';
import { StickerImport, StickerLibrary } from './StickerLibrary';
import { GroupLanguage } from './GroupLanguage';
import { FriendManager } from '../components/FriendManager';
import { FriendRelationships } from './FriendRelationships';

type Friend = { account: string; friend: string; name: string; enabled: number; blocked: number;
  auto_reply_idle:number; chat_type:number; natural_chat:number;
  state: string; mode: string; note: string; due_at: number | null;
  owner_relationship: string; miko_relationship: string; miko_introduced: number; intro_receipt_id: number | null };
type Run = { id: string; state: string; text: string; error: string | null; created_at: number;
  bubbles: { position: number; text: string; state: string; receipt_id: number | null; error: string | null }[] };
type QqMessage = Message & { message_id: number; recalled: boolean;
  segments: { type: string; message_id?: number; sticker_id?: string }[];
  quoted_message?: { text?: string; message_id: number } };
type QqStatus = { character_name:string; receiver: { state: string; account: string; note: string }; friends: Friend[] };
type Detail = { messages: QqMessage[]; runs: Run[]; reactions?: {message_id:number;name:string;reaction_id:string}[] };
const states: Record<string, string> = { disabled: '已关闭', blocked: '等待核对', waiting: '等待接话',
  generating: '正在生成', sending: '正在发送', chatting: 'Miko 接话中', listening: '等待新消息',
  sent: '已发送', skipped: '无需回复', stale: '旧回复已作废', error: '失败', unknown: '发送结果待核对', interrupted: '后台中断',
  pending: '待发送', preparing: '准备发送', cancelled: '已取消', between_bubbles: '接话中' };
const timeLabel = (stamp: number) => new Date(stamp).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export function QqPage() {
  const navigation = useContext(NavigationContext)!;
  const [status, setStatus] = useState<QqStatus | null>(null);
  const [selected, setSelected] = useState(new URLSearchParams(location.search).get('friend'));
  const [groupMode,setGroupMode]=useState(selected?.startsWith('group-')??false);
  const [manager,setManager]=useState(false);
  const [notice,setNotice]=useState('');
  const [stickerVersion,setStickerVersion]=useState(0);
  const [collecting,setCollecting]=useState<{account:string;imageUrl:string;collectUrl:string}|null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [changing, setChanging] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const friend = status?.friends.find(item => item.friend === selected);
  const latestRun = detail?.runs[0];
  const replyNotice = latestRun?.error === friend?.note ? latestRun?.error : null;
  const characterName = status?.character_name ?? 'Miko';
  const stateLabel = (state:string) => state==='chatting' ? `${characterName} 接话中` : states[state] ?? state;
  const [relationshipDirty,setRelationshipDirty]=useState(false);
  const guard = useDiscardGuard(relationshipDirty);
  useEffect(()=>{
    const id=navigation.search.get('friend');
    if(navigation.active==='qq' && id && id!==selected) guard.proceed(()=>select(id,true));
  },[navigation.active,navigation.search]);

  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        const value = await getJson<QqStatus>('/api/qq/status');
        if (!alive) return;
        setStatus(value);
        const current = value.friends.find(item => item.friend === selected);
        if (current) {
          const next = await getJson<Detail>(`/api/qq/friends/${current.account}/${current.friend}`);
          if (alive) setDetail(next);
        }
      } catch (cause) { if (alive) setError(String(cause)); }
    }
    void refresh();
    const timer = setInterval(refresh, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, [selected]);

  useEffect(() => {
    if(navigation.active!=='qq')return;
    const focus=navigation.search.get('message');
    const target=focus?document.getElementById('message-'+focus):null;
    if(target)target.scrollIntoView({block:'center'});else bottom.current?.scrollIntoView({block:'end'});
  }, [selected, detail?.messages.length,navigation.active,navigation.search]);

  function select(id: string,preserveFocus=false) {
    setSelected(id); setGroupMode(id.startsWith('group-')); setDetail(null); setError('');
    navigation.replace({friend:id,...(preserveFocus?{}:{message:undefined})});
  }

  async function toggle(enabled: boolean) {
    if (!friend) return;
    setChanging(true); setError('');
    try {
      const response = await fetch(`/api/qq/friends/${friend.account}/${friend.friend}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setStatus(await response.json());
    } catch (cause) { setError(String(cause)); }
    finally { setChanging(false); }
  }

  async function toggleIdle(auto_reply_idle:boolean) {
    if(!friend)return;
    setChanging(true);setError('');
    try {
      const response=await fetch(`/api/qq/friends/${friend.account}/${friend.friend}/options`,{
        method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(friend.chat_type===2?{natural_chat:auto_reply_idle}:{auto_reply_idle})});
      if(!response.ok)throw new Error(await responseError(response));
      setStatus(await response.json());
    } catch(cause) {setError(String(cause));} finally {setChanging(false);}
  }

  return <AppShell view="qq" sidebar={<>
      <div className="qq-chat-tabs"><button className={!groupMode?'active':''} onClick={()=>guard.proceed(()=>{setGroupMode(false);setSelected(null);setDetail(null);})}>私聊</button><button className={groupMode?'active':''} onClick={()=>guard.proceed(()=>{setGroupMode(true);setSelected(null);setDetail(null);})}>群聊</button></div>
      <button className="new-chat" onClick={()=>setManager(true)}>＋ 添加{groupMode?'群聊':'好友'}</button>
      <nav className="conversation-list" aria-label={groupMode?'QQ 群聊':'QQ 私聊'}>
        {status?.friends.filter(item=>(item.chat_type===2)===groupMode).map(item => <button key={`${item.account}:${item.friend}`}
          className={`conversation-item ${item.friend === selected ? 'active' : ''}`} onClick={() => {if(item.friend!==selected)guard.proceed(()=>select(item.friend));}}>
          <span className="conversation-title">{item.name}</span>
          <span className="conversation-preview">{stateLabel(item.state)}</span>
        </button>)}
      </nav>
    </>}>
    <main className="main-panel">
      <header className="page-header"><div><h1>{friend?.name ?? (groupMode?'QQ 群聊':'QQ 私聊')}</h1>
        <p>{friend?.friend.replace('group-','') ?? (groupMode?'选择一个群聊查看接话状态':'选择一位好友查看接话状态')}</p></div>
        <div className="header-actions">{friend && <><button className="ui-button" onClick={()=>navigation.navigate('review',
          {day:new Date().toLocaleDateString('sv-SE'),peer:`qq:${friend.account}:${friend.friend}`,reply:undefined})}><NotebookPen size={15}/>回顾</button>
          <button className="ui-button" onClick={()=>navigation.navigate('memory',
            {person:'miko',relation:`qq:${friend.account}:${friend.friend}`})}><BookOpen size={15}/>关系记忆</button></>}
          <span className="model-label">{status?.receiver.state==='connected'?'QQ 已连接':'QQ 未连接'}</span></div></header>
      {error && <div role="alert" className="qq-error error-notice">{error}<button className="text-button" aria-label="关闭提示" onClick={()=>setError('')}><X size={15}/></button></div>}
      {notice && <p className="qq-caption" role="status">{notice}</p>}
      {replyNotice && <div role="alert" className="qq-error error-notice"><span>{replyNotice}</span>
        <button className="text-button" onClick={()=>navigation.navigate('settings')}>模型设置</button></div>}
      {friend ? <>
        <div className="friend-controls">
          <div><strong>{stateLabel(friend.state)}</strong>
            <p>{friend.chat_type===2?`被 @、叫到 ${characterName} 或引用她的消息时接话；各群独立运行。`:<>好友明确提到 {characterName}{friend.auto_reply_idle ? ' 或用户两分钟没回复好友消息' : ''}时会自动接话</>}</p>
            {!!friend.blocked && <p>{friend.note}</p>}
            <label className="qq-idle-option"><button type="button" role="switch" aria-checked={!!(friend.chat_type===2?friend.natural_chat:friend.auto_reply_idle)}
              aria-label={friend.chat_type===2?'自然参与群聊':'用户两分钟没回复自动接话'} className="qq-idle-switch" disabled={changing}
              onClick={()=>void toggleIdle(!(friend.chat_type===2?friend.natural_chat:friend.auto_reply_idle))}><span/></button>{friend.chat_type===2?'自然参与群聊（允许保持安静）':'用户两分钟没回复自动接话'}</label>
            {friend.state === 'waiting' && friend.due_at && <small>约 {Math.max(0, Math.ceil(friend.due_at - Date.now()/1000))} 秒后接话</small>}
          </div>
          <div className="friend-actions">
            {friend.blocked ? <button className="send-button" disabled={changing || status?.receiver.state !== 'connected'}
              onClick={() => void toggle(true)}>已核对，恢复接话</button>
              : <button className="send-button" disabled={changing || (!friend.enabled && status?.receiver.state !== 'connected')}
                onClick={() => void toggle(!friend.enabled)}>{friend.enabled ? '关闭接话' : '开启接话'}</button>}
            {!!friend.blocked && <button className="new-chat" disabled={changing} onClick={() => void toggle(false)}>关闭接话</button>}
          </div>
        </div>
        <section className="conversation" aria-label="QQ 聊天记录">
          {!detail ? <EmptyState loading title="正在读取聊天记录…"/>
            : <div className="message-list">
              {!detail.messages.length && <p className="no-history">这里会保存连接后的新消息。</p>}
              {detail.messages.map(message => <article key={message.id} id={'message-'+message.id}
                className={`message ${message.author.kind === 'miko' ? 'assistant' : 'user'} ${navigation.search.get('message')===message.id?'message-highlight':''}`}>
                <div className="message-avatar">{message.author.kind === 'miko' ? 'm' : message.author.name.slice(0, 1)}</div>
                <div className="message-content"><div className="message-name">{message.author.name}
                  <time>{timeLabel(message.created_at)}</time></div>
                  {message.segments.filter(s => s.type === 'reply').map((s, i) => <blockquote key={i} className="qq-quote">
                    {detail.messages.find(m => m.message_id === s.message_id)?.text ?? message.quoted_message?.text ?? '引用消息'}
                  </blockquote>)}
                  <div className="message-text">{message.recalled ? '这条消息已撤回' : message.text}</div>
                  {!message.recalled && detail.reactions?.filter(r=>r.message_id===message.message_id).map(r=><span className="qq-reaction" key={r.reaction_id} title={`${characterName} 对这条消息的表情回应`}>{r.name} · {characterName}</span>)}
                  {!message.recalled && message.segments.filter(s => s.sticker_id).map((s, i) => <img key={i} className="chat-sticker"
                    src={`/api/qq/stickers/${friend.account}/${s.sticker_id}/image`} alt={message.text} />)}
                  {!message.recalled && message.segments.filter(s=>s.type==='image').map((s,i)=>!s.sticker_id && <div className="qq-message-image" key={i}>
                    <img loading="lazy" src={`/api/qq/friends/${friend.account}/${friend.friend}/images/${message.message_id}/${i}`}
                      alt="聊天图片" onError={event=>{event.currentTarget.hidden=true;}}/>
                    <button className="text-button" onClick={()=>setCollecting({account:friend.account,
                      imageUrl:`/api/qq/friends/${friend.account}/${friend.friend}/images/${message.message_id}/${i}`,
                      collectUrl:`/api/qq/friends/${friend.account}/${friend.friend}/images/${message.message_id}/${i}/collect`})}><ImagePlus size={13}/>存为表情</button>
                  </div>)}
                  {message.author.kind==='miko' && message.status==='completed' && <button className="text-button message-review-link"
                    onClick={()=>navigation.navigate('review',{day:new Date(message.created_at).toLocaleDateString('sv-SE'),peer:`qq:${friend.account}:${friend.friend}`,reply:message.id})}>
                    <NotebookPen size={13}/>批注</button>}
                </div>
              </article>)}
              {!!detail.runs.length && <details className="reply-runs"><summary>接话记录 · {detail.runs.length}</summary>
                {detail.runs.map(run => <div key={run.id} className="reply-run"><small>{timeLabel(run.created_at*1000)} · {states[run.state] ?? run.state}</small>
                  {run.bubbles.length ? run.bubbles.map(b => <p key={b.position}>{b.text}
                    <small className="bubble-state">{states[b.state] ?? b.state}{b.receipt_id !== null ? ` · ${b.receipt_id}` : ''}</small>
                    {b.error && <span className="message-note">{b.error}</span>}</p>) : run.text && <p>{run.text}</p>}
                  {run.error && <p className="message-note">{run.error}</p>}</div>)}
              </details>}
              <StickerLibrary key={friend.account} account={friend.account} refreshKey={stickerVersion}/>
              {friend.chat_type===2 && <GroupLanguage key={`${friend.account}:${friend.friend}`} account={friend.account} friend={friend.friend}/>}
              {friend.chat_type!==2 && <FriendRelationships key={`${friend.account}:${friend.friend}`} friend={friend} onDirty={setRelationshipDirty}/>}
              <div ref={bottom} />
            </div>}
        </section>
        <p className="qq-caption">{friend.chat_type===2?'只记录已开启群聊的新消息。你在本群发言会中断本群正在生成的回复。':'你发言后，该会话重新由你主导。'}</p>
      </> : <section className="conversation"><EmptyState title={groupMode?'选择或添加一个群聊':'选择一位好友'}/></section>}
      <FriendManager open={manager} kind={groupMode?'groups':'friends'} onClose={()=>setManager(false)} onAdded={message=>setNotice(message)}/>
      {guard.dialog}
      {collecting && <StickerImport {...collecting} onClose={()=>setCollecting(null)} onSaved={()=>{setStickerVersion(value=>value+1);setNotice('已添加到本地表情库');}}/>}
    </main>
  </AppShell>;
}
