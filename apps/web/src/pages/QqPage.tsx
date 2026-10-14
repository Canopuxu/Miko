import { AppShell, NavigationContext } from '../components/AppShell';
import { useContext, useEffect, useRef, useState } from 'react';
import { BookOpen, NotebookPen, X } from 'lucide-react';
import { EmptyState, useDiscardGuard } from '../components/ui';
import { getJson, responseError, type Message } from '../api/chat';
import { StickerLibrary } from './StickerLibrary';
import { FriendRelationships } from './FriendRelationships';

type Friend = { account: string; friend: string; name: string; enabled: number; blocked: number;
  state: string; mode: string; note: string; due_at: number | null;
  owner_relationship: string; miko_relationship: string; miko_introduced: number; intro_receipt_id: number | null };
type Run = { id: string; state: string; text: string; error: string | null; created_at: number;
  bubbles: { position: number; text: string; state: string; receipt_id: number | null; error: string | null }[] };
type QqMessage = Message & { message_id: number; recalled: boolean;
  segments: { type: string; message_id?: number; sticker_id?: string }[];
  quoted_message?: { text?: string; message_id: number } };
type QqStatus = { receiver: { state: string; account: string; note: string }; friends: Friend[] };
type Detail = { messages: QqMessage[]; runs: Run[] };
const states: Record<string, string> = { disabled: '已关闭', blocked: '等待核对', waiting: '等待接话',
  generating: '正在生成', sending: '正在发送', chatting: 'Miko 接话中', listening: '等待新消息',
  sent: '已发送', skipped: '无需回复', stale: '旧回复已作废', error: '失败', unknown: '发送结果待核对', interrupted: '后台中断',
  pending: '待发送', preparing: '准备发送', cancelled: '已取消', between_bubbles: '接话中' };
const timeLabel = (stamp: number) => new Date(stamp).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export function QqPage() {
  const navigation = useContext(NavigationContext)!;
  const [status, setStatus] = useState<QqStatus | null>(null);
  const [selected, setSelected] = useState(new URLSearchParams(location.search).get('friend'));
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [changing, setChanging] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const friend = status?.friends.find(item => item.friend === selected);
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
    setSelected(id); setDetail(null); setError('');
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

  return <AppShell view="qq" sidebar={<>
      <nav className="conversation-list" aria-label="QQ 好友">
        {status?.friends.map(item => <button key={`${item.account}:${item.friend}`}
          className={`conversation-item ${item.friend === selected ? 'active' : ''}`} onClick={() => {if(item.friend!==selected)guard.proceed(()=>select(item.friend));}}>
          <span className="conversation-title">{item.name}</span>
          <span className="conversation-preview">{states[item.state] ?? item.state}</span>
        </button>)}
      </nav>
    </>}>
    <main className="main-panel">
      <header className="page-header"><div><h1>{friend?.name ?? 'QQ 好友'}</h1>
        <p>{friend?.friend ?? '选择一位好友查看接话状态'}</p></div>
        <div className="header-actions">{friend && <><button className="ui-button" onClick={()=>navigation.navigate('review',
          {day:new Date().toLocaleDateString('sv-SE'),peer:`qq:${friend.account}:${friend.friend}`,reply:undefined})}><NotebookPen size={15}/>回顾</button>
          <button className="ui-button" onClick={()=>navigation.navigate('memory',
            {person:'miko',relation:`qq:${friend.account}:${friend.friend}`})}><BookOpen size={15}/>关系记忆</button></>}
          <span className="model-label">{status?.receiver.state==='connected'?'QQ 已连接':'QQ 未连接'}</span></div></header>
      {error && <div role="alert" className="qq-error error-notice">{error}<button className="text-button" aria-label="关闭提示" onClick={()=>setError('')}><X size={15}/></button></div>}
      {friend ? <>
        <div className="friend-controls">
          <div><strong>{states[friend.state] ?? friend.state}</strong>
            {friend.note !== (states[friend.state] ?? friend.state) && <p>{friend.note}</p>}
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
                  {!message.recalled && message.segments.filter(s => s.sticker_id).map((s, i) => <img key={i} className="chat-sticker"
                    src={`/api/qq/stickers/${friend.account}/${s.sticker_id}/image`} alt={message.text} />)}
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
              <StickerLibrary key={friend.account} account={friend.account} />
              <FriendRelationships key={`${friend.account}:${friend.friend}`} friend={friend} onDirty={setRelationshipDirty}/>
              <div ref={bottom} />
            </div>}
        </section>
        <p className="qq-caption">你两分钟没有回复时，Miko 接话；你发言后重新由你主导。</p>
      </> : <section className="conversation"><EmptyState title="选择一位好友"/></section>}
      {guard.dialog}
    </main>
  </AppShell>;
}
