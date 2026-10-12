import { useEffect, useState } from 'react';
import { responseError } from '../api/chat';

type Relationship = { account: string; friend: string; owner_relationship: string; miko_relationship: string;
  miko_introduced: number; intro_receipt_id: number | null };

export function FriendRelationships({ friend }: { friend: Relationship }) {
  const [owner, setOwner] = useState('');
  const [miko, setMiko] = useState('');
  const [introduced, setIntroduced] = useState<boolean | undefined>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  useEffect(() => {
    setOwner(friend.owner_relationship); setMiko(friend.miko_relationship);
    setIntroduced(undefined); setNotice('');
  }, [friend.account, friend.friend]);

  async function save() {
    setBusy(true); setNotice('');
    try {
      const response = await fetch(`/api/profiles/relationships/${friend.account}/${friend.friend}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner_relationship: owner, miko_relationship: miko, miko_introduced: introduced }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setNotice('已保存，将用于后续聊天');
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  return <details className="relationship-panel"><summary>关系与介绍状态</summary>
    <p>分别整理你们各自与这位好友的交往。聊天记录已保存，自动整理关系记忆后续接入。</p>
    <fieldset disabled={busy} className="profile-fields">
      <label>你与好友的关系<textarea value={owner} maxLength={3000} rows={3}
        onChange={event => setOwner(event.target.value)} /></label>
      <label>Miko 与好友的交往<textarea value={miko} maxLength={3000} rows={3}
        onChange={event => setMiko(event.target.value)} /></label>
      <label className="check-field"><input type="checkbox" checked={introduced ?? !!friend.miko_introduced}
        onChange={event => setIntroduced(event.target.checked)} />Miko 已介绍过自己／好友已认识她</label>
      {friend.intro_receipt_id !== null && <small>介绍消息已确认发送</small>}
      <button className="send-button" onClick={() => void save()}>保存关系</button>
    </fieldset>
    {notice && <p role="status">{notice}</p>}
  </details>;
}
