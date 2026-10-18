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
const { buildLifeGraph, monthLifeGraph } = load('components/lifeGraph.ts');
const { mockAt } = load('previews/lifeMock.ts');
const full = mockAt('2026-10-09');
const graph = buildLifeGraph(full.goals, full.events, 'Miko', '2026-10-09');

test('monthly display keeps cross-month progress and completion without repeating other months',()=>{
  const goal={id:'cake',text:'做蛋糕',start_date:'2026-09-29',target_date:'2026-10-02',completed_date:'2026-10-02',status:'completed'};
  const events=[{day:'2026-09-29',text:'试配方',goal_id:'cake'},{day:'2026-09-29',text:'散步'},
    {day:'2026-10-01',text:'调整面糊',goal_id:'cake'},{day:'2026-10-01',text:'买菜'},
    {day:'2026-10-02',text:'烤好了',goal_id:'cake'},{day:'2026-10-02',text:'看书'},
    {day:'2026-10-03',text:'收拾房间'},{day:'2026-10-03',text:'午睡'}];
  const history=buildLifeGraph([goal],events,'Miko','2026-10-03');
  const october=monthLifeGraph(history,'2026-10',620);
  const september=monthLifeGraph(history,'2026-09',620);
  assert.equal(october.nodes.filter(n=>n.data.kind==='goal').length,1);
  assert.equal(september.nodes.filter(n=>n.data.kind==='goal').length,0);
  assert.ok(october.lines.some(line=>line.id.startsWith('month-stem:')));
  assert.ok(october.nodes.some(n=>n.data.kind==='fork'));
  assert.ok(!october.lines.some(line=>line.id.startsWith('goal:cake/')));
  assert.ok(october.nodes.every(n=>!(n.data.event?.day??n.data.day??n.data.label).startsWith('2026-09')));
});

test('monthly columns use the available width including later branches',()=>{
  for(const width of [620,960]){
    const month=monthLifeGraph(graph,'2026-10',width);
    assert.ok(month.nodes.every(n=>n.position.x>=0&&n.position.x+n.width<=width));
    assert.ok(month.lines.every(line=>line.x1>=0&&line.x1<=width&&line.x2>=0&&line.x2<=width));
  }
  assert.equal(monthLifeGraph(graph,'2025-01',620).empty,true);
});
const activities=graph=>graph.nodes.filter(node=>node.data.event);
const reading=(graph,date)=>activities(graph).filter(node=>node.data.event.day===date)
  .sort((a,b)=>a.position.y-b.position.y||a.position.x-b.position.x);
function connected(graph){
  const nodes=new Map(graph.nodes.map(node=>[node.id,node]));
  const incoming=new Map(graph.edges.map(edge=>[edge.target,edge.source]));
  for(const node of graph.nodes.filter(node=>node.data.kind!=='date')){
    let id=node.id;const seen=new Set();
    while(id!=='root'){
      assert.ok(!seen.has(id),'cycle at '+id);seen.add(id);
      assert.ok(incoming.has(id),'disconnected '+id);id=incoming.get(id);
      assert.ok(nodes.has(id),'missing '+id);
    }
  }
}
function noOverlaps(graph){
  const blocks=graph.nodes.filter(node=>['activity','goal'].includes(node.data.kind));
  for(let i=0;i<blocks.length;i++)for(let j=i+1;j<blocks.length;j++){
    const a=blocks[i],b=blocks[j];
    assert.ok(a.position.x+a.width<=b.position.x||b.position.x+b.width<=a.position.x||
      a.position.y+a.height<=b.position.y||b.position.y+b.height<=a.position.y,`${a.id} overlaps ${b.id}`);
  }
}

test('the complete history preserves every record and continuous goal tracks from the header',()=>{
  assert.ok(full.events.length>=60);
  for(const event of full.events)assert.ok(activities(graph).some(node=>node.data.event.day===event.day&&node.data.event.text.includes(event.text)),event.text);
  assert.equal(graph.nodes.filter(node=>node.data.kind==='goal').length,full.goals.length);
  assert.equal(new Set(graph.nodes.map(node=>node.id)).size,graph.nodes.length);
  for(const goal of full.goals){
    const progress=activities(graph).filter(node=>node.data.event.goal_id===goal.id);
    assert.equal(new Set(progress.map(node=>node.position.x)).size,1,goal.id);
    assert.equal(graph.nodes.find(node=>node.id==='goal:'+goal.id).position.x,progress[0].position.x);
  }
  connected(graph);noOverlaps(graph);
});

