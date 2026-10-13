import { AppShell, NavigationContext } from '../components/AppShell';
import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { getJson, responseError } from '../api/chat';
import '../styles/persona.css';
import { ArrowUpRight, Pencil, Plus, Save, Search, Trash2 } from 'lucide-react';
import { useDiscardGuard, useNotice } from '../components/ui';

type Subject = 'miko' | 'owner';
type Entry = { id: string; subject: Subject; kind: 'overview' | 'fact' | 'recent' | 'excerpt';
  category: string; title: string; content: string; keywords: string; account: string; friend: string;
  as_of: string | null };
type Child = { id: string; name: string };
type Category = Child & { children: Child[] };
type Edit = { id: string | null; path: string; content: string; original?: Entry };
type CategoryEdit = { id?: string; parent?: string; name: string };
type Friend = { account: string; friend: string; name: string };
type Context = { role_material: Entry[]; retrieval?: { engine: string; error?: string } };

export function ProfilesPage() {
  const navigation = useContext(NavigationContext)!;
  const [subject, setSubject] = useState<Subject>('miko');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected] = useState('');
  const [q, setQ] = useState('');
  const [edit, setEdit] = useState<Edit | null>(null);
  const [categoryEdit, setCategoryEdit] = useState<CategoryEdit | null>(null);
  const [deleting, setDeleting] = useState<{ kind: 'category' | 'entry'; id: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const {notice,setNotice,setFailure,failed}=useNotice();
  const [revision, setRevision] = useState(0);
  const [question, setQuestion] = useState('');
  const [friends, setFriends] = useState<Friend[]>([]);
  const [previewFriend, setPreviewFriend] = useState('');
  const [preview, setPreview] = useState<Context | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const savedScroll = useRef<number | null>(null);

  useLayoutEffect(() => {
    if (contentRef.current && savedScroll.current !== null) {
      contentRef.current.scrollTop = savedScroll.current;
      savedScroll.current = null;
    }
  }, [entries, categories]);
  function preservePosition() { savedScroll.current = contentRef.current?.scrollTop ?? null; }

  useEffect(() => { let alive = true;
    void Promise.all([
      getJson<{ entries: Entry[] }>(`/api/profiles?subject=${subject}`),
      getJson<{ categories: Category[] }>(`/api/profiles/categories?subject=${subject}`),
    ]).then(([data, tree]) => { if (!alive) return;
      setEntries(data.entries); setCategories(tree.categories);
      setSelected(current => current === 'unfiled' || tree.categories.some(c => c.id === current)
        ? current : tree.categories[0]?.id ?? 'unfiled');
    }).catch(cause => { if (alive) setFailure(cause); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [subject, revision]);
  useEffect(() => { let alive = true;
    void getJson<{ friends: Friend[] }>('/api/qq/status').then(data => { if (alive) setFriends(data.friends); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const editDirty=!!edit && (edit.id ? edit.content!==edit.original?.content || edit.path!==edit.original?.category : !!edit.content.trim());
  const originalCategory=categoryEdit?.parent ? categories.find(c=>c.id===categoryEdit.parent)?.children.find(c=>c.id===categoryEdit.id)
    : categories.find(c=>c.id===categoryEdit?.id);
  const categoryDirty=!!categoryEdit && categoryEdit.name!==(originalCategory?.name ?? '');
  const guard=useDiscardGuard(editDirty || categoryDirty);
  function choose(id: string, action?: 'rename' | 'delete') {
    guard.proceed(()=>{
    savedScroll.current = null;
    if (contentRef.current) contentRef.current.scrollTop = 0;
    const category=categories.find(c=>c.id===id);
    setSelected(id); setEdit(null); setNotice('');
    setCategoryEdit(action==='rename' && category ? {id:category.id,name:category.name} : null);
    setDeleting(action==='delete' ? {kind:'category',id} : null);
    });
  }
  function chooseSubject(value: Subject) {
    if (value === subject) return;
    guard.proceed(()=>{
    setLoading(true); setSubject(value); setEntries([]); setCategories([]); setSelected(''); setEdit(null);
    setCategoryEdit(null); setDeleting(null); setNotice(''); setPreview(null);
    });
  }
  const root = categories.find(c => c.id === selected);
  const settings = entries.filter(e => e.kind === 'fact' || e.kind === 'overview');
  const known = new Set(categories.flatMap(c => [c.name, ...c.children.map(s => `${c.name}/${s.name}`)]));
  const unfiled = settings.filter(e => !known.has(e.category));
  const matches = (e: Entry) => !q.trim() || `${e.content} ${e.category}`.toLocaleLowerCase().includes(q.trim().toLocaleLowerCase());
  const sections = root ? [{ id: root.id, name: root.children.length ? '综合' : root.name, path: root.name },
    ...root.children.map(c => ({ ...c, path: `${root.name}/${c.name}` }))]
    : [{ id: 'unfiled', name: '待归类', path: '' }];
  const legacy = entries.filter(e => e.kind === 'recent' || e.kind === 'excerpt');

  async function request(url: string, method: string, body?: unknown) {
    const result = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!result.ok) throw new Error(await responseError(result));
    return result;
  }
  async function saveEntry() {
    if (!edit || !edit.content.trim()) return;
    setBusy(true); setNotice('');
    const original = edit.original;
    try {
      await request(edit.id ? `/api/profiles/entries/${edit.id}` : '/api/profiles', edit.id ? 'PUT' : 'POST', {
        subject, kind: original?.kind ?? (root?.id === `${subject}-core` && edit.path === root.name ? 'overview' : 'fact'),
        category: edit.path, title: edit.content.trim().split('\n')[0].slice(0, 80), content: edit.content.trim(),
        keywords: original?.keywords ?? '', account: original?.account ?? '', friend: original?.friend ?? '',
        as_of: original?.as_of ?? null,
      });
      preservePosition();
      setEdit(null); setRevision(v => v + 1); setPreview(null); setNotice('词条已保存');
    } catch (cause) { setFailure(cause); } finally { setBusy(false); }
  }
  async function saveTree(tree: Category[]) {
    await request('/api/profiles/categories', 'PUT', { subject, categories: tree });
    if (categoryEdit?.id || categoryEdit?.parent || deleting?.id !== selected) preservePosition();
    setCategoryEdit(null); setDeleting(null); setRevision(v => v + 1); setPreview(null);
  }
  async function saveCategory() {
    if (!categoryEdit?.name.trim()) return;
    setBusy(true); setNotice('');
    try {
      const data = { id: categoryEdit.id ?? crypto.randomUUID(), name: categoryEdit.name.trim() };
      let tree = categories;
      if (categoryEdit.parent) {
        tree = categories.map(c => c.id !== categoryEdit.parent ? c : { ...c, children: categoryEdit.id
          ? c.children.map(child => child.id === data.id ? data : child) : [...c.children, data] });
      } else {
        tree = categoryEdit.id ? categories.map(c => c.id === data.id ? { ...c, name: data.name } : c)
          : [...categories, { ...data, children: [] }];
      }
      await saveTree(tree);
      if (!categoryEdit.parent) setSelected(data.id);
      setNotice('分类已保存');
    } catch (cause) { setFailure(cause); } finally { setBusy(false); }
  }
  async function remove() {
    if (!deleting) return;
    setBusy(true); setNotice('');
    try {
      if (deleting.kind === 'entry') {
        await request(`/api/profiles/entries/${deleting.id}`, 'DELETE'); preservePosition(); setDeleting(null); setRevision(v => v + 1);
        if (edit?.id === deleting.id) setEdit(null); setNotice('词条已删除');
      } else {
        await saveTree(categories.filter(c => c.id !== deleting.id).map(c => ({ ...c,
          children: c.children.filter(child => child.id !== deleting.id) })));
        if (selected === deleting.id) setSelected('unfiled'); setNotice('分类已删除，已有词条保留在待归类中');
      }
      setPreview(null);
    } catch (cause) { setFailure(cause); } finally { setBusy(false); }
  }
  async function inspect() {
    setBusy(true); setNotice('');
    try {
      const friend = friends.find(f => `${f.account}:${f.friend}` === previewFriend);
      setPreview(await getJson<Context>(`/api/profiles/context?${new URLSearchParams({ q: question,
        ...(friend ? { account: friend.account, friend: friend.friend } : {}) })}`));
    } catch (cause) { setFailure(cause); } finally { setBusy(false); }
  }
  function editor() {
    return edit && <form className="persona-entry-editor" onSubmit={event => { event.preventDefault(); void saveEntry(); }}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); guard.proceed(()=>setEdit(null)); } }}>
      <label>词条内容<textarea autoFocus aria-label="词条内容" rows={4} maxLength={6000} value={edit.content}
        onChange={event => setEdit({ ...edit, content: event.target.value })} disabled={busy} placeholder="写下一条具体的设定" /></label>
      <div className="persona-edit-bottom">
        <div><button type="button" disabled={busy} onClick={() => guard.proceed(()=>setEdit(null))}>取消</button>
          <button className="send-button" disabled={busy || !edit.content.trim()}><Save size={14}/>{busy?'保存中…':'保存词条'}</button></div></div>
      {edit.id && <label className="persona-move">所属分类<select value={edit.path} disabled={busy}
        onChange={event => setEdit({ ...edit, path: event.target.value })}>
        <option value="">待归类</option>{!known.has(edit.path) && edit.path && <option value={edit.path}>{edit.path}</option>}
        {categories.map(c => <optgroup key={c.id} label={c.name}><option value={c.name}>{c.name} · 综合</option>
          {c.children.map(child => <option key={child.id} value={`${c.name}/${child.name}`}>{child.name}</option>)}</optgroup>)}
      </select></label>}
    </form>;
  }
  function categoryEditor() {
    return categoryEdit && <form className="persona-category-form" onSubmit={event => { event.preventDefault(); void saveCategory(); }}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); guard.proceed(()=>setCategoryEdit(null)); } }}>
      <label>{categoryEdit.id ? '分类新名称' : categoryEdit.parent ? '小分类名称' : '大分类名称'}<input autoFocus aria-label="分类名称" maxLength={36}
        value={categoryEdit.name} onChange={event => setCategoryEdit({ ...categoryEdit, name: event.target.value })} disabled={busy} /></label>
      <button className="send-button" disabled={busy || !categoryEdit.name.trim()}>保存分类</button>
      <button type="button" disabled={busy} onClick={() => guard.proceed(()=>setCategoryEdit(null))}>取消</button>
    </form>;
  }
  function deleteConfirmation() {
    return deleting && <div className="persona-delete" role="alert"><p>{deleting.kind === 'category'
      ? '删除这个分类？其中的词条将移到待归类，内容仍会保留。' : '删除这条词条？'}</p>
      <button disabled={busy} onClick={() => void remove()}>确认删除</button>
      <button disabled={busy} onClick={() => setDeleting(null)}>取消</button></div>;
  }

  return <AppShell view="profiles" sidebar={<section className="persona-browser" aria-label="人物分类">
    <div className="persona-subject" role="group" aria-label="人物"><button aria-pressed={subject === 'miko'} disabled={busy}
      onClick={() => chooseSubject('miko')}>Miko</button><button aria-pressed={subject === 'owner'} disabled={busy}
      onClick={() => chooseSubject('owner')}>我</button></div>
    <div className="search-field"><Search size={15}/><input className="persona-search" aria-label="搜索词条" value={q} onChange={event => setQ(event.target.value)} placeholder="搜索词条内容" /></div>
    <nav className="persona-categories" aria-label="大分类">{categories.map(c => <div key={c.id} className={`persona-category-row ${selected===c.id?'selected':''}`}>
      <button className="persona-category-label" disabled={busy} aria-current={selected === c.id ? 'page' : undefined}
        onClick={() => choose(c.id)}><span>{c.name}</span>
        <small>{settings.filter(e => (e.category === c.name || e.category.startsWith(c.name + '/')) && matches(e)).length}</small></button>
      <div className="persona-category-tools">
        <button disabled={busy} aria-label={`重命名分类 ${c.name}`} title="重命名分类" onClick={()=>choose(c.id,'rename')}><Pencil size={14} aria-hidden="true"/></button>
        <button disabled={busy} aria-label={`删除分类 ${c.name}`} title="删除分类" onClick={()=>choose(c.id,'delete')}><Trash2 size={14} aria-hidden="true"/></button>
      </div></div>)}
      <button aria-current={selected === 'unfiled' ? 'page' : undefined} disabled={busy} onClick={() => choose('unfiled')}>
        <span>待归类</span><small>{unfiled.filter(matches).length}</small></button></nav>
    <button className="persona-add-category" disabled={busy} onClick={() => guard.proceed(()=>{setEdit(null);setCategoryEdit({name:''});})}><Plus size={14}/>新增大分类</button>
  </section>}>
    <main className="main-panel persona-panel"><header className="page-header"><h1>{subject === 'miko' ? 'Miko 的人物设定' : '我的稳定资料'}</h1>
      <button className="persona-memory-link" onClick={() => navigation.navigate('memory',{person:subject,relation:undefined})}>近况与经历<ArrowUpRight size={14}/></button></header>
      <div className="persona-content" ref={contentRef}><div className="persona-category-heading"><div><small>当前分类</small><h2>{root?.name ?? '待归类'}</h2></div>
        {root && <div className="persona-category-actions"><button disabled={busy} onClick={() => guard.proceed(()=>{setEdit(null);setCategoryEdit({parent:root.id,name:''});})}><Plus size={14}/>小分类</button>
          <button disabled={busy} onClick={() => guard.proceed(()=>{setEdit(null);setCategoryEdit({id:root.id,name:root.name});})}>重命名</button>
          <button disabled={busy} onClick={() => setDeleting({ kind: 'category', id: root.id })}>删除分类</button></div>}</div>
        {notice && <p className={`persona-status ${failed?'error-notice':''}`} role={failed?'alert':'status'}>{notice}</p>}
        {categoryEdit && (!categoryEdit.parent || !categoryEdit.id) && categoryEditor()}
        {deleting?.kind === 'category' && deleting.id === root?.id && deleteConfirmation()}
        {loading ? <p className="profile-empty">正在读取…</p> : sections.map(section => {
          const items = (root ? settings.filter(e => e.category === section.path) : unfiled).filter(matches);
          return <section className="persona-section" key={section.id} aria-label={section.name}><header><h3>{section.name} <small>{items.length}</small></h3>
            <div>{root && section.id !== root.id && <><button disabled={busy} onClick={() => guard.proceed(()=>{setEdit(null);setCategoryEdit({id:section.id,parent:root.id,name:section.name});})}>重命名</button>
              <button disabled={busy} onClick={() => setDeleting({ kind: 'category', id: section.id })}>删除小分类</button></>}
              <button disabled={busy} onClick={() => guard.proceed(()=>{setCategoryEdit(null);setEdit({id:null,path:section.path,content:''});})}><Plus size={14}/>添加词条</button></div></header>
            {categoryEdit?.parent && categoryEdit.id === section.id && categoryEditor()}
            {deleting?.kind === 'category' && deleting.id === section.id && section.id !== root?.id && deleteConfirmation()}
            {!items.length && !(edit && !edit.id && edit.path === section.path) && <p className="persona-empty">还没有词条，想到时再补充。</p>}
            <div className="persona-entry-cloud">{items.map(entry => <article className={`persona-entry persona-tile${edit?.id === entry.id || (deleting?.kind === 'entry' && deleting.id === entry.id) ? ' is-editing' : ''}`} key={entry.id}>
              {edit?.id === entry.id ? editor() : <><p>{entry.content}</p><footer className="persona-tile-overlay"><span>{entry.account && '指定好友'}</span>
                <div><button disabled={busy} aria-label="编辑词条" title="编辑" onClick={() => guard.proceed(()=>{setCategoryEdit(null);setEdit({id:entry.id,path:entry.category,
                  content:entry.content,original:entry});})}><Pencil size={16} aria-hidden="true"/></button>
                  <button disabled={busy} aria-label="删除词条" title="删除" onClick={() => setDeleting({ kind: 'entry', id: entry.id })}><Trash2 size={16} aria-hidden="true"/></button></div></footer></>}
              {deleting?.kind === 'entry' && deleting.id === entry.id && deleteConfirmation()}
            </article>)}</div>
            {edit && !edit.id && edit.path === section.path && editor()}
          </section>;
        })}
        <details className="persona-preview"><summary>检索预览</summary><p>查看一段聊天会找到哪些人物资料。</p>
          <div className="profile-filters"><input aria-label="预览聊天内容" value={question} placeholder="输入想聊的内容" onChange={e => setQuestion(e.target.value)} />
            <select aria-label="预览对话对象" value={previewFriend} onChange={e => setPreviewFriend(e.target.value)}><option value="">我与 Miko</option>
              {friends.map(f => <option key={`${f.account}:${f.friend}`} value={`${f.account}:${f.friend}`}>{f.name}</option>)}</select>
            <button className="send-button" disabled={busy} onClick={() => void inspect()}>查看资料</button></div>
          {preview && <div className="context-material"><p>{preview.retrieval?.engine}{preview.retrieval?.error}</p>
            {preview.role_material.map(e => <article key={e.id}><small>{e.subject === 'miko' ? 'Miko' : '我'} · {e.category}</small><p>{e.content}</p></article>)}</div>}</details>
        {!!legacy.length && <details className="persona-preview"><summary>保留的旧近况与对话片段 · {legacy.length}</summary>
          <p>这些资料仍然保留；近况与经历由记忆模块维护。</p>{legacy.map(e => <article className="persona-entry" key={e.id}><small>{e.as_of}</small><p>{e.content}</p></article>)}</details>}
      </div>
    {guard.dialog}</main>
  </AppShell>;
}
