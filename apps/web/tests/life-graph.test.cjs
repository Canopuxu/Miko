const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const ts = require('typescript');

function load(file) {
  const compiled = ts.transpileModule(readFileSync(path.join(__dirname, '../src', file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  vm.runInNewContext(compiled.outputText, { module, exports: module.exports });
  return module.exports;
}
const { buildLifeGraph } = load('components/lifeGraph.ts');
const { mockAt } = load('previews/lifeMock.ts');
const full = mockAt('2026-10-09');
const graph = buildLifeGraph(full.goals, full.events, 'Miko', '2026-10-09');

test('the dense mock preserves every event and places all connections forward in time', () => {
  assert.ok(full.events.length >= 60);
  assert.equal(graph.nodes.filter(node => node.data.kind === 'activity').length, full.events.length);
  assert.equal(graph.nodes.filter(node => node.data.kind === 'goal').length, full.goals.length);
  assert.equal(new Set(graph.nodes.map(node => node.id)).size, graph.nodes.length);
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  for (const edge of graph.edges) {
    assert.ok(nodes.get(edge.source), `missing source ${edge.source}`);
    assert.ok(nodes.get(edge.target).position.y > nodes.get(edge.source).position.y||edge.source==='header:hub', edge.id);
  }
});

test('milestones follow progress, stay muted while pending, and do not light up just because due dates pass', () => {
  for (const goal of full.goals) {
    const endpoint = graph.nodes.find(node => node.id === 'goal:' + goal.id);
    const before = graph.nodes.filter(node => node.data.event?.goal_id === goal.id && node.data.event.day <= (goal.completed_date ?? '9999'));
    assert.ok(before.every(node => node.position.y < endpoint.position.y), goal.id);
    assert.equal(graph.edges.find(edge => edge.target === endpoint.id).className.includes('planned'), goal.status !== 'completed');
  }
  const overdue = buildLifeGraph([{id:'late',text:'尚未做完',start_date:'2026-10-01',target_date:'2026-10-02',status:'active'}],
    [{day:'2026-10-09',text:'今天继续',goal_id:'late'}], 'Miko', '2026-10-09');
  assert.equal(overdue.nodes.find(node => node.id === 'goal:late').data.goal.status, 'active');
  assert.ok(overdue.nodes.find(node => node.id === 'goal:late').position.y > overdue.nodes.find(node => node.data.kind === 'activity').position.y);
});

test('the top keeps its rail without start nodes, and later goals grow from a later junction', () => {
  assert.ok(graph.nodes.every(node=>node.data.kind!=='start'));
  const nodes=new Map(graph.nodes.map(node=>[node.id,node]));
  const hub=nodes.get('header:hub');
  assert.equal(graph.edges.find(edge=>edge.source==='root').target,hub.id);
  assert.ok(graph.edges.filter(edge=>edge.source===hub.id).every(edge=>nodes.get(edge.target).position.y===hub.position.y));
  for (const id of ['drawing','cooking','photo','plant']) {
    const first=graph.nodes.find(node=>node.data.event?.goal_id===id);
    const edge=graph.edges.find(edge=>edge.target===first.id);
    const origin=graph.edges.find(other=>other.target===edge.source);
    assert.equal(nodes.get(origin.source).data.kind,'fork');
    assert.equal(edge.type,'straight');
    assert.notEqual(nodes.get(origin.source).position.x,first.position.x);
  }
  assert.ok(full.goals.every(goal => !('parent_goal_id' in goal)));
  const early = mockAt('2026-10-02');
  assert.equal(early.goals.length, 4);
  assert.equal(early.goals.filter(goal => goal.status === 'completed').length, 0);
  assert.equal(full.goals.filter(goal => goal.status === 'completed').length, 4);
  const activeOn=day=>mockAt(day).goals.filter(goal=>goal.status==='active'||goal.status==='paused').length;
  assert.equal(activeOn('2026-10-03'),3);
  assert.equal(activeOn('2026-10-06'),5);
});

test('progress stays in the restored fixed branch columns',()=>{
  for(const goal of full.goals){
    const points=graph.nodes.filter(node=>node.data.branch===goal.id&&node.data.kind!=='date');
    assert.ok(points.every(node=>node.position.x===points[0].position.x));
  }
});

test('later births and completions preserve the positions of earlier progress',()=>{
  const early=mockAt('2026-10-02');
  const before=buildLifeGraph(early.goals,early.events,'Miko','2026-10-02');
  for(const node of before.nodes.filter(node=>node.data.event?.goal_id)){
    const after=graph.nodes.find(other=>other.id===node.id);
    assert.equal(after.position.x,node.position.x,node.id);
    assert.equal(after.position.y,node.position.y,node.id);
  }
});

test('straight trunks and new forks do not cross each other in the dense mock',()=>{
  const nodes=new Map(graph.nodes.map(node=>[node.id,node]));
  const lines=graph.edges.filter(edge=>edge.type==='straight').map(edge=>({edge,
    a:{x:nodes.get(edge.source).position.x+14,y:nodes.get(edge.source).position.y+18},
    b:{x:nodes.get(edge.target).position.x+14,y:nodes.get(edge.target).position.y+18}}));
  const side=(a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
  for(let i=0;i<lines.length;i++)for(let j=i+1;j<lines.length;j++){
    const a=lines[i],b=lines[j];
    const crosses=side(a.a,a.b,b.a)*side(a.a,a.b,b.b)<-.01&&side(b.a,b.b,a.a)*side(b.a,b.b,a.b)<-.01;
    assert.equal(crosses,false,`${a.edge.id} crosses ${b.edge.id}`);
  }
});

test('the dense map has no overlapping content blocks, and date/search filters remain connected', () => {
  const blocks = graph.nodes.filter(node => ['activity','goal'].includes(node.data.kind));
  for (let i=0;i<blocks.length;i++)for(let j=i+1;j<blocks.length;j++) {
    const a=blocks[i],b=blocks[j];
    const overlap=a.position.x < b.position.x+b.width && b.position.x < a.position.x+a.width &&
      a.position.y < b.position.y+b.height && b.position.y < a.position.y+a.height;
    assert.equal(overlap,false,`${a.id} overlaps ${b.id}`);
  }
  const filtered=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','','薄荷');
  assert.ok(filtered.nodes.some(node => node.id==='goal:plant'));
  const daily=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','2026-10-05');
  assert.ok(daily.nodes.filter(node => node.data.event).every(node => node.data.event.day==='2026-10-05'));
});

test('unlinked daily events fill free goal columns in reading order without becoming goal progress',()=>{
  const goals=['a','b','c','d'].map(id=>({id,text:id,start_date:'2026-10-01',target_date:'2026-10-10',status:'active'}));
  const progress={day:'2026-10-01',text:'目标进度',goal_id:'b'};
  const daily=Array.from({length:50},(_,index)=>({day:'2026-10-01',text:'日常 '+index}));
  const input=JSON.stringify([goals,daily]);
  const result=buildLifeGraph(goals,[progress,...daily],'Miko','2026-10-01');
  const ordinary=result.nodes.filter(node=>node.data.event&&!node.data.event.goal_id);
  const ordered=[...ordinary].sort((a,b)=>a.position.y-b.position.y||a.position.x-b.position.x);
  assert.equal(ordered.map(node=>node.data.event.text).join(','),daily.map(event=>event.text).join(','));
  assert.equal(new Set(ordinary.map(node=>node.position.x)).size,4);
  assert.equal(ordinary.length,50);
  assert.ok(ordinary.every(node=>node.data.branch==='ordinary'));
  assert.equal(result.width,buildLifeGraph(goals,[progress],'Miko','2026-10-01').width);
  assert.equal(JSON.stringify([goals,daily]),input);
  const blocks=result.nodes.filter(node=>['activity','goal'].includes(node.data.kind));
  for(let i=0;i<blocks.length;i++)for(let j=i+1;j<blocks.length;j++){
    const a=blocks[i],b=blocks[j];
    assert.ok(a.position.x+a.width<=b.position.x||b.position.x+b.width<=a.position.x||
      a.position.y+a.height<=b.position.y||b.position.y+b.height<=a.position.y,`${a.id} overlaps ${b.id}`);
  }
  const found=buildLifeGraph(goals,[progress,...daily],'Miko','2026-10-01','','日常 49');
  assert.equal(found.nodes.filter(node=>node.data.event).length,1);
  assert.ok(found.edges.some(edge=>edge.target===found.nodes.find(node=>node.data.event).id));
});

test('measured long labels expand the layout without overlapping later rows or milestones',()=>{
  const measured=new Map(graph.nodes.filter(node=>['activity','goal'].includes(node.data.kind))
    .map((node,index)=>[node.id,54+(index%4)*38]));
  const expanded=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','','',measured);
  const blocks=expanded.nodes.filter(node=>['activity','goal'].includes(node.data.kind));
  assert.ok(blocks.some(node=>node.height>150));
  for(let i=0;i<blocks.length;i++)for(let j=i+1;j<blocks.length;j++){
    const a=blocks[i],b=blocks[j];
    assert.ok(a.position.x+a.width<=b.position.x||b.position.x+b.width<=a.position.x||
      a.position.y+a.height<=b.position.y||b.position.y+b.height<=a.position.y,`${a.id} overlaps ${b.id}`);
  }
});
