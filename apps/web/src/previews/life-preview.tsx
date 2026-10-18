import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LifeTimeline } from '../components/LifeTimeline';
import { RuntimeProvider } from '../components/RuntimeStatus';
import { mockAt } from './lifeMock';
import '../styles/app.css';
import '../styles/system.css';
import '../styles/life.css';
import './life-preview.css';

function Preview(){
  const [day,setDay]=useState('2026-10-09');
  const data=mockAt(day);
  return <main className="life-mock-page">
    <header className="life-mock-header"><div><span>MOCK · 不保存到真实记录</span><h1>生活轨迹</h1>
      <p>{data.events.length} 条进度与日常 · {data.goals.length} 个目标 · {data.goals.filter(goal=>goal.status==='completed').length} 个已完成</p></div>
      <nav aria-label="模拟阶段">{[['2026-10-02','起步 · 四个目标'],['2026-10-05','有目标完成'],['2026-10-09','后续自然分叉']].map(([value,label])=><button key={value} className={day===value?'selected':''} onClick={()=>setDay(value)}>{label}</button>)}</nav></header>
    <LifeTimeline goals={data.goals} events={data.events} asOf={day}/>
    <footer className="life-mock-footer"><span>按月查看，滚轮上下浏览。</span><span>模拟日期 {day}</span></footer>
  </main>;
}
createRoot(document.getElementById('app')!).render(<RuntimeProvider><Preview/></RuntimeProvider>);
