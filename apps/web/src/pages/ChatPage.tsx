import { useEffect, useRef, useState } from 'react';
import { chatEvents, getJson, responseError, type Conversation, type ConversationDetail, type Message } from '../api/chat';

type Status = { chat: { configured: boolean; model: string | null }; dsh: { ready: boolean } };
const note = { cancelled: '已停止', error: '生成失败', interrupted: '回复中断' };
const dateTime = (stamp: number) => new Date(stamp).toLocaleString('zh-CN');
const shortTime = (stamp: number) => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(stamp);

function remember(id: string | null) {
  const url = new URL(window.location.href);
  url.searchParams.delete('conversation'); url.searchParams.delete('new');
  url.searchParams.set(id ? 'conversation' : 'new', id ?? '1');
  window.history.replaceState(null, '', url);
}

export function ChatPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [observedTurn, setObservedTurn] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const conversation = useRef<string | null>(null);
  const turn = useRef<string | null>(null);
  const cancelRequested = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const generating = busy || observedTurn !== null;

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
    async function refreshStatus() {
      try {
        const value = await getJson<Status>('/api/status');
        if (alive) { setStatus(value); setConnected(value.dsh.ready); }
      } catch { if (alive) setConnected(false); }
    }
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
    void refreshStatus(); void restore();
    const timer = window.setInterval(refreshStatus, 5000);
    return () => { alive = false; window.clearInterval(timer); controller.current?.abort(); };
  }, []);

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

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages]);

  async function openConversation(id: string) {
    if (generating || loading) return;
    setLoading(true); setError('');
    try { applyDetail(await getJson<ConversationDetail>(`/api/conversations/${id}`)); setDraft(''); }
    catch (cause) { setError(String(cause)); }
    finally { setLoading(false); }
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
  const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  const title = conversations.find(item => item.id === selected)?.title;

  return <div className="app-frame">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Miko 首页"><span className="brand-mark">m</span><span>Miko</span></a>
      <div className="sidebar-label">我们的空间</div>
      <a className="section-link" href="/?view=qq">QQ 好友 →</a>
      <a className="section-link" href="/?view=profiles">设定与资料 →</a>
      <button className="nav-item selected" onClick={() => input.current?.focus()}><span>◌</span> 聊天</button>
      <button className="new-chat" onClick={newConversation} disabled={generating || loading} aria-label="开启新对话" title="开启新对话"><span>＋</span><span className="new-chat-label"> 开启新对话</span></button>
      <div className="history-label">最近对话</div>
      <nav className="conversation-list" aria-label="历史对话">
        {conversations.map(item => <button key={item.id} className={`conversation-item ${selected === item.id ? 'active' : ''}`}
          aria-current={selected === item.id ? 'page' : undefined} onClick={() => void openConversation(item.id)}
          disabled={generating || loading} title={item.title}>
          <span className="conversation-title">{item.title}</span>
          <span className="conversation-preview">{item.preview || '已保存的对话'}</span>
        </button>)}
        {!conversations.length && <p className="no-history">还没有历史对话</p>}
      </nav>
      <div className="sidebar-footer"><span className={`status-dot ${connected ? 'online' : ''}`} />
        {connected ? '已连接' : '连接中'}<small>私人的一隅</small></div>
    </aside>
    <main className="main-panel">
      <header className="page-header"><div><h1>{title ?? '和 Miko 聊聊'}</h1><p>{today}</p></div>
        <span className="model-label">{status?.chat.model ?? '等待模型配置'}</span></header>
      <section className="conversation" aria-label="聊天记录" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="welcome"><p>正在读取对话…</p></div>
          : messages.length === 0 && <div className="welcome"><div className="welcome-orbit"><span>m</span></div>
            <p className="eyebrow">HELLO, YOU</p><h2>从一句话开始。</h2><p>想说什么，就在这里告诉我。</p></div>}
        {!loading && <div className="message-list">{messages.map(message => <article key={message.id}
          className={`message ${message.author.kind === 'miko' ? 'assistant' : 'user'}`}>
          <div className="message-avatar">{message.author.kind === 'miko' ? 'm' : message.author.name.slice(0, 1)}</div>
          <div className="message-content"><div className="message-name">{message.author.name}
            <time dateTime={new Date(message.created_at).toISOString()} title={dateTime(message.created_at)}>{shortTime(message.created_at)}</time></div>
            <div className="message-text">{message.text || (message.status === 'running' ? <span className="thinking">正在想…</span> : '')}</div>
            {(message.status === 'cancelled' || message.status === 'error' || message.status === 'interrupted') &&
              <span className="message-note">{note[message.status]}{message.error && ` · ${message.error}`}</span>}
          </div></article>)}<div ref={bottom} /></div>}
      </section>
      <footer className="composer-area">
        {error && <div role="alert" className="error-notice">{error}<button onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
        {status && !status.chat.configured && <div className="error-notice">模型尚未配置，配置完成后就可以聊天。</div>}
        <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <textarea ref={input} value={draft} onChange={event => setDraft(event.target.value)}
            placeholder="给 Miko 留一句话…" aria-label="消息" rows={3}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); void send();
            } }} />
          <div className="composer-bottom"><span>Enter 发送 <i>·</i> Shift + Enter 换行</span>
            {generating ? <button type="button" className="send-button stop-button" disabled={stopping} onClick={() => void cancelTurn()}>
              <span>■</span> {stopping ? '正在停止' : '停止生成'}</button>
              : <button type="submit" className="send-button" disabled={!enabled || !draft.trim()}>发送 <span>↑</span></button>}
          </div>
        </form>
        <p className="composer-caption">{generating ? 'Miko 正在回复你' : selected ? '这段对话已保存在这里。' : '一段对话，一点新的日常。'}</p>
      </footer>
    </main>
  </div>;
}
