import { AppShell } from '../components/AppShell';
import { useEffect, useState } from 'react';
import { getJson, responseError } from '../api/chat';

type Draft = { subject: 'miko' | 'owner'; kind: 'overview' | 'fact' | 'recent' | 'excerpt';
  category: string; title: string; content: string; keywords: string; account: string; friend: string;
  as_of: string | null; enabled: boolean };
type Entry = Omit<Draft, 'enabled'> & { id: string; enabled: number; source: string; updated_at: number };
type Friend = { account: string; friend: string; name: string };
type Context = { current_time: string; role_material: Entry[];
  friend_relationships: (Friend & { owner_relationship: string; miko_relationship: string; miko_introduced: boolean })[];
  retrieval?: { engine: string; error?: string };
  social_memory?: { subject: 'miko' | 'owner'; peer: string; automatic: string; manual_corrections: string }[] };
const kinds = { overview: '整体描述', fact: '设定／资料条目', recent: '手动近况', excerpt: '对话片段' };
const subjects = { miko: 'Miko', owner: '我' };
const blank = (): Draft => ({ subject: 'miko', kind: 'fact', category: '', title: '', content: '',
  keywords: '', account: '', friend: '', as_of: null, enabled: true });

export function ProfilesPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [friends, setFriends] = useState<Friend[]>([]);
  const [q, setQ] = useState('');
  const [subject, setSubject] = useState('');
  const [kind, setKind] = useState('');
  const [category, setCategory] = useState('');
  const [draft, setDraft] = useState<Draft>(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [question, setQuestion] = useState('');
  const [previewFriend, setPreviewFriend] = useState('');
  const [preview, setPreview] = useState<Context | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => { let alive = true;
    void getJson<{ friends: Friend[] }>('/api/qq/status').then(value => { if (alive) setFriends(value.friends); })
      .catch(cause => { if (alive) setNotice(String(cause)); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { let alive = true; setLoading(true);
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ q, subject, kind });
      void getJson<{ entries: Entry[] }>(`/api/profiles?${params}`).then(value => { if (alive) setEntries(value.entries); })
        .catch(cause => { if (alive) setNotice(String(cause)); }).finally(() => { if (alive) setLoading(false); });
    }, 200);
    return () => { alive = false; clearTimeout(timer); };
  }, [q, subject, kind, revision]);

  function edit(entry: Entry) {
    setEditing(entry.id); setNotice('');
    setDraft({ subject: entry.subject, kind: entry.kind, category: entry.category, title: entry.title,
      content: entry.content, keywords: entry.keywords, account: entry.account, friend: entry.friend,
      as_of: entry.as_of, enabled: !!entry.enabled });
  }
  async function save() {
    setBusy(true); setNotice('');
    try {
      const response = await fetch(editing ? `/api/profiles/entries/${editing}` : '/api/profiles', {
        method: editing ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const value: Entry = await response.json(); setEditing(value.id);
      setNotice(value.enabled ? '已保存并用于后续聊天' : '已保存，暂不用于聊天');
      setRevision(value => value + 1); setPreview(null);
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!editing || !window.confirm('删除这条资料？')) return;
    setBusy(true); setNotice('');
    try {
      const response = await fetch(`/api/profiles/entries/${editing}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseError(response));
      setEditing(null); setDraft(blank()); setRevision(value => value + 1); setPreview(null); setNotice('已删除');
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  async function inspect() {
    setBusy(true); setNotice('');
    try {
      const selected = friends.find(f => `${f.account}:${f.friend}` === previewFriend);
      const params = new URLSearchParams({ q: question, ...(selected ? { account: selected.account, friend: selected.friend } : {}) });
      setPreview(await getJson<Context>(`/api/profiles/context?${params}`));
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  const categories = [...new Set(entries.map(e => e.category).filter(Boolean))];
  const shown = entries.filter(e => !category || e.category === category);
  return <AppShell view="profiles" sidebar={
        <section className="profile-browser" aria-label="资料库">
          <div className="profile-filters">
            <input aria-label="搜索资料" value={q} placeholder="搜索标题、内容或关键词" onChange={event => setQ(event.target.value)} />
            <select aria-label="按主体筛选" value={subject} onChange={event => setSubject(event.target.value)}>
              <option value="">全部主体</option>{Object.entries(subjects).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select>
            <select aria-label="按类型筛选" value={kind} onChange={event => setKind(event.target.value)}>
              <option value="">全部类型</option>{Object.entries(kinds).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select>
            <select aria-label="按主题筛选" value={category} onChange={event => setCategory(event.target.value)}>
              <option value="">全部主题</option>{[...new Set([...categories, category].filter(Boolean))].map(name => <option key={name}>{name}</option>)}</select>
          </div>
          {loading ? <p>正在读取…</p> : !shown.length ? <p className="profile-empty">暂无匹配资料。可以先写整体描述，再逐步补充细节。</p> : shown.map(entry =>
            <button disabled={busy} className={`profile-card ${entry.id === editing ? 'active' : ''}`} key={entry.id} onClick={() => edit(entry)}>
              <small>{subjects[entry.subject]} · {kinds[entry.kind]}{entry.category && ` · ${entry.category}`}{!entry.enabled && ' · 未启用'}</small>
              <strong>{entry.title}</strong><p>{entry.content.slice(0, 110)}</p>
              {entry.as_of && <small>{entry.as_of}</small>}
            </button>)}
        </section>
  }>
    <main className="main-panel">
      <header className="page-header"><div><h1>设定与资料</h1><p>Miko、你的资料，以及你提供的近期状态</p></div>
        <button className="send-button" disabled={busy} onClick={() => { setDraft(blank()); setEditing(null); setNotice(''); }}>新增条目</button></header>
      <div className="profile-workspace">
        <section className="profile-editor" aria-label="资料编辑">
          <h2>{editing ? '编辑资料' : '新增资料'}</h2>
          <fieldset className="profile-fields" disabled={busy}>
            <div className="profile-row"><label>主体<select value={draft.subject} onChange={event => setDraft({ ...draft, subject: event.target.value as Draft['subject'] })}>
              {Object.entries(subjects).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
              <label>类型<select value={draft.kind} onChange={event => setDraft({ ...draft, kind: event.target.value as Draft['kind'] })}>
                {Object.entries(kinds).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
            <label>主题<input value={draft.category} maxLength={80} placeholder="自由填写，例如外貌、喜好、工作"
              onChange={event => setDraft({ ...draft, category: event.target.value })} /></label>
            <label>标题<input value={draft.title} maxLength={100} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
            <label>内容<textarea rows={9} value={draft.content} maxLength={6000} onChange={event => setDraft({ ...draft, content: event.target.value })} /></label>
            <label>检索关键词<input value={draft.keywords} maxLength={300} placeholder="可选，补充相关称呼或近义词"
              onChange={event => setDraft({ ...draft, keywords: event.target.value })} /></label>
            <label>发生日期{draft.kind === 'recent' ? '（近况必填）' : '（可选）'}<input type="date" value={draft.as_of ?? ''}
              onChange={event => setDraft({ ...draft, as_of: event.target.value || null })} /></label>
            <label>关联好友<select value={draft.account ? `${draft.account}:${draft.friend}` : ''}
              onChange={event => { const selected = friends.find(f => `${f.account}:${f.friend}` === event.target.value);
                setDraft({ ...draft, account: selected?.account ?? '', friend: selected?.friend ?? '' }); }}>
              <option value="">共用资料</option>{friends.map(f => <option key={`${f.account}:${f.friend}`} value={`${f.account}:${f.friend}`}>{f.name} · {f.friend}</option>)}</select></label>
            <label className="check-field"><input type="checkbox" checked={draft.enabled} onChange={event => setDraft({ ...draft, enabled: event.target.checked })} />用于聊天</label>
            <div className="profile-actions"><button className="send-button" disabled={!draft.title.trim() || !draft.content.trim() || (draft.kind === 'recent' && !draft.as_of)}
              onClick={() => void save()}>{draft.enabled ? '保存并用于聊天' : '保存资料'}</button>
              {editing && <button className="new-chat" onClick={() => void remove()}>删除</button>}</div>
          </fieldset>
          {notice && <p role="status" className="profile-notice">{notice}</p>}
        </section>
        <section className="context-preview" aria-label="检索预览">
          <h2>检索预览</h2><p>输入一段聊天，查看会提供给 Miko 的资料。语义检索会调用 Embedding 接口，此处不生成回复或发送消息。</p>
          <div className="profile-filters"><input aria-label="预览聊天内容" value={question} placeholder="输入想聊的内容" onChange={event => setQuestion(event.target.value)} />
            <select aria-label="预览对话对象" value={previewFriend} onChange={event => setPreviewFriend(event.target.value)}>
              <option value="">我与 Miko 的聊天</option>{friends.map(f => <option key={`${f.account}:${f.friend}`} value={`${f.account}:${f.friend}`}>{f.name}</option>)}</select>
            <button className="send-button" disabled={busy} onClick={() => void inspect()}>查看资料</button></div>
          {preview && <div className="context-material">
            <p>{preview.retrieval?.engine}{preview.retrieval?.error && ` · ${preview.retrieval.error}`}</p>
            {!preview.role_material.length && !preview.friend_relationships.length && <p>目前没有可用资料。</p>}
            {preview.role_material.map(entry => <article key={entry.id}><small>{subjects[entry.subject]} · {kinds[entry.kind]}{entry.as_of && ` · ${entry.as_of}`} · 由你填写</small>
              <strong>{entry.title}</strong><p>{entry.content}</p></article>)}
            {preview.friend_relationships.map(f => <article key={`${f.account}:${f.friend}`}><strong>{f.name} · 关系资料</strong>
              <p>你与好友：{f.owner_relationship || '尚未填写'}</p><p>Miko 与好友：{f.miko_relationship || '尚未填写'}</p>
              <small>{f.miko_introduced ? '已介绍过 Miko' : '尚无介绍记录'}</small></article>)}
            {preview.social_memory?.map(r => <article key={`${r.subject}:${r.peer}`}><strong>{subjects[r.subject]} · 关系与经历记忆</strong>
              <p>{r.automatic}</p>{r.manual_corrections && <p>你的补充：{r.manual_corrections}</p>}</article>)}
          </div>}
        </section>
      </div>
    </main>
  </AppShell>;
}
