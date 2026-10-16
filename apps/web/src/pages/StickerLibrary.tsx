import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Check, ImagePlus, Sparkles, Trash2, X } from 'lucide-react';
import { getJson, responseError } from '../api/chat';

type Meaning = { description: string; tags?: string[]; emotion?: string; usage?: string; avoid?: string; uncertainty?: string };
type Sticker = { sticker_id: string; description: string; enabled: number; use_count: number; last_state: string; semantic: Meaning };

async function imageData(file: Blob) {
  if (file.size > 6 * 1024 * 1024) throw new Error('图片超过 6 MiB 上限');
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('图片读取失败')); reader.readAsDataURL(file);
  });
}

export function StickerImport({ account, imageUrl, collectUrl, onClose, onSaved }: {
  account: string; imageUrl?: string; collectUrl?: string; onClose: () => void; onSaved?: () => void;
}) {
  const [file, setFile] = useState<Blob | null>(null);
  const [preview, setPreview] = useState('');
  const [meaning, setMeaning] = useState<Meaning>({ description: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  useEffect(() => {
    if (!imageUrl) return;
    let alive = true; setBusy('读取图片');
    void fetch(imageUrl).then(async response => {
      if (!response.ok) throw new Error(await responseError(response));
      return await response.blob();
    }).then(blob => { if (alive) setFile(blob); }).catch(cause => { if (alive) setError(String(cause)); })
      .finally(() => { if (alive) setBusy(''); });
    return () => { alive = false; };
  }, [imageUrl]);
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const url = URL.createObjectURL(file); setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  async function recognize() {
    if (!file) return;
    setBusy('识别含义'); setError('');
    try {
      const response = await fetch(`/api/qq/stickers/${account}/describe`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_base64: await imageData(file) }) });
      if (!response.ok) throw new Error(await responseError(response));
      setMeaning(await response.json());
    } catch (cause) { setError(String(cause)); } finally { setBusy(''); }
  }
  async function save() {
    if (!file) return;
    setBusy('保存'); setError('');
    try {
      const response = await fetch(collectUrl ?? `/api/qq/stickers/${account}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: meaning.description,
          semantic: meaning, ...(collectUrl ? {} : { image_base64: await imageData(file) }) }) });
      if (!response.ok) throw new Error(await responseError(response));
      onSaved?.(); onClose();
    } catch (cause) { setError(String(cause)); } finally { setBusy(''); }
  }
  return <Dialog.Root open onOpenChange={value => { if (!value && !busy) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="sticker-import-dialog">
      <header><Dialog.Title>添加表情</Dialog.Title><Dialog.Close className="icon-button" disabled={!!busy} aria-label="关闭添加表情"><X size={17}/></Dialog.Close></header>
      <Dialog.Description>保存在 Miko 本地表情库，这个 QQ 账号的会话共用。可以识别含义后修改，也可以直接填写。</Dialog.Description>
      {!imageUrl && <input aria-label="选择表情图片" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
        onChange={event => { setFile(event.target.files?.[0] ?? null); setMeaning({ description: '' }); }}/>}
      {preview && <img className="sticker-import-preview" src={preview} alt="待添加的表情"/>}
      <button className="ui-button" disabled={!file || !!busy} onClick={() => void recognize()}><Sparkles size={14}/>识别含义</button>
      <label>含义<textarea aria-label="表情含义" maxLength={200} value={meaning.description}
        onChange={event => setMeaning({ ...meaning, description: event.target.value, uncertainty: '' })}/></label>
      <label>适用场景（可选）<input aria-label="表情适用场景" maxLength={160} value={meaning.usage ?? ''}
        onChange={event => setMeaning({ ...meaning, usage: event.target.value })}/></label>
      {!!meaning.uncertainty && <p role="status">待确认：{meaning.uncertainty}。修改含义确认后即可用于回复。</p>}
      {error && <p role="alert">{error}</p>}
      <footer><span role="status">{busy && `正在${busy}…`}</span><button className="send-button" disabled={!file || !meaning.description.trim() || !!busy}
        onClick={() => void save()}>保存到表情库</button></footer>
    </Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function StickerLibrary({ account, refreshKey = 0 }: { account: string; refreshKey?: number }) {
  const [items, setItems] = useState<Sticker[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState('');
  const base = `/api/qq/stickers/${account}`;
  async function refresh() { setItems(await getJson<Sticker[]>(base)); }
  useEffect(() => { let alive = true; setItems([]); setError('');
    void getJson<Sticker[]>(base).then(value => { if (alive) setItems(value); })
      .catch(cause => { if (alive) setError(String(cause)); });
    return () => { alive = false; };
  }, [base, refreshKey]);
  useEffect(() => {
    if (!confirm) return;
    const cancel = () => setConfirm(''); document.addEventListener('click', cancel);
    return () => document.removeEventListener('click', cancel);
  }, [confirm]);
  async function save(item: Sticker) {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${base}/${item.sticker_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: item.description, enabled: !!item.enabled, semantic: item.semantic }) });
      if (!response.ok) throw new Error(await responseError(response));
      await refresh();
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true); setError('');
    try { const response = await fetch(`${base}/${id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseError(response)); await refresh();
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); setConfirm(''); }
  }
  return <details className="sticker-library"><summary>表情包 · {items.length}</summary>
    <div className="sticker-library-heading"><p>本地保存原图与动图，按话题选择使用。不会自动同步 QQ 收藏。</p>
      <button className="ui-button" onClick={() => setAdding(true)}><ImagePlus size={15}/>导入表情</button></div>
    {error && <p role="alert">{error}</p>}
    <div className="sticker-grid">{items.map(item => <div key={item.sticker_id} className="sticker-card">
      <img src={`${base}/${item.sticker_id}/image`} alt={item.description} loading="lazy"/>
      <textarea aria-label="表情含义" value={item.description} maxLength={200}
        onChange={event => setItems(items.map(row => row.sticker_id === item.sticker_id ? { ...row, description: event.target.value } : row))}/>
      <input aria-label="表情适用场景" placeholder="适用场景（可选）" maxLength={160} value={item.semantic.usage ?? ''}
        onChange={event => setItems(items.map(row => row.sticker_id === item.sticker_id ? { ...row, semantic: { ...row.semantic, usage: event.target.value } } : row))}/>
      <div className="sticker-card-actions"><label><input type="checkbox" checked={!!item.enabled} disabled={busy}
        onChange={event => void save({ ...item, enabled: Number(event.target.checked) })}/>启用</label>
        <small>已用 {item.use_count} 次</small><button className="icon-button" disabled={busy} aria-label={confirm === item.sticker_id ? '确认删除表情' : '删除表情'}
          title={confirm === item.sticker_id ? '确认删除' : '删除'} onClick={event => { event.stopPropagation();
            if (confirm === item.sticker_id) void remove(item.sticker_id); else setConfirm(item.sticker_id); }}>
          {confirm === item.sticker_id ? <Check size={14}/> : <Trash2 size={14}/>}</button></div>
      {!!item.semantic.uncertainty && <p>含义待确认，暂不自动使用</p>}
      {item.last_state === 'UNKNOWN' && <p>发送结果待核对，暂不自动使用</p>}
      <button className="ui-button" disabled={busy || !item.description.trim()} onClick={() => void save(item)}>保存修改</button>
    </div>)}</div>
    {adding && <StickerImport account={account} onClose={() => setAdding(false)} onSaved={() => void refresh().catch(cause => setError(String(cause)))}/>}
  </details>;
}
