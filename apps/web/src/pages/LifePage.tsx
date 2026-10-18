import { useCharacterName, useOwnerName } from '../components/RuntimeStatus';
import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { getJson, responseError } from '../api/chat';
import { LifeTimeline, goalStatus, type LifeGoal, type LifeEntry, type LifeEvent } from '../components/LifeTimeline';
import '../styles/life.css';
import { Plus, RefreshCw, Save, X, Pencil, Trash2, CalendarDays, SlidersHorizontal } from 'lucide-react';
import { ExpressionSettingsDialog } from './ProfileTools';
import { useDiscardGuard } from '../components/ui';

type Entry = LifeEntry;
type Listing = {
  day: string; dates: string[];
  record: {date: string; entries: Entry[]; progress?: Entry[]; revision: number; source: string};
  status: {enabled: boolean; generating: boolean; error: string | null; today_recorded: boolean};
  goals: LifeGoal[]; revision: number; trajectory: LifeEvent[];
};
const today = () => new Date().toLocaleDateString('sv-SE');
const dayEntries=(record:Listing['record'])=>[...record.entries,...(record.progress??[])]
  .map((entry,index)=>({entry,index})).sort((a,b)=>(a.entry.order??a.index)-(b.entry.order??b.index)).map(({entry})=>entry);

