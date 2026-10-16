import { useContext, useEffect, useState } from 'react';
import { Check, ExternalLink, Trash2 } from 'lucide-react';
import { NavigationContext } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';

type Source = { message_id: string; text: string; recalled: boolean; sent_at: number };
type Expression = { id: string; situation: string; style: string; surface_form: string; enabled: number; count: number; source: Source | null };
type Jargon = { id: string; term: string; meaning: string; enabled: number; count: number; manual: number; is_jargon: number; source: Source | null };
type Library = { enabled: boolean; expression_count: number; jargon_count: number; buffered_messages: number; last_failure: string;
  library: { expressions_in_use: boolean; checked_expressions: number; expression_threshold: number }; expressions: Expression[]; jargon: Jargon[] };

export function GroupLanguage({ account, friend }: { account: string; friend: string }) {
  const navigation = useContext(NavigationContext);
  const base = `/api/qq/friends/${account}/${friend}/language`;
  const [data, setData] = useState<Library | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    async function refresh() {
      try { const value = await getJson<Library>(base); if (alive) { setData(value); setError(''); } }
      catch (cause) { if (alive) setError(String(cause)); }
    }
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [base]);
  useEffect(() => {
    if (!confirm) return;
    const cancel = () => setConfirm('');
    document.addEventListener('click', cancel);
    return () => document.removeEventListener('click', cancel);
  }, [confirm]);

  async function change(path: string, method: string, body?: unknown) {
    setBusy(true); setError('');
    try {
      const response = await fetch(base + path, { method, ...(body === undefined ? {} : {
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
      if (!response.ok) throw new Error(await responseError(response));
      setData(await response.json());
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); setConfirm(''); }
  }
  function sourceLink(source: Source | null) {
    return source && <details className="language-source"><summary>查看来源</summary>
      <blockquote>{source.text}</blockquote><button className="text-button" onClick={() => navigation?.navigate('qq',
        { friend, message: source.message_id })}><ExternalLink size={12}/>打开原消息</button></details>;
  }
  function remove(kind: string, id: string) {
    return <button className={`icon-button ${confirm === id ? 'confirm-delete-button' : ''}`} disabled={busy}
      aria-label={confirm === id ? '确认删除' : '删除'} title={confirm === id ? '确认删除' : '删除'}
      onClick={event => { event.stopPropagation(); if (confirm === id) void change(`/${kind}/${id}`, 'DELETE'); else setConfirm(id); }}>
      {confirm === id ? <Check size={15}/> : <Trash2 size={15}/>}</button>;
  }
  return <div className="group-language">
    <div className="language-heading"><label className="qq-idle-option"><button type="button" role="switch"
      aria-label="学习和使用本群表达" aria-checked={!!data?.enabled} className="qq-idle-switch" disabled={busy || !data}
      onClick={() => void change('', 'PUT', { enabled: !data?.enabled })}><span/></button>学习和使用本群表达</label>
      <button className="text-button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '收起' : '查看表达与黑话'}</button></div>
    {error && <p role="alert">{error}</p>}
    {open && data && <div className="language-body">
      <p className="language-caption">只学习开启后的群友原话，使用记忆整理模型批量处理。关闭后保留已有内容，并停止学习和使用。</p>
      {data.enabled && <p className="language-caption">{data.library.expressions_in_use ? '表达参考已投入使用' : `已核验 ${data.library.checked_expressions} 条表达，积累到 ${data.library.expression_threshold} 条后开始选用`} · 待整理 {data.buffered_messages} 条消息</p>}
      {!!data.last_failure && <p role="status">最近一次学习未完成，后续会按原模块规则重试；聊天仍可使用。</p>}
      <h3>表达 · {data.expression_count}</h3>
      {!data.expressions.length && <p className="language-caption">还没有学到可核验的表达，无需为这里单独准备消息。</p>}
      {data.expressions.map(item => <article className="language-item" key={item.id}>
        <div className="language-item-heading"><strong>{item.surface_form}</strong><span className="language-item-actions">
          <label><input type="checkbox" checked={!!item.enabled} disabled={busy}
            onChange={event => void change(`/expressions/${item.id}`, 'PATCH', { enabled: event.target.checked })}/>启用</label>{remove('expressions', item.id)}</span></div>
        <p>{item.situation} · {item.style}</p>{sourceLink(item.source)}</article>)}
      <h3>群内词语 · {data.jargon_count}</h3>
      {!data.jargon.length && <p className="language-caption">还没有观察到群内词语。</p>}
      {data.jargon.map(item => <article className="language-item" key={item.id}>
        <div className="language-item-heading"><strong>{item.term}</strong><span className="language-item-actions">
          <small>{item.manual ? '已手动修正' : item.is_jargon ? '已有释义' : '观察中'}</small>
          <label><input type="checkbox" checked={!!item.enabled} disabled={busy}
            onChange={event => void change(`/jargon/${item.id}`, 'PATCH', { enabled: event.target.checked })}/>启用</label>{remove('jargon', item.id)}</span></div>
        <div className="language-meaning"><input aria-label={`${item.term}的释义`} maxLength={600}
          value={drafts[item.id] ?? item.meaning} placeholder="群内含义，可手动填写"
          onChange={event => setDrafts({ ...drafts, [item.id]: event.target.value })}/>
          <button className="ui-button" disabled={busy || !(drafts[item.id] ?? '').trim() || drafts[item.id] === item.meaning}
            onClick={() => void change(`/jargon/${item.id}`, 'PATCH', { meaning: drafts[item.id].trim() })}>保存释义</button></div>
        {sourceLink(item.source)}</article>)}
      {(data.expression_count > 30 || data.jargon_count > 30) && <p className="language-caption">各显示最近 30 条记录。</p>}
    </div>}
  </div>;
}