test('four first-day records create four trunks even with zero or one goal',()=>{
  const goal={id:'cake',text:'做蛋糕',start_date:'2026-10-12',target_date:'2026-10-20',status:'active'};
  const entries=['上午还书','中午做蛋糕','下午闲聊','睡前游戏'].map((text,index)=>({day:'2026-10-12',text,...(index===1?{goal_id:'cake'}:{})}));
  const original=JSON.stringify(entries);
  for(const goals of [[],[goal]]){
    const value=buildLifeGraph(goals,entries,'Miko','2026-10-12');
    const nodes=reading(value,'2026-10-12');
    assert.equal(value.edges.filter(edge=>edge.source==='header:hub').length,4);
    assert.equal(new Set(nodes.map(node=>node.position.x)).size,4);
    assert.equal(new Set(nodes.map(node=>node.position.y)).size,1);
    assert.equal(nodes.map(node=>node.data.label).join('|'),entries.map(entry=>entry.text).join('|'));
    connected(value);
  }
  assert.equal(JSON.stringify(entries),original);
});

test('sparse days remain connected and additional records fork locally without moving older days',()=>{
  const events=[4,2,5,3].flatMap((count,day)=>Array.from({length:count},(_,i)=>({day:`2026-10-${12+day}`,text:`${day}:${i}`})));
  const early=buildLifeGraph([],events.filter(event=>event.day<'2026-10-14'),'Miko','2026-10-13');
  const later=buildLifeGraph([],events,'Miko','2026-10-15');
  connected(later);
  for(const before of activities(early)){
    const after=later.nodes.find(node=>node.id===before.id);
    assert.deepEqual(after.position,before.position);
  }
  const compact=buildLifeGraph([],events.filter(event=>event.day<'2026-10-14'),'Miko','2026-10-13','','',new Map(),900);
  const extended=buildLifeGraph([],events,'Miko','2026-10-15','','',new Map(),900);
  for(const before of activities(compact)){
    const after=extended.nodes.find(node=>node.id===before.id);
    assert.deepEqual(after.position,before.position);assert.equal(after.width,before.width);
  }
  assert.equal(compact.startWidth,extended.startWidth);
  const fifth=reading(later,'2026-10-14')[4];
  const join=later.nodes.find(node=>node.id===later.edges.find(edge=>edge.target===fifth.id).source);
  const fork=later.nodes.find(node=>node.id===later.edges.find(edge=>edge.target===join.id).source);
  assert.equal(fork.data.kind,'fork');assert.equal(fork.data.day,'2026-10-14');
  assert.ok(fork.position.y>reading(later,'2026-10-13')[0].position.y);
  for(const date of new Set(events.map(event=>event.day)))
    assert.equal(reading(later,date).map(node=>node.data.label).join('|'),events.filter(event=>event.day===date).map(event=>event.text).join('|'));
});

test('goal progress keeps its track while independent records fill the free cells in input order',()=>{
  const goals=['a','b','c'].map((id,i)=>({id,text:id,start_date:`2026-10-${12+i}`,target_date:'2026-10-20',status:'active'}));
  const events=Array.from({length:4},(_,day)=>Array.from({length:4},(_,i)=>({day:`2026-10-${12+day}`,text:`${day}:${i}`,goal_id:i===1?goals[day%3].id:undefined}))).flat();
  const value=buildLifeGraph(goals,events,'Miko','2026-10-15');
  for(const date of new Set(events.map(event=>event.day))){
    const nodes=reading(value,date).filter(node=>!node.data.event.goal_id);
    assert.equal(nodes.map(node=>node.data.label).join('|'),events.filter(event=>event.day===date&&!event.goal_id).map(event=>event.text).join('|'));
  }
  for(const goal of goals){
    const nodes=activities(value).filter(node=>node.data.event.goal_id===goal.id);
    assert.equal(new Set(nodes.map(node=>node.position.x)).size,1);
    assert.equal(value.nodes.find(node=>node.id==='goal:'+goal.id).position.x,nodes[0].position.x);
  }
  assert.ok(activities(value).every(node=>node.data.event.goal_id===undefined||node.data.branch===node.data.event.goal_id));
});

