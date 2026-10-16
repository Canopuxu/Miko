import { useCharacterName } from './RuntimeStatus';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, ReactFlow, type Edge, type NodeChange, type NodeProps, type ReactFlowInstance } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Check, Maximize, Minus, Plus, ScanLine } from 'lucide-react';
import { EmptyState } from './ui';

import { buildLifeGraph, goalStatus, ordinary, type LifeNode, type LifeGoal, type LifeEvent } from './lifeGraph';
export { goalStatus, type LifeEntry, type LifeEvent, type LifeGoal } from './lifeGraph';

function LifeNodeView({data}: NodeProps<LifeNode>) {
  if(data.kind==='date') return <time className="life-branch-date" dateTime={data.label}>
    <strong>{data.label.slice(5).replace('-','/')}</strong><small>{data.label.slice(0,4)}</small></time>;
  if(data.kind==='root') return <button className="life-branch-root nodrag nopan" onClick={e=>{e.stopPropagation();data.choose?.();}} aria-label="查看全部生活分支">
    {data.label}<Handle type="source" position={Position.Bottom}/></button>;
  if(data.kind==='fork'||data.kind==='bend')return <div className="life-branch-junction" aria-hidden="true">
    <Handle type="target" position={Position.Top} id="in" style={{left:14,top:18}}/>
    {data.kind==='fork'&&<span/>}<Handle type="source" position={Position.Bottom} id="out" style={{left:14,top:18,bottom:'auto'}}/>
  </div>;
  const goal=data.kind==='goal';
  return <button className={`life-branch-node ${goal?'goal':'activity'} ${data.goal?.status ?? ''} ${data.branch===ordinary?'ordinary':''} ${data.active?'is-selected':''} nodrag nopan`}
    aria-label={goal?`${data.label}${data.goal?' · '+goalStatus[data.goal.status]:''}`:`${data.event?.day??data.day} · ${data.label}`}
    aria-pressed={!!data.active} onClick={e=>{e.stopPropagation();data.choose?.();}}>
    <Handle type="target" position={Position.Top} id="in" style={{left:14,top:18}}/>
    <span className="life-branch-dot" aria-hidden="true">{goal&&data.goal?.status==='completed'&&<Check size={15} strokeWidth={2.2}/>}</span>
    <span className="life-branch-copy"><span>{data.label}</span>
      {data.goal && <small>{data.goal.status==='completed'?`完成于 ${(data.goal.completed_date??data.day??'').slice(5).replace('-','/')}`:`${goalStatus[data.goal.status]} · 预计 ${data.goal.target_date.slice(5).replace('-','/')}`}</small>}</span>
    <Handle type="source" position={Position.Bottom} id="out" style={{left:14,top:18,bottom:'auto'}}/>
  </button>;
}
const nodeTypes={life:LifeNodeView};

export function LifeTimeline({goals, events, onGoal, onDay, asOf=new Date().toLocaleDateString('sv-SE')}: {goals: LifeGoal[]; events: LifeEvent[];
  onGoal: (goal: LifeGoal) => void; onDay: (day: string) => void; asOf?:string}) {
  const characterName = useCharacterName();
  const [flow,setFlow]=useState<ReactFlowInstance<LifeNode,Edge> | null>(null);
  const [day,setDay]=useState('');
  const [query,setQuery]=useState('');
  const [selection,setSelection]=useState('');
  const [heights,setHeights]=useState<ReadonlyMap<string,number>>(new Map());
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
  const measureNodes=useCallback((changes:NodeChange<LifeNode>[])=>{
    const sizes=changes.filter(change=>change.type==='dimensions'&&change.dimensions);
    if(!sizes.length)return;
    setHeights(previous=>{
      const updates=sizes.filter(change=>change.type==='dimensions'&&previous.get(change.id)!==change.dimensions!.height);
      if(!updates.length)return previous;
      const next=new Map(previous);
      for(const change of updates)if(change.type==='dimensions')next.set(change.id,change.dimensions!.height);
      return next;
    });
  },[]);
  const graph=useMemo(()=>{
    const value=buildLifeGraph(goals,events,characterName,asOf,day,query,heights);
    const picked=value.nodes.find(node=>node.id===selection);
    value.nodes.forEach(node=>{
      node.data={...node.data,active:node.id===selection,choose:node.id==='root'?clear:()=>setSelection(node.id)};
      if(node.data.kind==='activity'||node.data.kind==='goal'){
        node.height=undefined;node.style={width:node.width};
      }
    });
    value.edges.forEach(edge=>{if(picked?.data.branch===edge.data?.branch)edge.className+=' active';});
    return {...value,picked};
  },[goals,events,characterName,asOf,day,query,selection,clear,heights]);
  const frameStart=useCallback(()=>{
    if(!flow||graph.empty)return;
    const zoom=Math.min(1,Math.max(.65,(availableWidth-32)/graph.width));
    void flow.setViewport({x:Math.max(16,(availableWidth-graph.width*zoom)/2)-graph.left*zoom,y:20,zoom});
  },[flow,graph.empty,graph.width,graph.left,availableWidth]);
  // Long histories stay legible; the overview control can still fit the whole map.
  useEffect(()=>{frameStart();},[flow,availableWidth,day,query,goals.length,frameStart]);
  const selected=graph.picked?.data;
  return <section className="life-timeline" aria-label="生活轨迹">
    <div className="life-timeline-filter">
      <input type="date" aria-label="筛选生活日期" value={day} onChange={e=>{setDay(e.target.value);setSelection('');}}/>
      <input type="search" aria-label="搜索生活记录" placeholder="搜索目标或活动" value={query} onChange={e=>{setQuery(e.target.value);setSelection('');}}/>
    </div>
    {!hasContent?<EmptyState title="暂无生活记录"/>:<>
      <div ref={container} className="life-branch-container">{graph.empty?<EmptyState title="没有匹配的生活记录"/>:<div className="life-canvas">
        <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} nodesDraggable={false}
          nodesConnectable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false}
          onInit={setFlow} onNodesChange={measureNodes} minZoom={0.1} maxZoom={2}
          panOnDrag zoomOnScroll zoomOnPinch zoomOnDoubleClick={false}
          preventScrolling onPaneClick={clear} proOptions={{hideAttribution:true}}/>
        <div className="life-canvas-controls" aria-label="画布控制">
          <button aria-label="放大轨迹" title="放大" onClick={()=>void flow?.zoomIn()}><Plus size={16}/></button>
          <button aria-label="缩小轨迹" title="缩小" onClick={()=>void flow?.zoomOut()}><Minus size={16}/></button>
          <button aria-label="回到轨迹开头" title="回到开头" onClick={frameStart}><ScanLine size={16}/></button>
          <button aria-label="适配全部轨迹" title="适配全部" onClick={()=>void flow?.fitView({padding:0.12,maxZoom:1})}><Maximize size={16}/></button>
        </div>
      </div>}</div>
      {selected && (selected.goal||selected.event) && <div className="life-branch-detail" aria-live="polite">
        {selected?.kind==='goal'?<><span>{selected.label}{selected.goal&&` · ${goalStatus[selected.goal.status]} · ${selected.goal.status==='completed'?'完成于 '+(selected.goal.completed_date??selected.day):'预计 '+selected.goal.target_date}`}</span>
          {selected.goal&&<button className="text-button" onClick={()=>onGoal(selected.goal!)}>编辑目标</button>}</>:
          selected?.event?<><span>{selected.event.day} · {selected.event.text}</span><button className="text-button" onClick={()=>onDay(selected.event!.day)}>编辑当天</button></>:
          null}
      </div>}
    </>}
  </section>;
}
