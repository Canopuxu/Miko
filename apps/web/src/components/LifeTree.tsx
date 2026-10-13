import { useMemo, useState } from 'react';
import { hierarchy, tree } from 'd3-hierarchy';
import { Background, Controls, Handle, Position, ReactFlow, type Node, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

export type LifeEntry = {text: string; goal_id?: string};
export type LifeEvent = LifeEntry & {day: string};
export type LifeGoal = {id: string; text: string; start_date: string; target_date: string;
  status: 'active' | 'completed' | 'paused'; completed_date?: string};
export const goalStatus = {active: '进行中', completed: '已完成', paused: '暂时搁置'};
type Branch = {id: string; kind: 'root' | 'goal' | 'event' | 'ordinary'; label: string;
  goal?: LifeGoal; event?: LifeEvent; count?: number; children?: Branch[]};
type LifeNodeData = Branch & {onToggle?: () => void; collapsed?: boolean};
type LifeNode = Node<LifeNodeData>;

function LifeNodeView({data}: NodeProps<LifeNode>) {
  return <div className={`life-tree-node ${data.kind} ${data.goal?.status ?? ''}`} title={data.label}>
    {[Position.Top, Position.Right, Position.Bottom, Position.Left].map(position =>
      <span key={position}><Handle type="target" position={position} id={'in-' + position}/>
        <Handle type="source" position={position} id={'out-' + position}/></span>)}
    {data.kind === 'root' ? <><span className="life-tree-monogram">m</span><strong>Miko</strong></> : <>
      <small>{data.event?.day ?? (data.goal ? goalStatus[data.goal.status] : '不必有目标的日常')}</small>
      <div className="life-tree-label">{data.label}</div>
      {data.goal && <small>预计 {data.goal.target_date}</small>}
      {data.onToggle && !!data.count && <button className="nodrag nopan" aria-label={`${data.collapsed ? '展开' : '收起'}${data.label}`}
        onClick={event => {event.stopPropagation(); data.onToggle?.();}}>
        {data.collapsed ? '+' : '−'} {data.count} 件小事</button>}
    </>}
  </div>;
}
const nodeTypes = {life: LifeNodeView};
const side = (dx: number, dy: number) => Math.abs(dx) > Math.abs(dy)
  ? dx > 0 ? Position.Right : Position.Left : dy > 0 ? Position.Bottom : Position.Top;
const opposite = {[Position.Top]: Position.Bottom, [Position.Right]: Position.Left,
  [Position.Bottom]: Position.Top, [Position.Left]: Position.Right};

export function LifeTree({goals, events, onGoal, onDay}: {goals: LifeGoal[]; events: LifeEvent[];
  onGoal: (goal: LifeGoal) => void; onDay: (day: string) => void}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [allEvents, setAllEvents] = useState(false);
  const graph = useMemo(() => {
    const children: Branch[] = goals.map(goal => ({id: goal.id, kind: 'goal', label: goal.text, goal}));
    const ordinary = events.filter(e => !e.goal_id);
    if (ordinary.length) children.push({id: 'ordinary', kind: 'ordinary', label: '日常片段'});
    for (const branch of children) {
      const rows = branch.kind === 'ordinary' ? ordinary : events.filter(e => e.goal_id === branch.id);
      branch.count = rows.length;
      if (!collapsed.has(branch.id)) branch.children = (allEvents ? rows : rows.slice(0, 6)).map((event, i) =>
        ({id: `${branch.id}:${event.day}:${i}`, kind: 'event', label: event.text, event}));
    }
    const root = hierarchy<Branch>({id: 'miko', kind: 'root', label: 'Miko', children});
    const laidOut = tree<Branch>().size([Math.PI * 2, 1]).separation((a, b) => a.parent === b.parent ? 1 : 1.4)(root);
    const radius = Math.max(380, laidOut.leaves().length * 26);
    const nodes: LifeNode[] = laidOut.descendants().map(n => {
      const r = n.depth === 0 ? 0 : n.depth === 1 ? radius * .5 : radius;
      const toggle = n.depth === 1 ? () => setCollapsed(previous => {
        const next = new Set(previous); next.has(n.data.id) ? next.delete(n.data.id) : next.add(n.data.id); return next;
      }) : undefined;
      return {id: n.data.id, type: 'life', position: {x: Math.sin(n.x) * r, y: -Math.cos(n.x) * r},
        origin: [.5, .5], data: {...n.data, onToggle: toggle, collapsed: collapsed.has(n.data.id)}};
    });
    const byId = new Map(nodes.map(n => [n.id, n]));
    const edges = laidOut.links().map(link => {
      const source = byId.get(link.source.data.id)!;
      const target = byId.get(link.target.data.id)!;
      const position = side(target.position.x - source.position.x, target.position.y - source.position.y);
      return {id: source.id + '/' + target.id, source: source.id, target: target.id,
        sourceHandle: 'out-' + position, targetHandle: 'in-' + opposite[position], type: 'straight',
        style: {stroke: '#c9b9a6', strokeWidth: target.data.kind === 'goal' ? 2 : 1.2}};
    });
    return {nodes, edges};
  }, [goals, events, collapsed, allEvents]);
  return <section className="life-tree" aria-label="Miko 生活轨迹树">
    <div className="life-tree-toolbar"><span><i/> 进行中 <i className="done"/> 已完成 <i className="paused"/> 搁置</span>
      <button className="life-button" onClick={() => setAllEvents(!allEvents)}>
        {allEvents ? '只看近期小事' : '展开全部小事'}</button></div>
    <ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} fitView
      fitViewOptions={{padding: .15}} minZoom={.15} maxZoom={1.8} nodesDraggable={false}
      nodesConnectable={false} elementsSelectable={false} colorMode="light"
      onNodeClick={(_, node) => {const data = node.data as LifeNodeData;
        if (data.goal) onGoal(data.goal); else if (data.event) onDay(data.event.day);}}
      proOptions={{hideAttribution: true}}>
      <Background color="#d9d0c3" gap={24} size={1}/><Controls showInteractive={false}/>
    </ReactFlow>
    <div className="life-tree-caption">大节点是想做的事，小节点是每天的片段 · 可拖动画布、缩放、点击查看</div>
  </section>;
}
