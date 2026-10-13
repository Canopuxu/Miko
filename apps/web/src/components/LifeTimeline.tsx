import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { EmptyState } from './ui';

export type LifeEntry = {text: string; goal_id?: string};
export type LifeEvent = LifeEntry & {day: string};
export type LifeGoal = {id: string; text: string; start_date: string; target_date: string;
  status: 'active' | 'completed' | 'paused'; completed_date?: string};
export const goalStatus = {active: '进行中', completed: '已完成', paused: '暂时搁置'};
type LifeData = {kind: 'root' | 'goal' | 'activity' | 'date'; label: string; branch?: string;
  goal?: LifeGoal; event?: LifeEvent; active?: boolean; choose?: () => void};
type LifeNode = Node<LifeData>;
const ordinary = 'ordinary';

function LifeNodeView({data}: NodeProps<LifeNode>) {
  if(data.kind==='date') return <time className="life-branch-date" dateTime={data.label}>
    <strong>{data.label.slice(5).replace('-','/')}</strong><small>{data.label.slice(0,4)}</small></time>;
  if(data.kind==='root') return <button className="life-branch-root nodrag nopan" onClick={e=>{e.stopPropagation();data.choose?.();}} aria-label="查看全部生活分支">
    Miko<Handle type="source" position={Position.Bottom}/></button>;
  const goal=data.kind==='goal';
  return <button className={`life-branch-node ${goal?'goal':'activity'} ${data.goal?.status ?? ''} ${data.branch===ordinary?'ordinary':''} ${data.active?'is-selected':''} nodrag nopan`}
    aria-label={goal?`${data.label}${data.goal?' · '+goalStatus[data.goal.status]:''}`:`${data.event?.day} · ${data.label}`}
    aria-pressed={!!data.active} onClick={e=>{e.stopPropagation();data.choose?.();}}>
    <Handle type="target" position={Position.Top} id="in" style={{left:12,top:19}}/>
    <span className="life-branch-dot" aria-hidden="true"/>
    <span className="life-branch-copy"><span>{data.label}</span>
      {data.goal && <small>{goalStatus[data.goal.status]} · 预计 {data.goal.target_date.slice(5).replace('-','/')}</small>}</span>
    <Handle type="source" position={Position.Bottom} id="out" style={{left:12,top:19,bottom:'auto'}}/>
  </button>;
}
const nodeTypes={life:LifeNodeView};

