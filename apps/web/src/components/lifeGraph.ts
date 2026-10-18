import type { Edge, Node } from '@xyflow/react';

export type LifeEntry = {text:string; goal_id?:string; result?:string; order?:number};
export type LifeEvent = LifeEntry & {day:string};
export type LifeGoal = {id:string; text:string; start_date:string; target_date:string;
  status:'active'|'completed'|'paused'; completed_date?:string; done_when?:string; current_state?:string};
export const goalStatus = {active:'进行中', completed:'已完成', paused:'暂时搁置'};
export type LifeData = {kind:'root'|'goal'|'activity'|'date'|'fork'|'bend'; label:string; branch?:string;
  goal?:LifeGoal; event?:LifeEvent; day?:string; active?:boolean; choose?:()=>void};
export type LifeNode = Node<LifeData>;
export const ordinary='ordinary';
const laneWidth=260, left=82, nodeWidth=234;

// Goal progress owns a continuous track. Independent activities borrow the
// day's free cells in input order and keep their independent identity. A finished
// goal closes its track; later capacity forks from a nearby surviving trunk.
export function buildLifeGraph(goals:LifeGoal[],events:LifeEvent[],name:string,asOf:string,day='',query='',heights:ReadonlyMap<string,number>=new Map(),availableWidth=Infinity) {
  const byId=new Map(goals.map(goal=>[goal.id,goal]));
  type Entry={event:LifeEvent;id:string};
  const eventDays=new Map<string,Entry[]>();
  const ordinals=new Map<string,number>();
  for(const event of events){
    const rows=eventDays.get(event.day)??[];
    const ordinal=ordinals.get(event.day)??0;ordinals.set(event.day,ordinal+1);
    const previous=event.goal_id&&byId.has(event.goal_id)?rows.find(row=>row.event.goal_id===event.goal_id):undefined;
    if(previous)previous.event={...previous.event,text:previous.event.text+'；'+event.text};
    else rows.push({event:{...event},id:`activity:${event.day}:${ordinal}`});
    eventDays.set(event.day,rows);
  }
  const search=query.trim().toLocaleLowerCase();
  const visible=new Map<string,Entry[]>([...eventDays].map(([date,rows]):[string,Entry[]]=>[date,rows.filter(({event})=>(!day||date===day)&&(!search||
    (event.text+' '+(byId.get(event.goal_id??'')?.text??'')).toLocaleLowerCase().includes(search)))]).filter(([,rows])=>rows.length));
  const related=new Set([...visible.values()].flat().map(({event})=>event.goal_id));
  const milestones=goals.map(goal=>{
    const last=events.filter(event=>event.goal_id===goal.id).reduce((date,event)=>event.day>date?event.day:date,goal.start_date);
    const end=goal.status==='completed'?goal.completed_date??last:[goal.start_date,goal.target_date,last,asOf].sort().at(-1)!;
    return {goal,end};
  }).filter(({goal,end})=>(!day&&!search)||related.has(goal.id)||
    (!search&&(goal.start_date===day||end===day))||(!day&&goal.text.toLocaleLowerCase().includes(search)));
  const earliest=[...visible.keys(),...milestones.map(({goal})=>goal.start_date)].sort()[0];
  const dates=[...new Set([...visible.keys(),...milestones.flatMap(item=>[item.goal.start_date,item.end])])].sort();
  const firstEntries=visible.get(earliest)??[];
  const firstEnds=milestones.filter(item=>item.end===earliest&&!firstEntries.some(row=>row.event.goal_id===item.goal.id));
  const initial=Math.max(firstEntries.length+firstEnds.length,milestones.filter(({goal})=>goal.start_date===earliest).length,1);
  const nodes:LifeNode[]=[],edges:Edge[]=[];
  function add(id:string,data:LifeData,x:number,y:number,width=nodeWidth,height=74){
    nodes.push({id,type:'life',position:{x,y},width,height,style:{width,height},focusable:false,data});
  }
  function connect(source:string,target:string,branch='',planned=false){
    edges.push({id:source+'/'+target,source,target,sourceHandle:source==='root'?undefined:'out',targetHandle:'in',type:'straight',
      className:'life-branch-edge'+(planned?' planned':''),data:{branch}});
  }
  type Track={slot:number;tail:string;closed:boolean;goal?:string};
  const tracks:Track[]=[];
  const x=(slot:number)=>left+slot*laneWidth;
  const center=x(0)+(initial-1)*laneWidth/2;
  add('root',{kind:'root',label:name},center+14-40,8,80,36);
  add('header:hub',{kind:'bend',label:''},center,70,28,36);connect('root','header:hub');
  for(let slot=0;slot<initial;slot++){
    const id='header:'+slot;add(id,{kind:'bend',label:''},x(slot),70,28,36);
    connect('header:hub',id);tracks.push({slot,tail:id,closed:false});
  }
  let y=128;
  for(const date of dates){
    const entries=visible.get(date)??[];
    const ends=milestones.filter(item=>item.end===date);
    const prior=tracks.map(track=>({...track}));
    let open=tracks.filter(track=>!track.closed);
    const added:Track[]=[];
    function reopen(){
      const reuse=tracks.find(track=>track.closed);
      const track=reuse??{slot:tracks.length,tail:'',closed:false};
      track.closed=false;track.goal=undefined;
      if(!reuse)tracks.push(track);added.push(track);
      return track;
    }
    const births=milestones.filter(item=>item.goal.start_date===date);
    for(const {goal} of births){
      const preferred=entries.findIndex(({event})=>event.goal_id===goal.id);
      const free=tracks.filter(track=>!track.closed&&!track.goal);
      const track=(date===earliest?free.find(track=>track.slot===preferred):undefined)??
        (tracks.some(track=>track.closed)?reopen():free[0]??reopen());
      track.goal=goal.id;
    }
    const extraEnds=ends.filter(item=>!entries.some(row=>row.event.goal_id===item.goal.id)).length;
    const endingIds=new Set(ends.map(item=>item.goal.id));
    const live=tracks.filter(track=>!track.closed);
    // Keep a living side trunk before the last goal ends if the history continues.
    const continuation=date!==dates.at(-1)&&live.every(track=>track.goal&&endingIds.has(track.goal))?1:0;
    while(open.length+added.length<Math.max(entries.length+extraEnds+continuation,ends.length+continuation))reopen();
    // Adjacent surviving trunks provide new forks. If every trunk has ended,
    // continue from the nearest endpoint rather than returning to the header.
    const parents=new Map<number,string>();
    for(const track of added){
      const candidates=open.length?open:prior;
      const parent=[...candidates].sort((a,b)=>Math.abs(a.slot-track.slot)-Math.abs(b.slot-track.slot))[0];
      if(!parents.has(parent.slot)){
        const id=`fork:${date}:${parent.slot}`;
        add(id,{kind:'fork',label:'',day:date},x(parent.slot),y,28,36);connect(parent.tail,id);
        const live=open.find(other=>other.slot===parent.slot);if(live)live.tail=id;
        parents.set(parent.slot,id);
      }
      const id=`branch:${date}:${track.slot}`;
      add(id,{kind:'bend',label:'',day:date},x(track.slot),y+48,28,36);
      connect(parents.get(parent.slot)!,id);track.tail=id;
    }
    open=[...open,...added].sort((a,b)=>a.slot-b.slot);
    const rowY=y+(added.length?96:0);
    add('date:'+date,{kind:'date',label:date},0,rowY+3,52,46);
    const rows=new Map<number,Entry>();
    const daily:Entry[]=[];
    for(const entry of entries){
      const track=open.find(track=>track.goal===entry.event.goal_id&&!!track.goal);
      if(track)rows.set(track.slot,entry);else daily.push(entry);
    }
    // Reserve goal progress first, then fill the remaining cells left to right.
    for(const entry of daily){
      const track=open.find(track=>!rows.has(track.slot)&&!endingIds.has(track.goal??''))!;
      rows.set(track.slot,entry);
    }
    let bottom=rowY+46;
    for(const track of open){
      const placed=rows.get(track.slot);
      const ending=ends.find(item=>item.goal.id===track.goal);
      if(ending){
        const {goal}=ending,id='goal:'+goal.id,height=heights.get(id)??94;
        add(id,{kind:'goal',label:goal.text,branch:goal.id,goal,day:date,event:placed?.event},x(track.slot),rowY,nodeWidth,height);
        connect(track.tail,id,goal.id,goal.status!=='completed');track.tail=id;track.closed=true;
        bottom=Math.max(bottom,rowY+height);
      }else if(placed){
        const {event,id}=placed;
        const branch=event.goal_id&&byId.has(event.goal_id)?event.goal_id:ordinary;
        const height=heights.get(id)??74;
        add(id,{kind:'activity',label:event.text,branch,event},x(track.slot),rowY,nodeWidth,height);
        connect(track.tail,id,track.goal??ordinary);track.tail=id;
        bottom=Math.max(bottom,rowY+height);
      }
    }
    y=bottom+24;
  }
  // Geometry adapts after the ordered graph is complete. Narrower cards wrap
  // naturally; neither track count nor connectivity depends on the viewport.
  const column=Math.max(144,Math.min(laneWidth,(availableWidth-left-32)/initial));
  for(const node of nodes){
    if(node.data.kind==='date')continue;
    if(node.id==='root')node.position.x=left+(initial-1)*column/2+14-40;
    else node.position.x=left+(node.position.x-left)*column/laneWidth;
    if(node.data.kind==='activity'||node.data.kind==='goal'){
      node.width=column-26;node.style={...node.style,width:node.width};
    }
  }
  return {nodes,edges,left:0,startWidth:Math.max(400,left+initial*column),width:Math.max(400,left+tracks.length*column),height:y+24,empty:!visible.size&&!milestones.length};
}
