import { useCharacterName } from '../components/RuntimeStatus';
import { useEffect, useState } from 'react';
import { responseError } from '../api/chat';

type Relationship = { account: string; friend: string; owner_relationship: string; miko_relationship: string;
  miko_introduced: number; intro_receipt_id: number | null };

export function FriendRelationships({ friend,onDirty }: { friend: Relationship;onDirty?:(dirty:boolean)=>void }) {
  const characterName = useCharacterName();
  const [owner, setOwner] = useState('');
  const [miko, setMiko] = useState('');
  const [introduced, setIntroduced] = useState<boolean | undefined>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [baseline,setBaseline]=useState({owner:friend.owner_relationship,miko:friend.miko_relationship});
  useEffect(()=>{onDirty?.(owner!==baseline.owner || miko!==baseline.miko ||
    (introduced!==undefined && introduced!==!!friend.miko_introduced));},[owner,miko,introduced,baseline,friend.miko_introduced]);
  useEffect(() => {
    if(owner!==baseline.owner || miko!==baseline.miko || (introduced!==undefined && introduced!==!!friend.miko_introduced))return;
    setOwner(friend.owner_relationship); setMiko(friend.miko_relationship);
    setBaseline({owner:friend.owner_relationship,miko:friend.miko_relationship});
    setIntroduced(undefined); setNotice('');
  }, [friend.owner_relationship,friend.miko_relationship]);
  useEffect(() => {
    setOwner(friend.owner_relationship);setMiko(friend.miko_relationship);
    setBaseline({owner:friend.owner_relationship,miko:friend.miko_relationship});
    setIntroduced(undefined);setNotice('');
  },[friend.account,friend.friend]);

  async function save() {
    setBusy(true); setNotice('');
    try {
      const response = await fetch(`/api/profiles/relationships/${friend.account}/${friend.friend}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ owner_relationship: owner, miko_relationship: miko, miko_introduced: introduced }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setNotice('已保存');
      setBaseline({owner,miko});setIntroduced(undefined);
    } catch (cause) { setNotice(String(cause)); } finally { setBusy(false); }
  }
  return <details className="relationship-panel"><summary>关系与介绍状态</summary>
    <p>你与这位好友、{characterName} 与这位好友的关系分别保存；自动整理出的记忆可从页面上方进入查看。</p>
    <fieldset disabled={busy} className="profile-fields">
      <label>你与好友的关系<textarea value={owner} maxLength={3000} rows={3}
        onChange={event => setOwner(event.target.value)} /></label>
      <label>{characterName} 与好友的交往<textarea value={miko} maxLength={3000} rows={3}
        onChange={event => setMiko(event.target.value)} /></label>
      <label className="check-field"><input type="checkbox" checked={introduced ?? !!friend.miko_introduced}
        onChange={event => setIntroduced(event.target.checked)} />{characterName} 已介绍过自己／好友已认识她</label>
      {friend.intro_receipt_id !== null && <small>介绍消息已确认发送</small>}
      <button className="send-button" onClick={() => void save()}>保存关系</button>
    </fieldset>
    {notice && <p role="status">{notice}</p>}
  </details>;
}
