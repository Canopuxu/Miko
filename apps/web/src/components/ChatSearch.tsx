import { useEffect, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowLeft, ArrowUpRight, LoaderCircle, Search, X } from 'lucide-react';
import { getJson } from '../api/chat';
import { views } from './AppShell';
import { usePersonName } from './RuntimeStatus';
import '../styles/history.css';

export type HistoryMessage = { id: string; conversation_id: string; conversation_title: string;
  source: 'web' | 'qq'; author: { id: string; kind: string; name: string }; text: string;
  created_at: number; recalled: boolean; account: string | null; friend: string | null };
type Results = { messages: HistoryMessage[]; total: number; has_more: boolean; snapshot: number };
type Context = { messages: HistoryMessage[]; has_before: boolean; has_after: boolean };
type Filter = { query: string; day: string; conversation_id: string };
const time = (value: number) => new Date(value).toLocaleString('zh-CN', { hour12: false });
const message = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);

function excerpt(text: string, query: string) {
  const positions = query.toLowerCase().split(/\s+/).filter(Boolean).map(term => text.toLowerCase().indexOf(term)).filter(i => i >= 0);
  const start = positions.length ? Math.max(0, Math.min(...positions) - 48) : 0;
  return (start ? '…' : '') + text.slice(start, start + 220) + (text.length > start + 220 ? '…' : '');
}
function MatchedText({ text, query }: { text: string; query: string }) {
  const terms = query.trim().split(/\s+/).filter(Boolean).map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!terms.length) return <>{text}</>;
  const pattern = new RegExp('(' + terms.join('|') + ')', 'gi');
  return <>{text.split(pattern).map((part, i) => i % 2 ? <mark key={i}>{part}</mark> : part)}</>;
}

