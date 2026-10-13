import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';
import { LifeTree, goalStatus, type LifeGoal, type LifeEntry, type LifeEvent } from '../components/LifeTree';
import '../styles/life.css';

type Entry = LifeEntry;
type Listing = {
  day: string; dates: string[];
  record: {date: string; entries: Entry[]; revision: number; source: string};
  status: {enabled: boolean; generating: boolean; error: string | null; today_recorded: boolean};
  goals: LifeGoal[]; revision: number; trajectory: LifeEvent[];
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
  const [mode, setMode] = useState<'tree' | 'day'>('tree');
  const [goal, setGoal] = useState<LifeGoal | null>(null);
  const [goalDirty, setGoalDirty] = useState(false);
  const unsaved = dirty || goalDirty;

  async function load(selected = day) {
    try {
      const value = await getJson<Listing>('/api/life?day=' + selected);
      setData(value); setEntries(value.record.entries); setDirty(false); setError('');
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => { void load(day); }, [day]);
  useEffect(() => {
    if (!data?.status.generating || unsaved || busy) return;
    const timer = window.setInterval(() => void load(day), 2000);
    return () => window.clearInterval(timer);
  }, [day, data?.status.generating, unsaved, busy]);

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
      setNotice('目标与今天的小事已更新');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  function openGoal(value?: LifeGoal) {
    if (busy || unsaved) return;
    setMode('tree');
    setGoal(value ?? {id: '', text: '', start_date: today(), target_date: today(), status: 'active'});
    setGoalDirty(false); setNotice(''); setError('');
  }
  async function saveGoal() {
    if (!data || !goal) return;
    setBusy(true); setError('');
    try {
      await request('/api/life/goals' + (goal.id ? '/' + goal.id : ''), goal.id ? 'PUT' : 'POST',
        {revision: data.revision, text: goal.text, target_date: goal.target_date, status: goal.status});
      setGoal(null); setGoalDirty(false); await load(); setNotice('目标已保存');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function showDay(value: string) {
    if (busy || unsaved) return;
    setDay(value); setMode('day'); setGoal(null);
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
    <div className="life-view-switch"><button className={mode === 'tree' ? 'selected' : ''} disabled={busy || unsaved}
      onClick={() => setMode('tree')}>生活轨迹</button><button className={mode === 'day' ? 'selected' : ''}
      disabled={busy || unsaved} onClick={() => setMode('day')}>按日记录</button></div>
    <div className="life-goal-list"><div className="life-list-heading">想做的事<button aria-label="添加目标"
      disabled={busy || unsaved} onClick={() => openGoal()}>＋</button></div>
      {data?.goals.map(value => <button key={value.id} className={`life-goal-link ${value.status}`}
        disabled={busy || unsaved} onClick={() => openGoal(value)}><span>{value.text}</span><small>{goalStatus[value.status]}</small></button>)}
      {!data?.goals.length && <p>先过平常的一天，也可以有新的打算。</p>}</div>
    <label>查看日期<input aria-label="生活记录日期" type="date" value={day} disabled={busy || unsaved}
      onChange={e => { if (e.target.value) showDay(e.target.value); }}/></label>
    <button className="life-button" onClick={() => { showDay(today()); if (day === today()) void load(); }}
      disabled={busy || unsaved}>回到今天</button>
    <div className="life-date-list">{data?.dates.map(value => <button key={value}
      className={day === value ? 'selected' : ''} disabled={busy || unsaved} onClick={() => showDay(value)}>{value}</button>)}</div>
    <div className="life-controls"><p>{data?.status.enabled ? '自动记录已开启' : '自动记录已暂停'}</p>
      <button className="life-button" disabled={busy || data?.status.generating || !data} onClick={() => void toggle()}>
        {data?.status.enabled ? '暂停自动记录' : '恢复自动记录'}</button></div>
  </div>;

  return <AppShell view="life" sidebar={sidebar}><main className="main-panel life-panel">
    <header className="page-header"><div><h1>{mode === 'tree' ? '生活轨迹' : day === today() ? '今天' : day}</h1>
      <p>{mode === 'tree' ? '一些想做的事，和每天的小片段' : 'Miko 在做的事 · 简短生活条目'}</p></div>
      <button className="life-button" disabled={busy || unsaved || data?.status.generating} onClick={() => void generate()}>
        {busy || data?.status.generating ? '处理中…' : '推进今天'}</button>
    </header>
    {(error || data?.status.error) && <p className="profile-notice life-page-notice" role="alert">{error || data?.status.error}</p>}
    {mode === 'tree' && data && <LifeTree goals={data.goals} events={data.trajectory} onGoal={openGoal} onDay={showDay}/>}
    {mode === 'tree' && !goal && <span className="life-tree-notice" role="status">{notice}</span>}
    {goal && <aside className="life-goal-editor" aria-label="目标详情">
      <div className="life-record-header"><strong>{goal.id ? '想做的事' : '一个新目标'}</strong>
        <button className="life-button" disabled={busy} onClick={() => {setGoal(null); setGoalDirty(false);}}>关闭</button></div>
      <label>目标<textarea aria-label="目标内容" rows={3} maxLength={240} value={goal.text} disabled={busy}
        onChange={e => {setGoal({...goal, text: e.target.value}); setGoalDirty(true);}}/></label>
      <label>预计完成<input aria-label="目标预计完成日期" type="date" value={goal.target_date} disabled={busy}
        onChange={e => {setGoal({...goal, target_date: e.target.value}); setGoalDirty(true);}}/></label>
      <label>状态<select aria-label="目标状态" value={goal.status} disabled={busy} onChange={e => {
        setGoal({...goal, status: e.target.value as LifeGoal['status']}); setGoalDirty(true);}}>
        {Object.entries(goalStatus).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label>
      <small>开始于 {goal.start_date}{goal.completed_date && ` · 完成于 ${goal.completed_date}`}</small>
      <button className="life-button life-save" disabled={busy || !goalDirty || !goal.text.trim() || !goal.target_date}
        onClick={() => void saveGoal()}>保存目标</button>
    </aside>}
    {mode === 'day' && <div className="life-workspace">
      <div className="life-record-header"><span>{data?.record.source === 'generated' ? 'Miko 的生活记录' : data?.record.source === 'manual' ? '你修正的记录' : '尚未记录'}</span>
        <button className="life-button" disabled={busy || unsaved} onClick={() => void load()}>刷新</button></div>
      {!entries.length && <div className="life-empty"><p>这一天还没有生活条目。</p><span>可以生成今天的记录，或直接写下简短的活动。</span></div>}
      <div className="life-entries">{entries.map((entry, index) => <article className="life-entry" key={index}>
        <textarea aria-label="生活条目" placeholder="如：练键盘，左手换和弦还是有点卡；慢速练了一会儿" rows={3} value={entry.text} maxLength={240}
          onChange={e => update(index, 'text', e.target.value)} disabled={busy}/>
        <select aria-label="条目关联目标" value={entry.goal_id ?? ''} disabled={busy || goalDirty}
          onChange={e => {setEntries(rows => rows.map((r, i) => i === index ?
            {...r, goal_id: e.target.value || undefined} : r)); setDirty(true);}}>
          <option value="">日常片段</option>{data?.goals.map(g => <option value={g.id} key={g.id}>{g.text}</option>)}</select>
        <button className="life-remove" aria-label="移除条目" disabled={busy}
          onClick={() => { setEntries(entries.filter((_, i) => i !== index)); setDirty(true); }}>×</button>
      </article>)}</div>
      <div className="life-actions"><button className="life-button" disabled={busy || goalDirty || entries.length >= 100}
        onClick={() => { setEntries([...entries, {text: ''}]); setDirty(true); }}>＋ 添加条目</button>
        <button className="life-button life-save" disabled={busy || !dirty || entries.some(e => !e.text.trim())}
          onClick={() => void save()}>保存修改</button>
        {dirty && <button className="life-button" disabled={busy} onClick={() => void load()}>撤销修改</button>}
        <span role="status">{dirty ? '有未保存的修改' : notice}</span></div>
    </div>}
  </main></AppShell>;
}
