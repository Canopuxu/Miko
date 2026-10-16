import type { Edge, Node } from '@xyflow/react';

export type LifeEntry = {text:string; goal_id?:string};
export type LifeEvent = LifeEntry & {day:string};
export type LifeGoal = {id:string; text:string; start_date:string; target_date:string;
  status:'active'|'completed'|'paused'; completed_date?:string};
export const goalStatus = {active:'进行中', completed:'已完成', paused:'暂时搁置'};
export type LifeData = {kind:'root'|'goal'|'activity'|'date'|'fork'|'bend'; label:string; branch?:string;
  goal?:LifeGoal; event?:LifeEvent; day?:string; active?:boolean; choose?:()=>void};
export type LifeNode = Node<LifeData>;
export const ordinary='ordinary';
const laneWidth=260, left=82, nodeWidth=234;

export function buildLifeGraph(goals:LifeGoal[],events:LifeEvent[],name:string,asOf:string,day='',query='',heights:ReadonlyMap<string,number>=new Map()) {
  const byId=new Map(goals.map(goal=>[goal.id,goal]));
  type Branch={id:string;goal?:LifeGoal;parent?:string;start:string;end?:string;until?:string;events:LifeEvent[];lane:number;x:number};
  const all:Branch[]=[],occupancy:(string|undefined)[]=[];
  for(const goal of [...goals].sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.id.localeCompare(b.id))){
    const rows=events.filter(event=>event.goal_id===goal.id);
    const last=rows.reduce((value,event)=>event.day>value?event.day:value,goal.start_date);
    const end=goal.status==='completed'?goal.completed_date??last:[goal.target_date,last,asOf].sort().at(-1)!;
    let lane=occupancy.findIndex(until=>!!until&&until<goal.start_date);
    if(lane<0)lane=occupancy.length;
    const alive=all.filter(branch=>branch.start<goal.start_date&&(!branch.until||branch.until>=goal.start_date));
    const candidates=alive.length?alive:all.filter(branch=>branch.start<goal.start_date);
    const parent=[...candidates].sort((a,b)=>Math.abs(a.lane-lane)-Math.abs(b.lane-lane)||b.start.localeCompare(a.start))[0];
    const until=goal.status==='completed'?[end,last].sort().at(-1):undefined;
    occupancy[lane]=until;
    all.push({id:goal.id,goal,parent:parent?.id,start:goal.start_date,end,until,events:rows,lane,x:left+lane*laneWidth});
  }
  const daily=events.filter(event=>!event.goal_id||!byId.has(event.goal_id));
  const unplaced:LifeEvent[]=[];
  // Fill each day's free cells in reading order. A borrowed lane is only a
  // position: the original event keeps its independent, unlinked identity.
  for(const currentDay of [...new Set(daily.map(event=>event.day))].sort()){
    const lanes=all.filter(branch=>branch.start<=currentDay&&(!branch.end||branch.end>=currentDay)).sort((a,b)=>a.lane-b.lane);
    const counts=new Map(lanes.map(branch=>[branch.id,branch.events.filter(event=>event.day===currentDay).length]));
    for(const event of daily.filter(event=>event.day===currentDay)){
      const branch=[...lanes].sort((a,b)=>counts.get(a.id)!-counts.get(b.id)!||a.lane-b.lane)[0];
      if(!branch){unplaced.push(event);continue;}
      branch.events.push(event);counts.set(branch.id,counts.get(branch.id)!+1);
    }
  }
  // Without a goal to borrow space from, use the leftmost column.
  if(unplaced.length)all.push({id:ordinary,start:unplaced[0].day,events:unplaced,lane:0,x:left});
  const search=query.trim().toLocaleLowerCase();
  const visible=events.filter(event=>(!day||event.day===day)&&(!search||
    (event.text+' '+(byId.get(event.goal_id??'')?.text??'')).toLocaleLowerCase().includes(search)));
  const included=new Set(all.filter(branch=>(!day&&!search)||branch.events.some(event=>visible.includes(event))||
    (!search&&(branch.start===day||branch.end===day))||(!day&&branch.goal?.text.toLocaleLowerCase().includes(search))).map(branch=>branch.id));
  const byBranch=new Map(all.map(branch=>[branch.id,branch]));
  for(const id of included){let parent=byBranch.get(id)?.parent;while(parent){included.add(parent);parent=byBranch.get(parent)?.parent;}}
  const shown=all.filter(branch=>included.has(branch.id)).map(branch=>({...branch,events:branch.events.filter(event=>visible.includes(event))}));
  const nodes:LifeNode[]=[],edges:Edge[]=[];
  function add(id:string,data:LifeData,x:number,y:number,width=nodeWidth,height=74){
    nodes.push({id,type:'life',position:{x,y},width,height,style:{width,height},focusable:false,data});
  }
  function connect(source:string,target:string,branch:string,planned=false){
    // Standalone days outside the goals' lifetimes do not reopen a finished goal.
    if(branch===ordinary&&goals.length)return;
    edges.push({id:source+'/'+target,source,target,sourceHandle:source==='root'?undefined:'out',targetHandle:'in',type:'straight',
      className:'life-branch-edge'+(planned?' planned':''),data:{branch}});
  }
  const days=[...new Set(shown.flatMap(branch=>[branch.start,...branch.events.map(event=>event.day),...(branch.end?[branch.end]:[])]))].sort();
  const earliest=days[0],initial=shown.filter(branch=>branch.start===earliest);
  const center=initial.length?(initial[0].x+initial[initial.length-1].x)/2:120;
  add('root',{kind:'root',label:name},center+14-40,8,80,36);
  add('header:hub',{kind:'bend',label:''},center,70,28,36);
  connect('root','header:hub','');
  const last=new Map<string,string>(),endpoints=new Map<string,{id:string;y:number}>(),forks=new Map<string,{id:string;y:number}>();
  for(const branch of initial){
    const id='header:'+branch.id;
    add(id,{kind:'bend',label:'',branch:branch.id},branch.x,70,28,36);
    connect('header:hub',id,branch.id);last.set(branch.id,id);
  }
  let y=128;
  for(const currentDay of days){
    const base=y;let bottom=base;
    let rowStart=base;
    const births=shown.filter(branch=>branch.start===currentDay&&!last.has(branch.id));
    // Place new junctions above the day's shared grid, so a later branch does
    // not push its first entry far below all the other entries of that day.
    for(const parent of shown){
      const children=births.filter(child=>child.parent===parent.id);
      if(!children.length||!last.has(parent.id)||(parent.until&&parent.until<currentDay))continue;
      const id='fork:'+parent.id+':'+currentDay;
      add(id,{kind:'fork',label:'',branch:parent.id,day:currentDay},parent.x,base,28,36);
      connect(last.get(parent.id)!,id,parent.id);last.set(parent.id,id);
      for(const child of children)forks.set(child.id,{id,y:base});
      rowStart=Math.max(rowStart,base+64);
    }
    for(const branch of births){
      const join=forks.get(branch.id)??endpoints.get(branch.parent??'');
      const cursor=join?.y===base?base+64:base;
      const id='branch:'+branch.id;
      add(id,{kind:'bend',label:'',branch:branch.id,day:currentDay},branch.x,cursor,28,36);
      connect(join?.id??'root',id,branch.id,!join);last.set(branch.id,id);
      rowStart=Math.max(rowStart,cursor+48);
    }
    bottom=rowStart;
    add('date:'+currentDay,{kind:'date',label:currentDay},0,rowStart+3,52,46);
    const rowsByBranch=new Map(shown.map(branch=>[branch.id,branch.events.filter(event=>event.day===currentDay)]));
    const rowHeights:number[]=[];
    for(const branch of shown)rowsByBranch.get(branch.id)!.forEach((_,index)=>{
      const id='activity:'+branch.id+':'+currentDay+':'+index;
      rowHeights[index]=Math.max(rowHeights[index]??0,heights.get(id)??74);
    });
    const rowOffsets=[rowStart];
    for(const height of rowHeights)rowOffsets.push(rowOffsets.at(-1)!+height+14);
    for(const branch of shown){
      const rows=rowsByBranch.get(branch.id)!;
      const ends=branch.end===currentDay;
      if(!ends&&!rows.length)continue;
      let cursor=rowStart;
      rows.forEach((event,index)=>{
        const independent=!event.goal_id||!byId.has(event.goal_id);
        const id='activity:'+branch.id+':'+currentDay+':'+index;
        add(id,{kind:'activity',label:event.text,branch:independent?ordinary:branch.id,event},branch.x,rowOffsets[index],nodeWidth,heights.get(id)??74);
        connect(last.get(branch.id)??'root',id,branch.id);last.set(branch.id,id);cursor=rowOffsets[index+1];
      });
      if(ends&&branch.goal){
        const id='goal:'+branch.id;
        const height=heights.get(id)??94;
        add(id,{kind:'goal',label:branch.goal.text,branch:branch.id,goal:branch.goal,day:currentDay},branch.x,cursor,nodeWidth,height);
        connect(last.get(branch.id)??'root',id,branch.id,branch.goal.status!=='completed');last.set(branch.id,id);
        endpoints.set(branch.id,{id,y:cursor});cursor+=height+18;
      }
      bottom=Math.max(bottom,cursor);
    }
    y=bottom+24;
  }
  const slots=all.length?Math.max(...all.map(branch=>branch.lane))+1:0;
  return {nodes,edges,left:0,width:Math.max(400,left+Math.max(1,slots)*laneWidth),height:y+24,empty:shown.length===0};
}