export function ChatSearch({ source, conversationId, onClose, onOpen, navigationDisabled = false }: {
  source: 'web' | 'qq'; conversationId: string | null; onClose: () => void;
  onOpen: (item: HistoryMessage) => void; navigationDisabled?: boolean;
}) {
  const personName = usePersonName();
  const [query, setQuery] = useState('');
  const channel = views.find(view => view.id === (source === 'qq' ? 'qq' : 'chat'))!;
  const ChannelIcon = channel.icon;
  const [day, setDay] = useState('');
  const [current, setCurrent] = useState(!!conversationId);
  const [applied, setApplied] = useState<Filter | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [selected, setSelected] = useState<HistoryMessage | null>(null);
  const [context, setContext] = useState<Context | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const panel = useRef<HTMLDivElement>(null);
  const anchor = useRef<HTMLElement>(null);
  useEffect(() => {
    if (anchor.current) anchor.current.scrollIntoView({ block: 'center' });
    else panel.current?.scrollTo({ top: 0 });
  }, [context]);

  async function find(more = false) {
    const filters = more ? applied! : { query: query.trim(), day, conversation_id: current ? conversationId || '' : '' };
    if (!filters.query && !filters.day) { setError('请输入关键词或选择日期'); return; }
    setBusy(true); setError('');
    try {
      const params = new URLSearchParams({ source, query: filters.query, conversation_id: filters.conversation_id });
      if (filters.day) params.set('day', filters.day);
      if (more && results) { params.set('offset', String(results.messages.length)); params.set('snapshot', String(results.snapshot)); }
      const value = await getJson<Results>('/api/history/search?' + params);
      setResults(more && results ? { ...value, messages: [...results.messages, ...value.messages] } : value);
      setApplied(filters); setSelected(null); setContext(null);
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }

  async function read(item: HistoryMessage, before = 8, after = 8, preserveSelection = false) {
    setBusy(true); setError('');
    try {
      const params = new URLSearchParams({ source, conversation_id: item.conversation_id, message_id: item.id,
        before: String(before), after: String(after) });
      const value = await getJson<Context>('/api/history/context?' + params);
      setContext(value); if (!preserveSelection) setSelected(item);
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }

  return <Dialog.Root open onOpenChange={open => { if (!open) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="runtime-dialog history-dialog">
      <header className="runtime-heading"><Dialog.Title>查找聊天记录</Dialog.Title>
        <Dialog.Close className="icon-button" aria-label="关闭聊天查找"><X size={18}/></Dialog.Close></header>
      <Dialog.Description>查找<span className="history-channel"><ChannelIcon size={15} strokeWidth={1.6} aria-hidden="true"/>{channel.label}</span>中已保存的消息，按日期查看，或打开消息前后文。</Dialog.Description>
      <form onSubmit={event => { event.preventDefault(); void find(); }} className="history-search-form">
        <fieldset disabled={busy}>
          <label className="history-query">关键词<input aria-label="聊天关键词" placeholder="输入想找的词，多个词用空格分开" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></label>
          <label>日期<input aria-label="聊天日期" type="date" value={day} onChange={event => setDay(event.target.value)}/></label>
          <button className="send-button" type="submit" disabled={!query.trim() && !day}><Search size={15}/>查找</button>
          <label className="history-scope">范围<select aria-label="查找范围" value={current ? 'current' : 'all'} onChange={event => setCurrent(event.target.value === 'current')}>
            {conversationId && <option value="current">当前对话</option>}<option value="all">“{channel.label}”中的全部对话</option>
          </select></label>
          <p>多个关键词需同时出现；只选择日期时，按当天的消息顺序展示。</p>
        </fieldset>
      </form>
      {error && <p role="alert" className="error-notice history-error">{error}</p>}
      {selected && context ? <>
        <div className="history-context-heading"><button className="text-button" disabled={busy} onClick={() => { setSelected(null); setContext(null); setError(''); }}><ArrowLeft size={15}/>返回结果</button>
          <span>{selected.conversation_title}</span></div>
        <div ref={panel} className="history-context" aria-label="消息前后文" aria-busy={busy}>
          {context.has_before && <button className="ui-button history-window" disabled={busy} onClick={() => void read(context.messages[0], 16, 0, true)}>查看更早的消息</button>}
          {context.messages.map(item => <article ref={item.id === selected.id ? anchor : undefined} key={item.id} className={item.id === selected.id ? 'history-message selected' : 'history-message'}>
            <div><strong>{personName(item.author.id, item.author.name)}</strong><time>{time(item.created_at)}</time>{item.id === selected.id && <small>选中消息</small>}</div>
            <p className={item.recalled ? 'history-recalled' : ''}><MatchedText text={item.text || '（消息没有文字）'} query={applied?.query || ''}/></p>
          </article>)}
          {context.has_after && <button className="ui-button history-window" disabled={busy} onClick={() => void read(context.messages[context.messages.length - 1], 0, 16, true)}>查看更晚的消息</button>}
        </div>
        <footer className="history-footer"><span>{navigationDisabled ? '回复结束后可打开原对话' : '在原对话中可查看图片和批注'}</span>
          <button className="send-button" disabled={busy || navigationDisabled} onClick={() => onOpen(selected)}>在聊天中打开<ArrowUpRight size={15}/></button></footer>
      </> : <>
        <div className="history-result-heading" role="status">{busy ? <><LoaderCircle size={15} className="spinning"/>正在查找…</> : results ? `找到 ${results.total} 条消息` : '输入关键词，或选择想查看的日期'}</div>
        <div className="history-results" aria-label="聊天查找结果" aria-busy={busy}>
          {results?.messages.map(item => <button key={item.id} className="history-hit" disabled={busy} onClick={() => void read(item)}>
            <span className="history-hit-meta"><strong>{personName(item.author.id, item.author.name)}</strong><span>{item.conversation_title}</span><time>{time(item.created_at)}</time></span>
            <span className="history-hit-text"><MatchedText text={excerpt(item.text, applied?.query || '') || '（消息没有文字）'} query={applied?.query || ''}/></span>
          </button>)}
          {results?.total === 0 && <p className="history-empty">没有找到消息，可以换个词或调整日期、范围。</p>}
          {results?.has_more && <button className="ui-button history-window" disabled={busy} onClick={() => void find(true)}>加载更多结果</button>}
        </div>
      </>}
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