test('milestones stay after progress, remain muted until completed and closed trunks branch again near new activity',()=>{
  for(const goal of full.goals){
    const endpoint=graph.nodes.find(node=>node.id==='goal:'+goal.id);
    const before=activities(graph).filter(node=>node.data.event.goal_id===goal.id&&node.data.event.day<=(goal.completed_date??'9999'));
    assert.ok(before.every(node=>node.position.y<=endpoint.position.y),goal.id);
    assert.equal(graph.edges.find(edge=>edge.target===endpoint.id).className.includes('planned'),goal.status!=='completed');
  }
  const goal={id:'one',text:'完成一件事',start_date:'2026-10-01',target_date:'2026-10-02',completed_date:'2026-10-01',status:'completed'};
  const value=buildLifeGraph([goal],[{day:'2026-10-01',text:'做完',goal_id:'one'},
    {day:'2026-10-03',text:'新的日常'},{day:'2026-10-03',text:'另一件日常'}],'Miko','2026-10-03');
  connected(value);
  const endpoint=value.nodes.find(node=>node.id==='goal:one');
  const following=value.nodes.find(node=>node.data.event?.day==='2026-10-03');
  assert.ok(following.position.y>endpoint.position.y);
  assert.ok(value.nodes.some(node=>node.data.kind==='fork'&&node.data.day==='2026-10-03'));
  const ongoing={id:'ongoing',text:'另一件事',start_date:'2026-10-01',target_date:'2026-10-08',status:'active'};
  const next={id:'next',text:'后来开始的事',start_date:'2026-10-03',target_date:'2026-10-10',status:'active'};
  const continued=buildLifeGraph([goal,ongoing,next],[
    {day:'2026-10-01',text:'做完',goal_id:'one'},
    {day:'2026-10-01',text:'开始另一件事',goal_id:'ongoing'},
    {day:'2026-10-02',text:'普通日常'},
    {day:'2026-10-03',text:'新目标开工',goal_id:'next'},
    {day:'2026-10-03',text:'继续另一件事',goal_id:'ongoing'},
  ],'Miko','2026-10-03');
  connected(continued);noOverlaps(continued);
  const old=continued.nodes.find(node=>node.id==='goal:one');
  const fresh=activities(continued).find(node=>node.data.event.goal_id==='next');
  assert.equal(fresh.position.x,old.position.x);
  assert.ok(fresh.position.y>old.position.y);
  assert.ok(!continued.edges.some(edge=>edge.source===old.id),'a surviving trunk supplies the new branch');
  assert.ok(continued.nodes.some(node=>node.data.kind==='fork'&&node.data.day==='2026-10-03'));
});

test('four daily entries and two goals occupy one row; completion replaces its progress and old duplicates retain their text',()=>{
  const goals=[{id:'a',text:'蛋糕做完',start_date:'2026-10-12',target_date:'2026-10-12',completed_date:'2026-10-12',status:'completed'},
    {id:'b',text:'读完一本书',start_date:'2026-10-12',target_date:'2026-10-20',status:'active'}];
  const events=[{day:'2026-10-12',text:'烤好蛋糕',goal_id:'a'},
    {day:'2026-10-12',text:'旧记录里的收尾',goal_id:'a'},
    {day:'2026-10-12',text:'看了下一章',goal_id:'b'},
    ...['上课','吃饭','散步','闲聊'].map(text=>({day:'2026-10-12',text}))];
  const value=buildLifeGraph(goals,events,'Miko','2026-10-12');
  const row=reading(value,'2026-10-12');
  assert.equal(row.length,6);assert.equal(new Set(row.map(node=>node.position.y)).size,1);
  assert.equal(new Set(row.map(node=>node.position.x)).size,6);
  const completed=row.find(node=>node.data.goal?.id==='a');
  assert.ok(completed.data.event.text.includes('烤好蛋糕'));
  assert.ok(completed.data.event.text.includes('旧记录里的收尾'));
  assert.equal(row.filter(node=>node.data.event.goal_id==='a').length,1);
  assert.ok(!value.edges.some(edge=>edge.source===completed.id));
  connected(value);noOverlaps(value);
});

test('straight tracks and local forks do not cross or run backwards',()=>{
  const nodes=new Map(graph.nodes.map(node=>[node.id,node]));
  const lines=graph.edges.map(edge=>({edge,
    a:{x:nodes.get(edge.source).position.x+14,y:nodes.get(edge.source).position.y+18},
    b:{x:nodes.get(edge.target).position.x+14,y:nodes.get(edge.target).position.y+18}}));
  const side=(a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
  for(const line of lines)assert.ok(line.b.y>line.a.y||line.edge.source==='header:hub',line.edge.id);
  for(let i=0;i<lines.length;i++)for(let j=i+1;j<lines.length;j++){
    const a=lines[i],b=lines[j];
    assert.equal(side(a.a,a.b,b.a)*side(a.a,a.b,b.b)<-.01&&side(b.a,b.b,a.a)*side(b.a,b.b,a.b)<-.01,false,`${a.edge.id} crosses ${b.edge.id}`);
  }
});

test('date and text filtering retain the selected records and connected goal context',()=>{
  const filtered=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','','薄荷');
  assert.ok(filtered.nodes.some(node=>node.id==='goal:plant'));connected(filtered);
  const daily=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','2026-10-05');
  assert.ok(activities(daily).every(node=>node.data.event.day==='2026-10-05'));connected(daily);
});

test('measured long text expands the day without overlapping the next day or milestones',()=>{
  const measured=new Map(graph.nodes.filter(node=>['activity','goal'].includes(node.data.kind)).map((node,i)=>[node.id,90+(i%4)*38]));
  const expanded=buildLifeGraph(full.goals,full.events,'Miko','2026-10-09','','',measured);
  assert.ok(expanded.nodes.some(node=>node.height>180));noOverlaps(expanded);connected(expanded);
});
