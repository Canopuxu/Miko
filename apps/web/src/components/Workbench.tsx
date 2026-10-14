import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

export function Workbench() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let count = 0;
    async function check() {
      try {
        const response = await fetch('http://127.0.0.1:7871/_miko/health', { signal: AbortSignal.timeout(3000) });
        if (response.ok && (await response.json()).ready) { if (alive) { setReady(true); setError(false); } return; }
      } catch {}
      if (alive) { if (++count < 15) timer = setTimeout(check, 2000); else setError(true); }
    }
    setError(false); void check();
    return () => { alive = false; clearTimeout(timer); };
  }, [attempt]);
  return <section className="agent-workbench">
    <header className="workbench-header" aria-hidden="true" />
    {ready ? <iframe title="DSH 原生工作台" src="http://127.0.0.1:7871/_miko/start" allow="clipboard-read; clipboard-write" />
      : <div className="workbench-loading" role="status"><p>{error ? '工作台尚未连接' : '正在连接工作台…'}</p>
        {error && <><p>运行 scripts/start_workbench.ps1 启动独立服务。</p><button onClick={() => setAttempt(x => x + 1)}><RefreshCw size={16}/>重新连接</button></>}</div>}
  </section>;
}
