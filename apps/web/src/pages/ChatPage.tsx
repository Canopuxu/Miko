import { useEffect, useRef, useState } from 'react';

type Message = { id: string; role: 'user' | 'assistant'; text: string; state?: string };
type Status = { chat: { configured: boolean; model: string | null }; dsh: { ready: boolean } };
type Event = { type: string; text?: string; status?: string; error?: string;
  turn_id?: string; conversation_id?: string };

async function responseError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`;
}

export function ChatPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const conversation = useRef<string | null>(null);
  const turn = useRef<string | null>(null);
  const cancelRequested = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error('连接失败');
        const value: Status = await response.json();
        if (alive) { setStatus(value); setConnected(value.dsh.ready); }
      } catch { if (alive) setConnected(false); }
    }
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { alive = false; window.clearInterval(timer); controller.current?.abort(); };
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages]);

  async function cancelTurn() {
    cancelRequested.current = true;
    setStopping(true);
    if (!turn.current) return;
    try {
      const response = await fetch(`/api/chat/${turn.current}/cancel`, { method: 'POST' });
      if (!response.ok) throw new Error(await responseError(response));
    } catch (cause) { setError(String(cause)); setStopping(false); }
  }

  async function send() {
    const text = draft.trim();
    if (!text || busy || !connected || !status?.chat.configured) return;
    const assistantId = crypto.randomUUID();
    setMessages(current => [...current,
      { id: crypto.randomUUID(), role: 'user', text },
      { id: assistantId, role: 'assistant', text: '', state: 'running' },
    ]);
    setDraft(''); setError(''); setBusy(true); setStopping(false);
    turn.current = null; cancelRequested.current = false;
    controller.current = new AbortController();
    let finished = false;

    async function consume(event: Event) {
      if (event.type === 'started') {
        turn.current = event.turn_id!;
        conversation.current = event.conversation_id!;
        if (cancelRequested.current) await cancelTurn();
      } else if (event.type === 'delta') {
        setMessages(current => current.map(message => message.id === assistantId
          ? { ...message, text: message.text + (event.text ?? '') } : message));
      } else if (event.type === 'done') {
        finished = true;
        setMessages(current => current.map(message => message.id === assistantId
          ? { ...message, text: event.text ?? message.text, state: event.status } : message));
        if (event.status === 'error') setError(event.error ?? '生成失败，请重试');
      }
    }

    try {
      const response = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversation.current }),
        signal: controller.current.signal,
      });
      if (!response.ok) {
        if (response.status === 404) conversation.current = null;
        throw new Error(await responseError(response));
      }
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let pending = '';
      while (true) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value, { stream: !done });
        let boundary: number;
        while ((boundary = pending.indexOf('\n\n')) !== -1) {
          const block = pending.slice(0, boundary);
          pending = pending.slice(boundary + 2);
          const line = block.split('\n').find(item => item.startsWith('data:'));
          if (line) await consume(JSON.parse(line.slice(5)) as Event);
        }
        if (done) break;
      }
      if (!finished) throw new Error('连接中断，本轮未完成，请重试');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setMessages(current => current.map(message => message.id === assistantId
        ? { ...message, state: 'error' } : message));
    } finally {
      setBusy(false); setStopping(false); turn.current = null; controller.current = null;
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }

  function newConversation() {
    if (busy) return;
    conversation.current = null; setMessages([]); setError(''); input.current?.focus();
  }

  const enabled = connected && status?.chat.configured;
  const today = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());

  return <div className="app-frame">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Miko 首页"><span className="brand-mark">m</span><span>Miko</span></a>
      <div className="sidebar-label">我们的空间</div>
      <button className="nav-item selected" onClick={() => input.current?.focus()}><span>◌</span> 聊天</button>
      <button className="new-chat" onClick={newConversation} disabled={busy} aria-label="开启新对话" title="开启新对话"><span>＋</span><span className="new-chat-label"> 开启新对话</span></button>
      <div className="sidebar-footer"><span className={`status-dot ${connected ? 'online' : ''}`} />
        {connected ? '已连接' : '连接中'}<small>私人的一隅</small></div>
    </aside>
    <main className="main-panel">
      <header className="page-header"><div><h1>和 Miko 聊聊</h1><p>{today}</p></div>
        <span className="model-label">{status?.chat.model ?? '等待模型配置'}</span></header>
      <section className="conversation" aria-label="聊天记录" aria-live="polite">
        {messages.length === 0 && <div className="welcome"><div className="welcome-orbit"><span>m</span></div>
          <p className="eyebrow">HELLO, YOU</p><h2>从一句话开始。</h2><p>想说什么，就在这里告诉我。</p></div>}
        <div className="message-list">{messages.map(message => <article key={message.id} className={`message ${message.role}`}>
          <div className="message-avatar">{message.role === 'assistant' ? 'm' : '你'}</div>
          <div className="message-content"><div className="message-name">{message.role === 'assistant' ? 'Miko' : '你'}</div>
            <div className="message-text">{message.text || (message.state === 'running' ? <span className="thinking">正在想…</span> : '')}</div>
            {message.state === 'cancelled' && <span className="message-note">已停止</span>}
            {message.state === 'error' && <span className="message-note">本轮未完成</span>}
          </div></article>)}<div ref={bottom} /></div>
      </section>
      <footer className="composer-area">
        {error && <div role="alert" className="error-notice">{error}<button onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
        {status && !status.chat.configured && <div className="error-notice">模型尚未配置，配置完成后就可以聊天。</div>}
        <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <textarea ref={input} value={draft} onChange={event => setDraft(event.target.value)}
            placeholder="给 Miko 留一句话…" aria-label="消息" rows={3}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); if (!busy) void send();
            } }} />
          <div className="composer-bottom"><span>Enter 发送 <i>·</i> Shift + Enter 换行</span>
            {busy ? <button type="button" className="send-button stop-button" disabled={stopping} onClick={() => void cancelTurn()}>
              <span>■</span> {stopping ? '正在停止' : '停止生成'}</button>
              : <button type="submit" className="send-button" disabled={!enabled || !draft.trim()}>发送 <span>↑</span></button>}
          </div>
        </form>
        <p className="composer-caption">{busy ? 'Miko 正在回复你' : '一段对话，一点新的日常。'}</p>
      </footer>
    </main>
  </div>;
}
