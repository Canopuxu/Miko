import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Archive, FolderOpen, LoaderCircle, Upload, X } from 'lucide-react';
import { getJson, responseError } from '../api/chat';

type Backup = { id: string; name: string; created_at: string; reason: string; bytes: number; files: number };
type Preview = { stage_id: string; created_at: string; app_version: string; bytes: number; files: number; has_api_keys: boolean };
type RestoreStatus = { state: string; message?: string; backup_id?: string };
const date = (value: string) => new Date(value).toLocaleString();
const size = (bytes: number) => bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1) + ' MiB' : Math.ceil(bytes / 1024) + ' KiB';
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
const defaultName = () => 'Miko备份-' + new Date().toLocaleString('sv-SE').replace(/:/g, '-');

export function DataBackups() {
  const [items, setItems] = useState<Backup[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [createError, setCreateError] = useState('');
  const [opening, setOpening] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [restoring, setRestoring] = useState(sessionStorage.getItem('miko-restoring') === 'yes');
  async function load() { setItems((await getJson<{ backups: Backup[] }>('/api/settings/backups')).backups); }
  useEffect(() => { void load().catch(cause => setError(errorText(cause))); }, []);
  useEffect(() => {
    const id = preview?.stage_id;
    return () => {
      if (id && sessionStorage.getItem('miko-restoring') !== 'yes')
        void fetch('/api/settings/backups/staged/' + id, { method: 'DELETE', keepalive: true }).catch(() => {});
    };
  }, [preview?.stage_id]);
  useEffect(() => {
    if (!restoring) return;
    let alive = true;
    const check = async () => {
      try {
        const status = await getJson<RestoreStatus>('/api/settings/backups/restore-status');
        if (!alive) return;
        if (status.state === 'completed') {
          sessionStorage.removeItem('miko-restoring');
          sessionStorage.setItem('miko-restore-result', status.message || '数据已恢复');
          window.location.assign('/?view=settings&section=data');
        } else if (status.state === 'error' || status.state === 'idle') {
          sessionStorage.removeItem('miko-restoring'); setRestoring(false);
          setError(status.message || '恢复未开始，请重新选择文件'); void load();
        } else { setNotice(status.message || '正在恢复数据…'); }
      } catch { if (alive) setNotice('服务正在重新启动，请稍候…'); }
    };
    void check(); const timer = window.setInterval(() => void check(), 1800);
    return () => { alive = false; window.clearInterval(timer); };
  }, [restoring]);
  useEffect(() => {
    const result = sessionStorage.getItem('miko-restore-result');
    if (result) { setNotice(result); sessionStorage.removeItem('miko-restore-result'); }
  }, []);
  async function create() {
    setBusy('create'); setError(''); setCreateError(''); setNotice('');
    try {
      const response = await fetch('/api/settings/backups', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) });
      if (!response.ok) throw new Error(await responseError(response));
      setNotice((await response.json()).message); setCreating(false); await load();
    } catch (cause) { setError(errorText(cause)); setCreateError(errorText(cause)); } finally { setBusy(''); }
  }
  async function openFolder(id: string) {
    setOpening(id); setError('');
    try {
      const response = await fetch('/api/settings/backups/' + id + '/open-folder', { method: 'POST' });
      if (!response.ok) throw new Error(await responseError(response));
    } catch (cause) { setError(errorText(cause)); } finally { setOpening(''); }
  }
  async function read(file?: File) {
    if (!file) return;
    setBusy('inspect'); setPreview(null); setError(''); setNotice('');
    try {
      if (file.size > 2 * 1024 ** 3) throw new Error('备份文件超过 2 GiB');
      const response = await fetch('/api/settings/backups/inspect', { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file });
      if (!response.ok) throw new Error(await responseError(response));
      setPreview(await response.json());
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(''); }
  }
  async function restore() {
    if (!preview) return;
    setBusy('restore'); setError('');
    try {
      const response = await fetch('/api/settings/backups/restore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage_id: preview.stage_id }) });
      if (!response.ok) throw new Error(await responseError(response));
      sessionStorage.setItem('miko-restoring', 'yes'); setConfirming(false); setRestoring(true); setPreview(null);
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(''); }
  }
  return <section className="data-backups"><h2>备份与恢复</h2>
    <p>备份聊天、资料、记忆、生活和设置，用于迁移或恢复。</p>
    <p className="settings-footnote">完整备份包含 API 密钥和私人聊天，请妥善保管；分享人物设定请用资料页的“导入导出”。</p>
    <div className="settings-actions"><button className="settings-primary" disabled={!!busy || restoring} onClick={() => { setName(defaultName()); setCreateError(''); setCreating(true); }}><Archive size={16}/>创建备份</button>
      <label className={`backup-file-picker ${busy || restoring ? 'disabled' : ''}`}><Upload size={16}/>选择备份恢复
        <input type="file" accept=".zip,application/zip" aria-label="选择完整备份" disabled={!!busy || restoring} onChange={event => { void read(event.target.files?.[0]); event.target.value = ''; }}/></label></div>
    {!!items.length && <details className="backup-list" open><summary>本机备份 · {items.length}</summary>
      {items.map(item => <div key={item.id}><span><strong>{item.name}</strong><small>{date(item.created_at)} · {size(item.bytes)}</small></span>
        <button className="text-button" disabled={!!opening} onClick={() => void openFolder(item.id)}>{opening === item.id ? <LoaderCircle size={15} className="spinning"/> : <FolderOpen size={15}/>}打开所在文件夹</button></div>)}
    </details>}
    {preview && <div className="backup-preview"><h3>待恢复的备份</h3><p>{date(preview.created_at)} · Miko {preview.app_version} · {size(preview.bytes)}</p>
      <p>会替换当前的聊天、资料、记忆、生活和设置。恢复前自动备份当前数据，完成后重新启动服务。QQ 需要重新选择账号连接。</p>
      <div className="settings-actions"><button className="settings-primary" disabled={!!busy} onClick={() => setConfirming(true)}>恢复这份备份</button><button disabled={!!busy} onClick={() => setPreview(null)}>取消</button></div></div>}
    {busy && <p role="status" className="settings-notice"><LoaderCircle size={16} className="spinning"/>{busy === 'create' ? '正在创建备份…' : busy === 'inspect' ? '正在检查备份文件…' : '正在准备恢复…'}</p>}
    {notice && <p role="status" className="settings-notice">{restoring && <LoaderCircle size={16} className="spinning"/>}{notice}</p>}
    {error && <p role="alert" className="error-notice">{error}</p>}
    <Dialog.Root open={creating} onOpenChange={open => { if (!busy) setCreating(open); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/>
      <Dialog.Content className="runtime-dialog backup-confirm-dialog" aria-describedby={undefined}><header className="runtime-heading"><Dialog.Title>创建备份</Dialog.Title>
        <Dialog.Close className="icon-button" aria-label="取消创建备份" disabled={!!busy}><X size={18}/></Dialog.Close></header>
        <form onSubmit={event => { event.preventDefault(); void create(); }}>
          <label className="backup-name-field">备份名称<input autoFocus value={name} maxLength={80} disabled={!!busy} onFocus={event => event.currentTarget.select()} onChange={event => setName(event.target.value)}/></label>
          {createError && <p role="alert" className="error-notice">{createError}</p>}
          <footer className="dialog-actions"><Dialog.Close className="ui-button" type="button" disabled={!!busy}>取消</Dialog.Close>
            <button className="send-button" type="submit" disabled={!!busy || !name.trim()}>{busy === 'create' ? <LoaderCircle size={15} className="spinning"/> : <Archive size={15}/>} {busy === 'create' ? '正在创建…' : '创建备份'}</button></footer>
        </form>
      </Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={confirming} onOpenChange={open => { if (!busy) setConfirming(open); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/>
      <Dialog.Content className="runtime-dialog backup-confirm-dialog"><header className="runtime-heading"><Dialog.Title>确认恢复数据？</Dialog.Title>
        <Dialog.Close className="icon-button" aria-label="取消恢复" disabled={!!busy}><X size={18}/></Dialog.Close></header>
        <Dialog.Description>将恢复到 {preview ? date(preview.created_at) : ''} 的备份。当前数据会先自动备份；恢复期间暂停聊天，服务会重新启动。</Dialog.Description>
        <footer className="dialog-actions"><Dialog.Close className="ui-button" disabled={!!busy}>取消</Dialog.Close><button className="send-button" disabled={!!busy} onClick={() => void restore()}>确认恢复并重启</button></footer>
      </Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