export function LifePage() {
  const characterName = useCharacterName();
  const ownerName = useOwnerName();
  const [day, setDay] = useState(today);
  const [data, setData] = useState<Listing | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [mode, setMode] = useState<'timeline' | 'day'>('timeline');
  const [goal, setGoal] = useState<LifeGoal | null>(null);
  const [goalDirty, setGoalDirty] = useState(false);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const unsaved = dirty || goalDirty;
  const guard=useDiscardGuard(unsaved,()=>{setEntries(data?dayEntries(data.record):[]);setDirty(false);setGoal(null);setGoalDirty(false);});

  async function load(selected = day) {
    try {
      const value = await getJson<Listing>('/api/life?day=' + selected);
      setData(value); setEntries(dayEntries(value.record)); setDirty(false); setEditing(null); setError('');
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => { if(!unsaved)void load(day); }, [day,characterName,ownerName]);
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
      setData(value); setEntries(dayEntries(value.record)); setDirty(false); setEditing(null); setNotice('已保存');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function generate() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await request('/api/life/generate', 'POST');
      setDay(value.day); setData(value); setEntries(dayEntries(value.record)); setDirty(false); setEditing(null);
      setNotice('目标与今天的小事已更新');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  function openGoal(value?: LifeGoal) {
    if (busy) return;
    guard.proceed(()=>{
    setMode('timeline');
    setGoal(value ?? {id: '', text: '', start_date: today(), target_date: today(), status: 'active'});
    setGoalDirty(false); setNotice(''); setError('');
    });
  }
  async function saveGoal() {
    if (!data || !goal) return;
    setBusy(true); setError('');
    try {
      await request('/api/life/goals' + (goal.id ? '/' + goal.id : ''), goal.id ? 'PUT' : 'POST',
        {revision: data.revision, text: goal.text, done_when:goal.done_when, target_date: goal.target_date, status: goal.status});
      setGoal(null); setGoalDirty(false); await load(); setNotice('目标已保存');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function showDay(value: string) {
    if (busy) return;
    guard.proceed(()=>{setDay(value);setMode('day');setGoal(null);});
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
    <div className="life-view-switch"><button className={mode === 'timeline' ? 'selected' : ''} disabled={busy}
      onClick={() => guard.proceed(()=>setMode('timeline'))}>生活轨迹</button><button className={mode === 'day' ? 'selected' : ''}
      disabled={busy} onClick={() => guard.proceed(()=>{setMode('day');setGoal(null);})}>按日记录</button></div>
    {mode === 'timeline' && <div className="life-goal-list"><div className="life-list-heading">想做的事<button aria-label="添加目标"
      disabled={busy || (data?.goals.filter(goal=>goal.status!=='completed').length??0)>=3}
      title={(data?.goals.filter(goal=>goal.status!=='completed').length??0)>=3?'最多同时保留三个未完成目标':'添加目标'}
      onClick={() => openGoal()}><Plus size={16}/></button></div>
      {data?.goals.map(value => <button key={value.id} className={`life-goal-link ${value.status}`}
        disabled={busy} onClick={() => openGoal(value)}><span>{value.text}</span><small>{goalStatus[value.status]} · {(value.status==='completed'?value.completed_date??value.target_date:value.target_date).slice(5).replace('-','/')}</small></button>)}
      {!data?.goals.length && <p>暂无目标</p>}</div>}
    {mode === 'day' && <><label>查看日期<input aria-label="生活记录日期" type="date" value={day} disabled={busy}
      onChange={e => { if (e.target.value) showDay(e.target.value); }}/></label>
    <button className="life-button" onClick={() => showDay(today())}
      disabled={busy}>回到今天</button>
    <div className="life-date-list">{data?.dates.map(value => <button key={value}
      className={day === value ? 'selected' : ''} disabled={busy} onClick={() => showDay(value)}><CalendarDays size={15}/><span>{value}</span><small>{data.trajectory.filter(e=>e.day===value).length} 条</small></button>)}</div></>}
    <div className="life-controls"><p>{data?.status.enabled ? '每天 22:00 自动总结（北京时间）' : '自动记录已暂停'}</p>
      <button className="life-button" disabled={busy || data?.status.generating || !data} onClick={() => void toggle()}>
        {data?.status.enabled ? '暂停自动记录' : '恢复自动记录'}</button></div>
  </div>;

  return <AppShell view="life" sidebar={sidebar}><main className="main-panel life-panel">
    <header className="page-header"><h1>{mode === 'timeline' ? '生活轨迹' : day === today() ? '今天' : day}</h1>
      <div className="header-actions"><button className="ui-button" onClick={()=>setPreferencesOpen(true)}><SlidersHorizontal size={15}/>生成偏好</button>
      <button className="life-button" disabled={busy || unsaved || data?.status.generating} onClick={() => void generate()}>
        {busy || data?.status.generating ? '处理中…' : '推进今天'}</button></div>
    </header>
    {(error || data?.status.error) && <p className="profile-notice life-page-notice" role="alert">{error || data?.status.error}</p>}
    {mode === 'timeline' && data && <LifeTimeline goals={data.goals} events={data.trajectory} onGoal={openGoal} onDay={showDay}/>}
    {mode === 'timeline' && !goal && notice && <p className="life-timeline-notice" role="status">{notice}</p>}
    {goal && <aside className="life-goal-editor" aria-label="目标详情">
      <div className="life-record-header"><strong>{goal.id ? '想做的事' : '一个新目标'}</strong>
        <button className="icon-button" aria-label="关闭目标详情" title="关闭" disabled={busy}
          onClick={() => guard.proceed(()=>{setGoal(null);setGoalDirty(false);})}><X size={17}/></button></div>
      <label>目标<textarea aria-label="目标内容" rows={3} maxLength={240} value={goal.text} disabled={busy}
        onChange={e => {setGoal({...goal, text: e.target.value}); setGoalDirty(true);}}/></label>
      <label>完成条件<textarea aria-label="目标完成条件" rows={2} maxLength={500} value={goal.done_when??''} disabled={busy}
        onChange={e=>{setGoal({...goal,done_when:e.target.value});setGoalDirty(true);}}/></label>
      {goal.current_state&&<p>{goal.current_state}</p>}
      <label>预计完成<input aria-label="目标预计完成日期" type="date" value={goal.target_date} disabled={busy}
        onChange={e => {setGoal({...goal, target_date: e.target.value}); setGoalDirty(true);}}/></label>
      <label>状态<select aria-label="目标状态" value={goal.status} disabled={busy} onChange={e => {
        setGoal({...goal, status: e.target.value as LifeGoal['status']}); setGoalDirty(true);}}>
        {Object.entries(goalStatus).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label>
      <small>开始于 {goal.start_date}{goal.completed_date && ` · 完成于 ${goal.completed_date}`}</small>
      <button className="life-button life-save" disabled={busy || !goalDirty || !goal.text.trim() || !goal.target_date}
        onClick={() => void saveGoal()}><Save size={14}/>保存目标</button>
    </aside>}
    {mode === 'day' && <div className="life-workspace">
      <div className="life-record-header"><span>{data?.record.source === 'generated' ? `${characterName} 的生活记录` : data?.record.source === 'manual' ? '你修正的记录' : '尚未记录'}</span>
        <button className="life-button" disabled={busy} onClick={() => guard.proceed(()=>void load())}><RefreshCw size={14}/>刷新</button></div>
      {!entries.length && <div className="life-empty"><p>这一天还没有生活条目。</p><span>可以生成今天的记录，或直接写下简短的活动。</span></div>}
      <div className="life-entries">{entries.map((entry, index) => <article className={`life-entry ${editing === index ? 'editing' : 'reading'}`} key={index}>
        <span className="life-entry-number">{String(index+1).padStart(2,'0')}</span>
        {editing === index ? <div className="life-entry-fields">
        <textarea aria-label="生活条目" placeholder="如：练键盘，左手换和弦还是有点卡；慢速练了一会儿" rows={3} value={entry.text} maxLength={240}
          onChange={e => update(index, 'text', e.target.value)} disabled={busy}/>
        <select aria-label="条目关联目标" value={entry.goal_id ?? ''} disabled={busy || goalDirty}
          onChange={e => {setEntries(rows => rows.map((r, i) => i === index ?
            {...r, goal_id: e.target.value || undefined} : r)); setDirty(true);}}>
          <option value="">日常片段</option>{data?.goals.map(g => <option value={g.id} key={g.id}>{g.text}</option>)}</select>
        <button className="life-entry-done" onClick={()=>setEditing(null)}>完成编辑</button></div> : <div className="life-entry-content"><p>{entry.text || '尚未填写'}</p><small>{data?.goals.find(g=>g.id===entry.goal_id)?.text ?? '日常片段'}</small></div>}
        <div className="life-entry-tools"><button className="icon-button" aria-label="编辑生活条目" title="编辑" disabled={busy} onClick={()=>setEditing(editing===index?null:index)}><Pencil size={16}/></button>
        <button className="icon-button" aria-label="移除条目" title="移除" disabled={busy}
          onClick={() => { setEntries(entries.filter((_, i) => i !== index)); setEditing(null); setDirty(true); }}><Trash2 size={16}/></button></div>
      </article>)}</div>
      <div className="life-actions"><button className="life-button" disabled={busy || goalDirty || entries.length >= 100}
        onClick={() => { setEntries([...entries, {text: ''}]); setEditing(entries.length); setDirty(true); }}><Plus size={15}/>添加条目</button>
        <button className="life-button life-save" disabled={busy || !dirty || entries.some(e => !e.text.trim())}
          onClick={() => void save()}><Save size={14}/>{busy?'保存中…':'保存修改'}</button>
        {dirty && <button className="life-button" disabled={busy} onClick={() => void load()}>撤销修改</button>}
        <span role="status">{dirty ? '有未保存的修改' : notice}</span></div>
    </div>}
    {preferencesOpen && <ExpressionSettingsDialog scope="life" onClose={()=>setPreferencesOpen(false)}/>}
    {guard.dialog}</main></AppShell>;
}
