// Tulpa's contact memory panel adapted to Miko's React root: separate automatic
// and manual memories, batch progress, source records, revision and history.
import { useEffect, useState } from 'react';
import { getJson, responseError } from '../api/chat';

type Scope = { id: number; name: string; conversation_id: string; progress: number; pending_batches: number; status: string; has_memory: boolean };
type Source = { id: number; author: string; content: string; timestamp: number; original_id: string };
type Detail = { id: number; automatic: string; manual: string; revision: number; valid: number;
  source_messages: Source[]; history: { id: number; kind: string; body: string; created_at: number; details: string }[] };
type Listing = { control: { enabled: number }; scopes: Scope[]; index: { configured: boolean; embedding_model: string | null; error: string | null } };
const authors: Record<string, string> = { miko: 'Miko', owner: '你', contact: '好友' };

export function MemoryPage() {
  const [listing, setListing] = useState<Listing | null>(null);
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [manual, setManual] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    void getJson<Listing>(`/api/memory?q=${encodeURIComponent(q)}`).then(value => { if (alive) setListing(value); })
      .catch(cause => { if (alive) setNotice(String(cause)); });
    return () => { alive = false; };
  }, [q, refresh]);
  useEffect(() => {
    let alive = true; setDetail(null);
    if (selected !== null) void getJson<Detail>(`/api/memory/scopes/${selected}`).then(value => {
      if (alive) { setDetail(value); setManual(value.manual); }
    }).catch(cause => { if (alive) setNotice(String(cause)); });
    return () => { alive = false; };
  }, [selected, refresh]);
  async function mutate(path: string, method: string, body?: object) {
    setBusy(true); setNotice('');
    try {
      const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      if (!response.ok) throw new Error(await responseError(response));
      setRefresh(v => v + 1); setNotice(method === 'PATCH' ? '修正已保存，历史记录保留' : '已更新');
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  return <div className="app-frame profiles-frame">
    <aside className="sidebar"><a className="brand" href="/"><span className="brand-mark">m</span><span>Miko</span></a>
      <a className="section-link" href="/">和 Miko 聊天</a><a className="section-link" href="/?view=qq">QQ 好友</a>
      <a className="section-link" href="/?view=profiles">设定与资料</a><div className="history-label">关系与经历记忆</div>
      <p className="profile-help">你与好友、Miko 与好友分别整理。既有聊天可作为原文查看；新对话每积累 100 条有效消息自动整理一次。</p></aside>
    <main className="main-panel"><header className="page-header"><div><h1>关系与经历记忆</h1>
      <p>{listing?.index.configured ? `语义检索 · ${listing.index.embedding_model}` : '尚未配置语义检索'}</p></div>
      <button disabled={busy || !listing} className="send-button" onClick={() => void mutate('/api/memory/control', 'POST', { enabled: !listing?.control.enabled })}>
        {listing?.control.enabled ? '暂停自动记忆' : '开启自动记忆'}</button></header>
      {(notice || listing?.index.error) && <p role="status" className="profile-notice">{notice || listing?.index.error}</p>}
      <div className="profile-workspace"><section className="profile-browser" aria-label="记忆对象">
        <div className="profile-filters"><input aria-label="搜索记忆对象" placeholder="搜索人物" value={q} onChange={e => setQ(e.target.value)} /></div>
        {listing?.scopes.map(scope => <button disabled={busy} className={`profile-card ${selected === scope.id ? 'active' : ''}`} key={scope.id} onClick={() => setSelected(scope.id)}>
          <strong>{scope.name}</strong><small>{scope.has_memory ? '已有自动记忆' : '正在积累'} · {scope.progress}/100 · 待整理 {scope.pending_batches} 批</small>
          <small>{scope.status === 'update_failed' ? '整理失败，可重试' : scope.status === 'updating' ? '正在整理' : ''}</small></button>)}
        {!listing?.scopes.length && <p>有聊天记录后，这里会出现对应的记忆对象。</p>}
      </section><section className="profile-editor" aria-label="记忆详情">
        {!detail ? <p>选择一位人物，查看记忆、原文和历史。</p> : <>
          <h2>自动记忆</h2><p className="memory-body">{detail.automatic || '尚未积累到一批有效消息。'}</p>
          {!detail.valid && <p>原文已变化，这份自动记忆暂不用于聊天。</p>}
          <h2>我的补充与修正</h2><div className="profile-fields"><textarea aria-label="人工记忆" rows={6} maxLength={6000} value={manual} onChange={e => setManual(e.target.value)} /></div>
          <div className="profile-actions"><button className="send-button" disabled={busy} onClick={() => void mutate(`/api/memory/scopes/${detail.id}`, 'PATCH', { text: manual, revision: detail.revision })}>保存修正</button>
            <button className="new-chat" disabled={busy} onClick={() => void mutate(`/api/memory/scopes/${detail.id}/retry`, 'POST')}>重新安排整理</button></div>
          <h2>原文依据与互动记录</h2><div className="context-material">{detail.source_messages.map(source => <article key={source.id}>
            <small>{authors[source.author] ?? source.author} · {new Date(source.timestamp).toLocaleString()} · #{source.id}</small><p>{source.content}</p></article>)}</div>
          <h2>更新历史</h2>{detail.history.map(h => <details key={h.id}><summary>{h.kind === 'manual' ? '你的修正' : '自动整理'} · {new Date(h.created_at * 1000).toLocaleString()}</summary>
            <p className="memory-body">{h.body}</p></details>)}
        </>}
      </section></div>
    </main></div>;
}
