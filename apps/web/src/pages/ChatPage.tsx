import * as AlertDialog from '@radix-ui/react-alert-dialog';
import { AppShell, NavigationContext } from '../components/AppShell';
import { useContext, useEffect, useRef, useState } from 'react';
import { ArrowUp, NotebookPen, Plus, Square, Trash2, X } from 'lucide-react';
import { EmptyState, useDiscardGuard } from '../components/ui';
import { useRuntime } from '../components/RuntimeStatus';
import { chatEvents, getJson, responseError, type Conversation, type ConversationDetail, type Message } from '../api/chat';

const note = { cancelled: '已停止', error: '生成失败', interrupted: '回复中断' };
const dateTime = (stamp: number) => new Date(stamp).toLocaleString('zh-CN');
const shortTime = (stamp: number) => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(stamp);

export function ChatPage() {
  const navigation = useContext(NavigationContext)!;
  const {status, error:runtimeError} = useRuntime();
  const connected = !!status?.dsh.ready && !runtimeError;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [observedTurn, setObservedTurn] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [removing, setRemoving] = useState(false);
  const conversation = useRef<string | null>(null);
  const turn = useRef<string | null>(null);
  const cancelRequested = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const generating = busy || observedTurn !== null;
  const guard = useDiscardGuard(!!draft.trim());
  function remember(id: string | null) {navigation.replace({conversation:id ?? undefined,new:id?undefined:'1'});}

  function applyDetail(detail: ConversationDetail) {
    conversation.current = detail.id; setSelected(detail.id); remember(detail.id);
    setMessages(detail.messages.map(message => !detail.active_turn_id && message.status === 'running'
      ? { ...message, status: 'interrupted', error: message.error ?? '这轮回复未正常完成' } : message));
    setObservedTurn(detail.active_turn_id);
    turn.current = detail.active_turn_id;
    if (!detail.active_turn_id) setStopping(false);
  }

  async function refreshList() {
    const result = await getJson<{ conversations: Conversation[] }>('/api/conversations');
    setConversations(result.conversations);
    return result.conversations;
  }

  useEffect(() => {
    let alive = true;
    async function restore() {
      try {
        const result = await getJson<{ conversations: Conversation[] }>('/api/conversations');
        if (!alive) return;
        setConversations(result.conversations);
        const query = new URLSearchParams(window.location.search);
        const id = query.get('conversation') ?? (query.has('new') ? null : result.conversations[0]?.id);
        if (id) {
          const detail = await getJson<ConversationDetail>(`/api/conversations/${id}`);
          if (alive) applyDetail(detail);
        }
      } catch (cause) { if (alive) setError(String(cause)); }
      finally { if (alive) setLoading(false); }
    }
    void restore();
    return () => { alive = false; controller.current?.abort(); };
  }, []);

  useEffect(() => {
    const id = navigation.search.get('conversation');
    if(navigation.active==='chat' && id && id!==conversation.current && !loading && !generating)
      guard.proceed(()=>void openConversation(id));
  },[navigation.active,navigation.search,loading]);

  // A reload closes the old stream. Poll its saved state until cancellation finishes.
  useEffect(() => {
    if (!observedTurn || busy) return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const detail = await getJson<ConversationDetail>(`/api/conversations/${conversation.current}`);
        if (alive) applyDetail(detail);
      } catch (cause) { if (alive) setError(String(cause)); }
    }, 1000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [observedTurn, busy]);

  useEffect(() => {
    if(navigation.active!=='chat')return;
    const focus=navigation.search.get('message');
    const target=focus?document.getElementById('message-'+focus):null;
    if(target) target.scrollIntoView({block:'center'});else bottom.current?.scrollIntoView({block:'end'});
  }, [messages,navigation.active,navigation.search]);

  async function openConversation(id: string) {
    if (generating || loading) return;
    setLoading(true); setError('');
    try { applyDetail(await getJson<ConversationDetail>(`/api/conversations/${id}`)); setDraft(''); }
    catch (cause) { setError(String(cause)); }
    finally { setLoading(false); }
  }

  async function deleteConversation() {
    if (!deleting) return;
    setRemoving(true); setError('');
    try {
      const response = await fetch(`/api/conversations/${deleting.id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseError(response));
      if (conversation.current === deleting.id) {
        conversation.current = null; turn.current = null; setSelected(null); remember(null);
        setMessages([]); setDraft(''); setObservedTurn(null);
      }
      setConversations(rows => rows.filter(row => row.id !== deleting.id));
      setDeleting(null);
    } catch (cause) { setError(String(cause)); }
    finally { setRemoving(false); }
  }

  async function cancelTurn() {
    cancelRequested.current = true; setStopping(true);
    if (!turn.current) return;
    try {
      const response = await fetch(`/api/chat/${turn.current}/cancel`, { method: 'POST' });
      if (!response.ok) throw new Error(await responseError(response));
    } catch (cause) { setError(String(cause)); setStopping(false); }
  }

  async function send() {
    const text = draft.trim();
    if (!text || generating || loading || !connected || !status?.chat.configured) return;
    const assistantId = crypto.randomUUID();
    const userId = crypto.randomUUID();
    const stamp = Date.now();
    setMessages(current => [...current,
      { id: userId, author: { id: 'owner', kind: 'owner', name: '你' }, source: 'web', text,
        status: 'completed', error: null, created_at: stamp },
      { id: assistantId, author: { id: 'miko', kind: 'miko', name: 'Miko' }, source: 'web', text: '',
        status: 'running', error: null, created_at: stamp },
    ]);
    setDraft(''); setError(''); setBusy(true); setStopping(false);
    turn.current = null; cancelRequested.current = false;
    controller.current = new AbortController();
    let finished = false;
    let accepted = false;
    try {
      for await (const event of chatEvents(text, conversation.current, controller.current.signal)) {
        if (event.type === 'started') {
          accepted = true; turn.current = event.turn_id!;
          conversation.current = event.conversation_id!; setSelected(event.conversation_id!); remember(event.conversation_id!);
          void refreshList().catch(cause => setError(String(cause)));
          if (cancelRequested.current) await cancelTurn();
        } else if (event.type === 'delta') {
          setMessages(current => current.map(message => message.id === assistantId
            ? { ...message, text: message.text + (event.text ?? '') } : message));
        } else if (event.type === 'done') {
          finished = true;
          setMessages(current => current.map(message => message.id === assistantId
            ? { ...message, text: event.text ?? message.text, status: event.status!, error: event.error ?? null } : message));
          if (event.status === 'error') setError(event.error ?? '生成失败，请重试');
        }
      }
      if (!finished) throw new Error('连接中断，本轮未完成，请重试');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      if (!accepted) {
        setDraft(text);
        setMessages(current => current.filter(message => message.id !== assistantId && message.id !== userId));
      }
    } finally {
      if (conversation.current) {
        try { applyDetail(await getJson<ConversationDetail>(`/api/conversations/${conversation.current}`)); }
        catch { /* Keep the visible stream until the backend reconnects or the page reloads. */ }
      }
      await refreshList().catch(() => {});
      setBusy(false); setStopping(false); controller.current = null;
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }

  function newConversation() {
    if (generating || loading) return;
    conversation.current = null; turn.current = null; setSelected(null); remember(null);
    setMessages([]); setDraft(''); setError(''); input.current?.focus();
  }

  const enabled = connected && status?.chat.configured && !loading;
  const title = conversations.find(item => item.id === selected)?.title;

  return <AppShell view="chat" sidebar={<>
      <button className="new-chat" onClick={()=>guard.proceed(newConversation)} disabled={generating || loading} aria-label="开启新对话" title="开启新对话"><Plus size={17}/><span className="new-chat-label"> 开启新对话</span></button>
      <nav className="conversation-list" aria-label="历史对话">
        {conversations.map(item => <div key={item.id} className="conversation-row"><button className={`conversation-item ${selected === item.id ? 'active' : ''}`}
          aria-current={selected === item.id ? 'page' : undefined} onClick={() => {if(item.id!==selected)guard.proceed(()=>void openConversation(item.id));}}
          disabled={generating || loading} title={item.title}>
          <span className="conversation-title">{item.title}</span>
          <span className="conversation-preview">{item.preview || '已保存的对话'}</span>
        </button><button className="conversation-delete" disabled={generating || loading || removing}
          aria-label={`删除对话：${item.title}`} title="删除对话" onClick={() => setDeleting(item)}><Trash2 size={16}/></button></div>)}
        {!conversations.length && <p className="no-history">还没有历史对话</p>}
      </nav>
      <div className="sidebar-footer"><span className={`status-dot ${connected ? 'online' : ''}`} />
        {connected ? '已连接' : '连接中'}</div>
    </>}>
    <main className="main-panel">
      <header className="page-header"><h1>{title ?? '和 Miko 聊聊'}</h1>
        <div className="header-actions"><button className="ui-button" onClick={()=>navigation.navigate('review',
          {day:new Date().toLocaleDateString('sv-SE'),peer:selected ?? '',reply:undefined})}><NotebookPen size={16}/>回顾与批注</button>
          <span className="model-label">{status?.chat.model ?? '等待模型配置'}</span></div></header>
      <section className="conversation" aria-label="聊天记录" aria-live="polite" aria-busy={loading}>
        {loading ? <EmptyState loading title="正在读取对话…"/>
          : messages.length === 0 && <EmptyState title="新对话"/>}
        {!loading && <div className="message-list">{messages.map(message => <article key={message.id}
          id={'message-'+message.id} className={`message ${message.author.kind === 'miko' ? 'assistant' : 'user'} ${navigation.search.get('message')===message.id?'message-highlight':''}`}>
          <div className="message-avatar">{message.author.kind === 'miko' ? 'm' : message.author.name.slice(0, 1)}</div>
          <div className="message-content"><div className="message-name">{message.author.name}
            <time dateTime={new Date(message.created_at).toISOString()} title={dateTime(message.created_at)}>{shortTime(message.created_at)}</time></div>
            <div className="message-text">{message.text || (message.status === 'running' ? <span className="thinking">正在想…</span> : '')}</div>
            {(message.status === 'cancelled' || message.status === 'error' || message.status === 'interrupted') &&
              <span className="message-note">{note[message.status]}{message.error && ` · ${message.error}`}</span>}
            {message.author.kind==='miko' && message.status==='completed' && <button className="message-review-link text-button"
              onClick={()=>navigation.navigate('review',{day:new Date(message.created_at).toLocaleDateString('sv-SE'),peer:selected ?? '',reply:message.id})}>
              <NotebookPen size={13}/>批注</button>}
          </div></article>)}<div ref={bottom} /></div>}
      </section>
      <footer className="composer-area">
        {error && <div role="alert" className="error-notice">{error}<button onClick={() => setError('')} aria-label="关闭提示"><X size={15}/></button></div>}
        {status && !status.chat.configured && <div className="error-notice">模型尚未配置，配置完成后就可以聊天。</div>}
        <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <textarea ref={input} value={draft} onChange={event => setDraft(event.target.value)}
            placeholder="给 Miko 留一句话…" aria-label="消息" rows={3}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); void send();
            } }} />
          <div className="composer-bottom"><span>Enter 发送 <i>·</i> Shift + Enter 换行</span>
            {generating ? <button type="button" className="send-button stop-button" disabled={stopping} onClick={() => void cancelTurn()}>
              <Square size={13}/> {stopping ? '正在停止' : '停止生成'}</button>
              : <button type="submit" className="send-button" disabled={!enabled || !draft.trim()}>发送 <ArrowUp size={16}/></button>}
          </div>
        </form>
        {generating && <p className="composer-caption" role="status">Miko 正在回复你</p>}
      </footer>
    {guard.dialog}
    <AlertDialog.Root open={!!deleting} onOpenChange={open => { if (!open && !removing) setDeleting(null); }}>
      <AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay"/>
        <AlertDialog.Content className="confirm-dialog">
          <AlertDialog.Title>删除这段对话？</AlertDialog.Title>
          <AlertDialog.Description>“{deleting?.title}”及其聊天记录将被删除，无法撤销。已整理的长期记忆可在记忆页单独管理。</AlertDialog.Description>
          <div className="dialog-actions"><AlertDialog.Cancel className="ui-button" disabled={removing}>取消</AlertDialog.Cancel>
            <button className="ui-button warning" disabled={removing} onClick={() => void deleteConversation()}>{removing ? '正在删除…' : '确认删除'}</button></div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root></main>
  </AppShell>;
}
