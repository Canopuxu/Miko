import { useEffect, useState } from 'react';
import { getJson, responseError } from '../api/chat';

type Sticker = { sticker_id: string; description: string; enabled: number; use_count: number };

export function StickerLibrary({ account }: { account: string }) {
  const [items, setItems] = useState<Sticker[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const base = `/api/qq/stickers/${account}`;
  useEffect(() => { let alive = true; setItems([]); setError('');
    void getJson<Sticker[]>(base).then(value => { if (alive) setItems(value); })
      .catch(cause => { if (alive) setError(String(cause)); });
    return () => { alive = false; };
  }, [base]);

  async function save(item: Sticker) {
    setBusy(true); setError('');
    try {
      const response = await fetch(`${base}/${item.sticker_id}`, { method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: item.description, enabled: !!item.enabled }) });
      if (!response.ok) throw new Error(await responseError(response));
      setItems(await getJson<Sticker[]>(base));
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  async function upload() {
    if (!file) return;
    setBusy(true); setError('');
    try {
      if (file.size > 6 * 1024 * 1024) throw new Error('图片超过 6 MiB 上限');
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = () => reject(new Error('图片读取失败')); reader.readAsDataURL(file);
      });
      const response = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_base64: data, description }) });
      if (!response.ok) throw new Error(await responseError(response));
      setItems(await getJson<Sticker[]>(base)); setDescription('');
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  return <details className="sticker-library"><summary>表情包 · {items.length}</summary>
    <p>给 Miko 添加表情，并写下含义和适用场景。这个账号的好友共用同一表情库。</p>
    {error && <p role="alert">{error}</p>}
    <div className="sticker-upload">
      <input aria-label="选择表情图片" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
        onChange={event => setFile(event.target.files?.[0] ?? null)} />
      <input aria-label="新表情含义和适用场景" value={description} placeholder="含义和适用场景"
        maxLength={200} onChange={event => setDescription(event.target.value)} />
      <button disabled={busy || !file || !description.trim()} onClick={() => void upload()}>添加</button>
    </div>
    <div className="sticker-grid">{items.map(item => <div key={item.sticker_id} className="sticker-card">
      <img src={`${base}/${item.sticker_id}/image`} alt={item.description} />
      <textarea aria-label="表情说明" value={item.description} maxLength={200}
        onChange={event => setItems(items.map(row => row.sticker_id === item.sticker_id ? { ...row, description: event.target.value } : row))} />
      <label><input type="checkbox" checked={!!item.enabled} disabled={busy}
        onChange={event => void save({ ...item, enabled: Number(event.target.checked) })} />启用</label>
      <button disabled={busy || !item.description.trim()} onClick={() => void save(item)}>保存说明</button>
    </div>)}</div>
  </details>;
}
