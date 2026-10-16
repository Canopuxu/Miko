import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Download, Eye, RotateCcw, Save, Upload, X } from 'lucide-react';
import { getJson, responseError } from '../api/chat';
import { useDiscardGuard } from '../components/ui';
import { views } from '../components/AppShell';

type Expressions = { web: string; qq: string };
type ExpressionSettings = { values: Expressions; defaults: Expressions; effective: Expressions };
type Subject = 'miko' | 'owner';
const chatSections = [
  { key: 'qq' as const, ...views.find(view => view.id === 'qq')! },
  { key: 'web' as const, ...views.find(view => view.id === 'chat')! },
];
type ProfileFile = { format: 'miko-profile'; version: 1; subject: Subject; name: string;
  categories: { name: string; children: { name: string }[] }[];
  entries: { kind: string; category: string; title: string; content: string; keywords: string }[];
  expression?: Expressions | null };
const message = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
async function post<T>(path: string, body: unknown, method = 'POST'): Promise<T> {
  const response = await fetch('/api/profiles/' + path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error(response.status === 422 ? '内容格式不正确，请检查名称、分类和词条；导入时请选择 Miko 导出的资料文件' : await responseError(response));
  return response.json();
}

export function ExpressionSettingsDialog({ onClose }: { onClose: () => void }) {
  const [saved, setSaved] = useState<ExpressionSettings | null>(null);
  const [draft, setDraft] = useState<Expressions>({ web: '', qq: '' });
  const [tab, setTab] = useState<keyof Expressions>('qq');
  const [preview, setPreview] = useState<Expressions | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const dirty = !!saved && (draft.web !== saved.values.web || draft.qq !== saved.values.qq);
  const guard = useDiscardGuard(dirty);
  useEffect(() => { let alive = true;
    void getJson<ExpressionSettings>('/api/profiles/expression').then(value => {
      if (alive) { setSaved(value); setDraft(value.values); }
    }).catch(cause => { if (alive) setError(message(cause)); });
    return () => { alive = false; };
  }, []);
  async function apply() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await post<ExpressionSettings & { message: string }>('expression', draft, 'PUT');
      setSaved(value); setDraft(value.values); setNotice(value.message); setPreview(null);
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  async function inspect() {
    setBusy(true); setError('');
    try { setPreview((await post<{ effective: Expressions }>('expression/preview', draft)).effective); }
    catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  return <><Dialog.Root open onOpenChange={open => { if (!open && !busy) guard.proceed(onClose); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="runtime-dialog profile-tools-dialog">
      <header className="runtime-heading"><Dialog.Title>表达设置</Dialog.Title>
        <button className="icon-button" aria-label="关闭表达设置" disabled={busy} onClick={() => guard.proceed(onClose)}><X size={18}/></button></header>
      <Dialog.Description>这里设置角色怎样说话。性格、喜好等具体资料仍在词条中编辑。</Dialog.Description>
      <div className="profile-tools-tabs" role="tablist" aria-label="表达设置范围">
        {chatSections.map(({ key, label, icon: Icon }) => <button key={key} role="tab" aria-selected={tab === key} onClick={() => { setTab(key); setPreview(null); }}><Icon size={17} strokeWidth={1.6} aria-hidden="true"/>{label}</button>)}
      </div>
      {!saved ? <p role="status">{error || '正在读取…'}</p> : <>
        <p className="profile-tools-note">{tab === 'qq' ? '用于“QQ 聊天”中的私聊和群聊。' : '用于“聊天”中你与角色的直接对话。'}保存后从下一轮回复起生效。</p>
        <label className="profile-tools-editor">{tab === 'qq' ? 'QQ 聊天 · 表达要求' : '聊天 · 基础设定'}
          <textarea aria-label={tab === 'qq' ? 'QQ 聊天表达要求' : '聊天基础设定'} rows={9} maxLength={12000} disabled={busy} value={draft[tab]}
            onChange={event => { setDraft({ ...draft, [tab]: event.target.value }); setPreview(null); setNotice(''); }}/></label>
        <p className="profile-tools-note">文中的 Miko 和橙汁会自动替换为你设置的名字，可在预览中查看。</p>
        <div className="profile-tools-row"><button className="ui-button" disabled={busy} onClick={() => {
          setDraft({ ...draft, [tab]: saved.defaults[tab] }); setPreview(null); setNotice('已载入默认文字，保存后生效');
        }}><RotateCcw size={14}/>恢复默认</button>
          <button className="ui-button" disabled={busy || !draft.web.trim() || !draft.qq.trim()} onClick={() => void inspect()}><Eye size={14}/>预览称呼替换</button></div>
        {preview && <div className="profile-tools-preview"><h3>实际使用的{tab === 'qq' ? ' QQ 聊天表达要求' : '聊天基础设定'}</h3><pre>{preview[tab]}</pre></div>}
        {notice && <p role="status" className="profile-tools-notice">{notice}</p>}
        <footer className="dialog-actions"><button className="ui-button" disabled={busy} onClick={() => guard.proceed(onClose)}>关闭</button>
          <button className="send-button" disabled={busy || !dirty || !draft.web.trim() || !draft.qq.trim()} onClick={() => void apply()}><Save size={14}/>{busy ? '处理中…' : '保存并应用'}</button></footer>
      </>}
      {saved && error && <p role="alert">{error}</p>}
    </Dialog.Content></Dialog.Portal></Dialog.Root>{guard.dialog}</>;
}

export function ProfileTransferDialog({ subject, name, onClose, onApplied }: {
  subject: Subject; name: string; onClose: () => void; onApplied: () => void;
}) {
  const [card, setCard] = useState<ProfileFile | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [backup, setBackup] = useState('');
  async function exportFile() {
    setBusy(true); setError('');
    try {
      const value = await getJson<ProfileFile>('/api/profiles/portable/export?subject=' + subject);
      const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
      const anchor = document.createElement('a'); anchor.href = url;
      anchor.download = value.name.replace(/[\\/:*?"<>|]/g, '_') + '-资料.json'; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  async function read(file: File | undefined) {
    setCard(null); setNotice(''); setError(''); setBackup('');
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('资料文件超过 4 MiB，请选择 Miko 导出的资料文件');
      const body = JSON.parse(await file.text());
      if (body.subject !== subject) throw new Error(subject === 'miko' ? '请选择角色资料文件；我的资料请切换到“我”后导入' : '请选择我的资料文件；角色资料请切换到角色后导入');
      setCard((await post<{ file: ProfileFile }>('portable/preview', body)).file);
    } catch (cause) { setError(cause instanceof SyntaxError ? '文件不是有效的 JSON 资料文件' : message(cause)); }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!card) return;
    setBusy(true); setError('');
    try {
      const result = await post<{ message: string; backup_id: string }>('portable/import', card);
      setNotice(result.message); setBackup(result.backup_id); setCard(null); onApplied();
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  return <Dialog.Root open onOpenChange={open => { if (!open && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="runtime-dialog profile-tools-dialog">
      <header className="runtime-heading"><Dialog.Title>资料导入导出</Dialog.Title><Dialog.Close className="icon-button" aria-label="关闭资料导入导出" disabled={busy}><X size={18}/></Dialog.Close></header>
      <Dialog.Description>当前人物：{name}。资料文件包含名称、大小分类、稳定词条{subject === 'miko' ? '和表达设置' : ''}。</Dialog.Description>
      <section className="profile-tools-transfer"><h3>导出资料</h3><p className="profile-tools-note">用于保存或分享当前人物设定。聊天记录、长期记忆、好友关系和 API 密钥保留在本机。</p>
        <button className="ui-button" disabled={busy} onClick={() => void exportFile()}><Download size={14}/>导出 {name} 的资料</button></section>
      <section className="profile-tools-transfer"><h3>导入资料</h3><label className="profile-tools-file"><Upload size={15}/><span>选择 Miko 资料文件</span>
        <input type="file" aria-label="选择资料文件" accept="application/json,.json" disabled={busy} onChange={event => void read(event.target.files?.[0])}/></label>
        {card && <div className="profile-tools-preview"><h3>{name} → {card.name}</h3>
          <p>{card.categories.length} 个大分类 · {card.entries.length} 条资料{card.expression ? ' · 包含表达设置' : ''}</p>
          <p>确认后替换当前人物的名称、分类和稳定词条{card.expression ? '，并应用文件中的表达设置' : ''}。导入前会自动备份原资料。</p>
          <details><summary>查看文件内容</summary><div className="profile-file-entries">
            <p className="profile-tools-note">{card.categories.map(c => c.name + (c.children.length ? '（' + c.children.map(s => s.name).join('、') + '）' : '')).join('；')}</p>
            {card.entries.map((entry, i) => <article key={i}><small>{entry.category || '待归类'}</small><p>{entry.content}</p></article>)}
            {card.expression && chatSections.map(({ key, label, icon: Icon }) => <div key={key}><h4 className="profile-expression-heading"><Icon size={15} strokeWidth={1.6} aria-hidden="true"/>{label} · {key === 'qq' ? '表达要求' : '基础设定'}</h4><pre>{card.expression![key]}</pre></div>)}
          </div></details><button className="send-button" disabled={busy} onClick={() => void apply()}>确认导入并替换</button></div>}
      </section>
      {notice && <p role="status" className="profile-tools-notice">{notice}</p>}
      {backup && <a className="ui-button" href={'/api/profiles/portable/backups/' + backup} download><Download size={14}/>下载导入前的资料备份</a>}
      {error && <p role="alert">{error}</p>}{busy && <p role="status">正在处理…</p>}
    </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
