import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';
import '../styles/life.css';

type Entry = {text: string};
type Listing = {
  day: string; dates: string[];
  record: {date: string; entries: Entry[]; revision: number; source: string};
  status: {enabled: boolean; generating: boolean; error: string | null; today_recorded: boolean};
};
const today = () => new Date().toLocaleDateString('sv-SE');

export function LifePage() {
  const [day, setDay] = useState(today);
  const [data, setData] = useState<Listing | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function load(selected = day) {
    try {
      const value = await getJson<Listing>('/api/life?day=' + selected);
      setData(value); setEntries(value.record.entries); setDirty(false); setError('');
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => { void load(day); }, [day]);
  useEffect(() => {
    if (!data?.status.generating || dirty || busy) return;
    const timer = window.setInterval(() => void load(day), 2000);
    return () => window.clearInterval(timer);
  }, [day, data?.status.generating, dirty, busy]);

  function update(index: number, key: keyof Entry, value: string) {
    setEntries(rows => rows.map((r, i) => i === index ? {...r, [key]: value} : r));
    setDirty(true); setNotice('');
  }
  async function request(path: string, method: string, body?: object) {
    const response = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined});
    if (!response.ok) throw new Error(await responseError(response));
    return response.json() as Promise<Listing>;
  }
  async function save() {
    if (!data) return;
    setBusy(true); setError('');
    try {
      const value = await request('/api/life', 'PUT', {day, revision: data.record.revision, entries});
      setData(value); setEntries(value.record.entries); setDirty(false); setNotice('已保存');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function generate() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await request('/api/life/generate', 'POST');
      setDay(value.day); setData(value); setEntries(value.record.entries); setDirty(false);
      setNotice('今天的条目已更新');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function toggle() {
    if (!data) return;
    setBusy(true);
    try {
      await request('/api/life/control', 'PUT', {enabled: !data.status.enabled});
      const value = await getJson<Listing>('/api/life?day=' + day);
      setData(value);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  const sidebar = <div className="life-navigation">
    <label>查看日期<input aria-label="生活记录日期" type="date" value={day} disabled={busy || dirty}
      onChange={e => { if (e.target.value) setDay(e.target.value); }}/></label>
    <button className="life-button" onClick={() => { setDay(today()); if (day === today()) void load(); }}
      disabled={busy || dirty}>回到今天</button>
    <div className="life-date-list">{data?.dates.map(value => <button key={value}
      className={day === value ? 'selected' : ''} disabled={busy || dirty} onClick={() => setDay(value)}>{value}</button>)}</div>
    <div className="life-controls"><p>{data?.status.enabled ? '自动记录已开启' : '自动记录已暂停'}</p>
      <button className="life-button" disabled={busy || data?.status.generating || !data} onClick={() => void toggle()}>
        {data?.status.enabled ? '暂停自动记录' : '恢复自动记录'}</button></div>
  </div>;

  return <AppShell view="life" sidebar={sidebar}><main className="main-panel life-panel">
    <header className="page-header"><div><h1>{day === today() ? '今天' : day}</h1>
      <p>Miko 在做的事 · 简短生活条目</p></div>
      <button className="life-button" disabled={busy || dirty || data?.status.generating} onClick={() => void generate()}>
        {busy || data?.status.generating ? '处理中…' : '生成今天'}</button>
    </header>
    <div className="life-workspace">
      {(error || data?.status.error) && <p className="profile-notice" role="alert">{error || data?.status.error}</p>}
      <div className="life-record-header"><span>{data?.record.source === 'generated' ? 'Miko 的生活记录' : data?.record.source === 'manual' ? '你修正的记录' : '尚未记录'}</span>
        <button className="life-button" disabled={busy || dirty} onClick={() => void load()}>刷新</button></div>
      {!entries.length && <div className="life-empty"><p>这一天还没有生活条目。</p><span>可以生成今天的记录，或直接写下简短的活动。</span></div>}
      <div className="life-entries">{entries.map((entry, index) => <article className="life-entry" key={index}>
        <textarea aria-label="生活条目" placeholder="如：练键盘，左手换和弦还是有点卡；慢速练了一会儿" rows={3} value={entry.text} maxLength={240}
          onChange={e => update(index, 'text', e.target.value)} disabled={busy}/>
        <button className="life-remove" aria-label="移除条目" disabled={busy}
          onClick={() => { setEntries(entries.filter((_, i) => i !== index)); setDirty(true); }}>×</button>
      </article>)}</div>
      <div className="life-actions"><button className="life-button" disabled={busy || entries.length >= 20}
        onClick={() => { setEntries([...entries, {text: ''}]); setDirty(true); }}>＋ 添加条目</button>
        <button className="life-button life-save" disabled={busy || !dirty || entries.some(e => !e.text.trim())}
          onClick={() => void save()}>保存修改</button>
        {dirty && <button className="life-button" disabled={busy} onClick={() => void load()}>撤销修改</button>}
        <span role="status">{dirty ? '有未保存的修改' : notice}</span></div>
    </div>
  </main></AppShell>;
}
