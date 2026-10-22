import { useCharacterName } from './RuntimeStatus';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronLeft, ChevronRight } from 'lucide-react';
import { EmptyState } from './ui';
import { buildLifeGraph, monthLifeGraph, goalStatus, ordinary, type LifeData, type LifeGoal, type LifeEvent } from './lifeGraph';
export { goalStatus, type LifeEntry, type LifeEvent, type LifeGoal } from './lifeGraph';

function LifeNodeView({data,active,choose}:{data:LifeData;active:boolean;choose:()=>void}) {
  if(data.kind==='date')return <time className="life-branch-date" dateTime={data.label}>
    <strong>{data.label.slice(5).replace('-','/')}</strong><small>{data.label.slice(0,4)}</small></time>;
  if(data.kind==='fork'||data.kind==='bend')return <div className="life-branch-junction" aria-hidden="true">{data.kind==='fork'&&<span/>}</div>;
  const goal=data.kind==='goal';
  return <button className={`life-branch-node ${goal?'goal':'activity'} ${data.goal?.status??''} ${data.branch===ordinary?'ordinary':''} ${active?'is-selected':''}`}
    aria-label={goal?`${data.label}${data.goal?' · '+goalStatus[data.goal.status]:''}`:`${data.event?.day??data.day} · ${data.label}`}
    aria-pressed={active} onClick={choose}>
    <span className="life-branch-dot" aria-hidden="true">{goal&&data.goal?.status==='completed'&&<Check size={15} strokeWidth={2.2}/>}</span>
    <span className="life-branch-copy"><span>{data.label}</span>
      {data.goal&&<small>{data.goal.status==='completed'?`完成于 ${(data.goal.completed_date??data.day??'').slice(5).replace('-','/')}`:
        `${goalStatus[data.goal.status]} · 预计 ${data.goal.target_date.slice(5).replace('-','/')}`}</small>}</span>
  </button>;
}

function adjacentMonth(month:string,offset:number){
  const [year,value]=month.split('-').map(Number);
  const date=new Date(year,value-1+offset,1);
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`;
}

export function LifeTimeline({goals,events,month:selectedMonth,onMonthChange,asOf=new Date().toLocaleDateString('sv-SE')}:{goals:LifeGoal[];events:LifeEvent[];
  asOf?:string;month?:string;onMonthChange?:(month:string)=>void}) {
  const characterName=useCharacterName();
  const [localMonth,setLocalMonth]=useState(asOf.slice(0,7));
  const month=selectedMonth??localMonth;
  const [query,setQuery]=useState('');
  const [selection,setSelection]=useState('');
  const [heights,setHeights]=useState<ReadonlyMap<string,number>>(new Map());
  const [width,setWidth]=useState(740);
  const container=useRef<HTMLDivElement>(null);
  const track=useRef<HTMLDivElement>(null);
  useLayoutEffect(()=>{
    const element=container.current;
    if(!element)return;
    const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));
    observer.observe(element);return()=>observer.disconnect();
  },[]);
  const graph=useMemo(()=>monthLifeGraph(buildLifeGraph(goals,events,characterName,asOf,'',query,heights),month,width,selection),
    [goals,events,characterName,asOf,query,heights,month,width,selection]);
  useLayoutEffect(()=>{
    if(!track.current)return;
    const observer=new ResizeObserver(entries=>{
      setHeights(previous=>{
        const next=new Map(previous);let changed=false;
        for(const entry of entries){
          const id=(entry.target as HTMLElement).dataset.lifeNode!;
          const height=entry.borderBoxSize[0]?.blockSize??entry.contentRect.height;
          if(Math.abs((previous.get(id)??0)-height)>.5){next.set(id,height);changed=true;}
        }
        return changed?next:previous;
      });
    });
    track.current.querySelectorAll<HTMLElement>('[data-life-node]').forEach(node=>observer.observe(node));
    return()=>observer.disconnect();
  },[graph.nodes]);
  useEffect(()=>{container.current?.scrollTo({top:0});setSelection('');},[month,query]);
  const selected=graph.nodes.find(node=>node.id===selection)?.data;
  const detail=selected?.kind==='goal'?selected.event?.text||selected.goal?.current_state:selected?.event?.text;
  const showDetail=detail&&(selected?.kind!=='goal'||detail!==selected.label);
  const path=graph.selectionPath;
  const pathData=path.length?`M ${path[0].x1} ${path[0].y1} `+path.map(line=>`L ${line.x2} ${line.y2}`).join(' '):'';
  function changeMonth(value:string){if(value){setLocalMonth(value);onMonthChange?.(value);}}
  return <section className="life-timeline" aria-label="生活轨迹" onClick={event=>{
    if(!(event.target as Element).closest('.life-branch-node, .life-branch-detail, input, button'))setSelection('');
  }}>
    <div className="life-timeline-filter">
      <div className="life-month-switch" aria-label="切换生活月份">
        <button className="icon-button" aria-label="上个月" onClick={()=>changeMonth(adjacentMonth(month,-1))}><ChevronLeft size={17}/></button>
        <input type="month" aria-label="生活月份" value={month} onChange={e=>changeMonth(e.target.value)}/>
        <button className="icon-button" aria-label="下个月" onClick={()=>changeMonth(adjacentMonth(month,1))}><ChevronRight size={17}/></button>
      </div>
      <input type="search" aria-label="搜索生活记录" placeholder="搜索目标或活动" value={query} onChange={e=>setQuery(e.target.value)}/>
    </div>
    <div ref={container} className="life-branch-container" tabIndex={0} aria-label={`${month.replace('-','年')}月生活轨迹`}>
      {graph.empty?<EmptyState title={query?'这个月没有匹配的生活记录':'这个月还没有生活记录'}/>:
        <div ref={track} className="life-month-track" style={{height:graph.height}}>
          <svg className="life-month-lines" width="100%" height={graph.height} aria-hidden="true">
            {graph.lines.map(line=><line key={line.id} x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2}
              className={`life-month-line ${line.planned?'planned':''}`}/>)}
            {pathData&&<path key={selection} className="life-month-selection-path" d={pathData} pathLength={1}/>}
          </svg>
          <button className="life-branch-root life-month-root" style={{left:graph.rootX-40}} onClick={()=>setSelection('')}>{characterName}</button>
          {graph.nodes.map(node=><div key={node.id} className="life-month-node"
            data-life-node={node.data.kind==='activity'||node.data.kind==='goal'?node.id:undefined}
            style={{left:node.position.x,top:node.position.y,width:node.width}}>
            <LifeNodeView data={node.data} active={selection===node.id} choose={()=>setSelection(selection===node.id?'':node.id)}/>
          </div>)}
        </div>}
    </div>
    {showDetail&&<div className="life-branch-detail" aria-live="polite">
      <span>{selected?.kind!=='goal'&&selected?.event?selected.event.day+' · ':''}{detail}</span>
    </div>}
  </section>;
}