export function LifeTimeline({goals, events, onGoal, onDay}: {goals: LifeGoal[]; events: LifeEvent[];
  onGoal: (goal: LifeGoal) => void; onDay: (day: string) => void}) {
  const [day,setDay]=useState('');
  const [query,setQuery]=useState('');
  const [selection,setSelection]=useState('');
  const [availableWidth,setAvailableWidth]=useState(740);
  const container=useRef<HTMLDivElement>(null);
  const hasContent=!!(goals.length||events.length);
  useEffect(()=>{
    const element=container.current;
    if(!element)return;
    const observer=new ResizeObserver(([entry])=>setAvailableWidth(entry.contentRect.width));
    observer.observe(element);return()=>observer.disconnect();
  },[hasContent]);
  const clear=useCallback(()=>setSelection(''),[]);
  const graph=useMemo(()=>{
    const branches:{id:string;label:string;goal?:LifeGoal}[]=goals.map(g=>({id:g.id,label:g.text,goal:g}));
    if(events.some(e=>!e.goal_id))branches.push({id:ordinary,label:'日常'});
    const search=query.trim().toLocaleLowerCase();
    const visible=events.filter(e=>(!day||e.day===day) && (!search ||
      (e.text+' '+(branches.find(b=>b.id===(e.goal_id??ordinary))?.label ?? '')).toLocaleLowerCase().includes(search)));
    const shown=branches.filter(b=>!day&&!search || visible.some(e=>(e.goal_id??ordinary)===b.id) ||
      !day&&!!search&&b.label.toLocaleLowerCase().includes(search));
    const width=Math.max(availableWidth,72+shown.length*210);
    const lane=(width-72)/Math.max(1,shown.length);
    const nodes:LifeNode[]=[];
    const edges:Edge[]=[];
    const add=(id:string,data:LifeData,x:number,y:number,w:number,h:number)=>{
      const node:LifeNode={id,type:'life',position:{x,y},width:w,height:h,style:{width:w,height:h},
        focusable:false,data:{...data,active:id===selection,choose:data.choose??(()=>setSelection(id))}};
      nodes.push(node);return node;
    };
    add('root',{kind:'root',label:'Miko',choose:clear},92+Math.max(0,shown.length-1)*lane/2-36,8,72,34);
    const last=new Map<string,string>();
    shown.forEach((branch,i)=>{
      const id='goal:'+branch.id;
      add(id,{kind:'goal',label:branch.label,branch:branch.id,goal:branch.goal},72+i*lane+8,80,lane-20,90);
      edges.push({id:'root/'+id,source:'root',target:id,targetHandle:'in',type:'smoothstep',data:{branch:branch.id}});
      last.set(branch.id,id);
    });
    let y=190;
    const days=[...new Set(visible.map(e=>e.day))].sort();
    days.forEach(day=>{
      add('date:'+day,{kind:'date',label:day},0,y+14,52,48);
      let count=1;
      shown.forEach((branch,i)=>{
        const entries=visible.filter(e=>e.day===day&&(e.goal_id??ordinary)===branch.id);
        count=Math.max(count,entries.length);
        entries.forEach((event,j)=>{
          const id=branch.id+':'+day+':'+j;
          add(id,{kind:'activity',label:event.text,branch:branch.id,event},72+i*lane+8,y+j*86,lane-20,80);
          edges.push({id:last.get(branch.id)+'/'+id,source:last.get(branch.id)!,target:id,
            sourceHandle:'out',targetHandle:'in',type:'straight',data:{branch:branch.id}});
          last.set(branch.id,id);
        });
      });
      y+=count*86+24;
    });
    const picked=nodes.find(n=>n.id===selection);
    edges.forEach(edge=>{edge.className=picked?.data.branch===edge.data?.branch?'life-branch-edge active':'life-branch-edge';});
    return {nodes,edges,width,height:Math.max(300,y),picked,empty:!shown.length};
  },[goals,events,day,query,selection,availableWidth,clear]);
  const selected=graph.picked?.data;
  return <section className="life-timeline" aria-label="生活轨迹">
    <div className="life-timeline-filter">
      <input type="date" aria-label="筛选生活日期" value={day} onChange={e=>{setDay(e.target.value);setSelection('');}}/>
      <input type="search" aria-label="搜索生活记录" placeholder="搜索目标或活动" value={query} onChange={e=>{setQuery(e.target.value);setSelection('');}}/>
    </div>
    {!hasContent?<EmptyState title="暂无生活记录"/>:<>
      <div ref={container} className="life-branch-container">{graph.empty?<EmptyState title="没有匹配的生活记录"/>:<div style={{width:graph.width,height:graph.height}}>
        <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} nodesDraggable={false}
          nodesConnectable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false}
          panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false}
          preventScrolling={false} onPaneClick={clear} proOptions={{hideAttribution:true}}/>
      </div>}</div>
      {selected && selected.kind!=='root' && <div className="life-branch-detail" aria-live="polite">
        {selected?.kind==='goal'?<><span>{selected.label}{selected.goal&&` · ${goalStatus[selected.goal.status]} · 预计 ${selected.goal.target_date}`}</span>
          {selected.goal&&<button className="text-button" onClick={()=>onGoal(selected.goal!)}>编辑目标</button>}</>:
          selected?.event?<><span>{selected.event.day} · {selected.event.text}</span><button className="text-button" onClick={()=>onDay(selected.event!.day)}>编辑当天</button></>:
          null}
      </div>}
    </>}
  </section>;
}
