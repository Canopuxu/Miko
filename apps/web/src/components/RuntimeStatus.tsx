import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Activity, ArrowUpRight, CircleCheck, CirclePause, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { getJson } from '../api/chat';

type Status = {version: string; dsh: {ready: boolean}; chat: {configured: boolean; model: string | null; active_turns: number};
  memory: {configured: boolean; embedding_model: string | null; error: string | null; memory_engine: string;
    search_expansion?:{enabled:boolean; configured:boolean; running:boolean; total:number; ready:number; pending:number; failed:number; model:string|null; error:string|null};
    automatic: {enabled: number}}; life: {enabled: boolean; generating: boolean; error: string | null; today_recorded: boolean}};
type Qq = {character_name:string; owner_name:string; receiver: {state: string; note: string}; friends: {name: string; friend: string; enabled: number; blocked: number}[]};
type DesktopWindow = Window & {pywebview?: {api: {restart_backend: () => Promise<{ok: boolean; message?: string}>}}};
const RuntimeContext = createContext<{status: Status | null; qq: Qq | null; error: string; qqError:string; refresh: () => Promise<void>} | null>(null);
export const useRuntime = () => useContext(RuntimeContext)!;
export const useCharacterName = () => useRuntime().qq?.character_name ?? 'Miko';
export const useOwnerName = () => useRuntime().qq?.owner_name ?? '橙汁';
export function usePersonName() {
  const characterName = useCharacterName();
  const ownerName = useOwnerName();
  return (id: string, fallback: string) => id === 'miko' ? characterName : id === 'owner' ? ownerName : fallback;
}

export function RuntimeProvider({children}: {children: ReactNode}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [qq, setQq] = useState<Qq | null>(null);
  const [error, setError] = useState('');
  const [qqError,setQqError]=useState('');
  async function refresh() {
    const [a,b]=await Promise.allSettled([getJson<Status>('/api/status'),getJson<Qq>('/api/qq/status')]);
    if(a.status==='fulfilled'){setStatus(a.value);setError('');}else setError(String(a.reason));
    if(b.status==='fulfilled'){setQq(b.value);setQqError('');}else setQqError(String(b.reason));
  }
  useEffect(() => {void refresh(); const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);}, []);
  return <RuntimeContext.Provider value={{status, qq, error, qqError, refresh}}>{children}</RuntimeContext.Provider>;
}

export function RuntimeStatus({navigate}: {navigate: (view: 'qq' | 'life' | 'memory') => void}) {
  const {status, qq, error, qqError, refresh} = useRuntime();
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [restartError, setRestartError] = useState('');
  const desktop = (window as DesktopWindow).pywebview?.api;
  const enabled = qq?.friends.filter(f => f.enabled).length ?? 0;
  const problem = !!error || !!qqError || !!status?.memory.error || !!status?.memory.search_expansion?.error || !!status?.life.error;
  async function update() {setRefreshing(true); await refresh(); setRefreshing(false);}
  async function restart() {
    setRefreshing(true); setRestartError('');
    try {
      const result = await desktop!.restart_backend();
      if (!result.ok) setRestartError(result.message ?? '重启未完成，请重试');
      await refresh();
    } catch {setRestartError('重启未完成，请查看运行日志后重试');}
    finally {setRefreshing(false);}
  }
  function visit(view: 'qq' | 'life' | 'memory') {setOpen(false); navigate(view);}
  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Dialog.Trigger asChild><button className={`runtime-trigger ${problem ? 'needs-attention' : ''}`}>
      <span className="runtime-lamp"/><span>{error ? '后台未连接' : status ? problem ? '有待查看的状态' : '运行状态' : '连接中…'}</span><Activity size={15}/>
    </button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="runtime-dialog">
      <div className="runtime-heading"><Dialog.Title>运行状态</Dialog.Title><Dialog.Close className="icon-button" aria-label="关闭运行状态" title="关闭"><X size={19}/></Dialog.Close></div>
      <Dialog.Description className="sr-only">模型、QQ 接话、记忆整理与生活记录的当前状态。</Dialog.Description>
      {error && <p className="error-notice" role="alert">{desktop ? '后台连接已中断，可以重启后台继续使用。' : `${error}，恢复连接后可刷新。`}</p>}
      {restartError && <p className="error-notice" role="alert">{restartError}</p>}
      <div className="runtime-cards">
        <article><div className="runtime-card-title"><Activity size={18}/><h3>模型与后台</h3>
          {status?.dsh.ready && !error ? <CircleCheck size={17}/> : <CirclePause size={17}/>}</div>
          <strong>{error ? '连接已中断' : status?.chat.model ?? '尚未配置模型'}</strong>
          <p>{error ? '连接后台后更新状态' : status?.chat.active_turns ? `${status.chat.active_turns} 项生成正在进行` : status?.dsh.ready ? '后台已就绪' : '正在连接后台'}</p></article>
        <article><div className="runtime-card-title"><h3>QQ 接话</h3><button className="text-button" onClick={() => visit('qq')}>
          好友<ArrowUpRight size={14}/></button></div><strong>{qqError?'QQ 状态未更新':qq?.receiver.state === 'connected' ? 'QQ 已连接' : 'QQ 未连接'}</strong>
          <p>{qqError || qq?.receiver.note || '读取连接状态…'}</p><small>{enabled} 位好友已开启 · {qq?.friends.filter(f => f.blocked).length ?? 0} 位等待核对</small></article>
        <article><div className="runtime-card-title"><h3>记忆整理</h3><button className="text-button" onClick={() => visit('memory')}>
          记忆<ArrowUpRight size={14}/></button></div><strong>{error ? '状态未更新' : !status ? '读取状态…' : status.memory.automatic.enabled ? '自动整理已开启' : '自动整理已暂停'}</strong>
          <p>{error ? '连接后台后更新状态' : status?.memory.error ?? (status?.memory.configured ? '语义检索已就绪' : status ? '使用关键词检索' : '正在连接后台')}</p>
          {!error&&status?.memory.search_expansion&&<small>{status.memory.search_expansion.error??`口语检索表达 ${status.memory.search_expansion.ready} / ${status.memory.search_expansion.total}`}</small>}</article>
        <article><div className="runtime-card-title"><h3>自主生活</h3><button className="text-button" onClick={() => visit('life')}>
          生活<ArrowUpRight size={14}/></button></div><strong>{error ? '状态未更新' : !status ? '读取状态…' : status.life.generating ? '正在推进生活' : status.life.enabled ? '自动记录已开启' : '自动记录已暂停'}</strong>
          <p>{error ? '连接后台后更新状态' : status?.life.error ?? (status?.life.today_recorded ? '今天的小事已经保存' : status ? '今天尚未记录' : '正在连接后台')}</p></article>
      </div>
      <details className="runtime-details"><summary>技术详情</summary><dl><dt>版本</dt><dd>{status?.version ?? '—'}</dd>
        <dt>模型运行</dt><dd>独立 DSH</dd><dt>记忆整理</dt><dd>{status?.memory.memory_engine ?? '—'}</dd>
        <dt>Embedding</dt><dd>{status?.memory.embedding_model ?? '—'}</dd></dl></details>
      <div className="dialog-actions">{error && desktop && <button className="ui-button" disabled={refreshing} onClick={() => void restart()}>
        {refreshing ? <LoaderCircle size={15} className="spinning"/> : <RefreshCw size={15}/>}重启后台</button>}
        <button className="ui-button" disabled={refreshing} onClick={() => void update()}>
        {refreshing ? <LoaderCircle size={15} className="spinning"/> : <RefreshCw size={15}/>}刷新状态</button></div>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>;
}
