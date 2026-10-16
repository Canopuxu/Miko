import type { LifeGoal, LifeEvent } from '../components/lifeGraph';

const date=(day:number)=>`2026-10-${String(day).padStart(2,'0')}`;
const goal=(id:string,text:string,start:number,target:number,completed?:number,status:LifeGoal['status']='active'):LifeGoal=>({
  id,text,start_date:date(start),target_date:date(target),status:completed?'completed':status,
  ...(completed?{completed_date:date(completed)}:{}),
});
export const mockGoals:LifeGoal[]=[
  goal('piano','完整弹完《River Flows in You》',1,4,3),
  goal('website','完成个人作品集的第一版',1,6,5),
  goal('reading','读完《人间草木》并写一页摘记',1,10,8),
  goal('tennis','连续练习两周网球发球',1,12),
  goal('drawing','画完窗边那组日常速写',4,9,7),
  goal('cooking','学会做番茄炖牛腩',6,14),
  goal('photo','整理一组秋天的街头照片',8,16),
  goal('plant','试试薄荷扦插',6,20,undefined,'paused'),
];
const descriptions:Record<string,string[]>={
  piano:['慢速合了两遍，左手换和弦没那么卡了','把副歌单独练了十分钟','跟着节拍器，把最后一段再走一遍','连起来弹完整首，最后两个音还有点急'],
  website:['把首页留白调大了一点','补了项目页的过程草图','试了一版米白和墨绿的配色','用手机看了一遍，缩短了导航文案','把作品封面换成自己拍的照片'],
  reading:['翻了几页《人间草木》，在喜欢的句子旁画了线','把《昆明的雨》重读了一遍','记下几句关于吃食的描写','合上书，抄了半页摘记'],
  tennis:['练了十几次抛球，先不追求力量','在墙边找了一会儿击球点','发球落点终于没那么飘了','和朋友打了半小时，回来拉伸'],
  drawing:['铅笔画了窗边的杯子和台灯','给速写加了两块浅色','照着傍晚的光调整阴影','把最后一张速写收尾，贴在本子上'],
  cooking:['先记下牛腩焯水和炖煮的顺序','去买了番茄，问了下摊主怎么挑','试了一小锅，盐可以再少一点','等炖煮时把厨房收拾了一遍'],
  photo:['选出几张梧桐树下的照片','把街角那张调成暖一点的色温'],
  plant:['查了薄荷枝条该从哪里剪','先留了两根枝条，等周末再试'],
};
export const mockEvents:LifeEvent[]=[];
for(const item of mockGoals){
  const first=Number(item.start_date.slice(-2));
  const last=item.completed_date?Number(item.completed_date.slice(-2)):9;
  const perDay=['piano','website','reading','drawing','plant'].includes(item.id)?2:1;
  for(let day=first;day<=Math.min(last,9);day++)for(let part=0;part<perDay;part++){
    const texts=descriptions[item.id];
    mockEvents.push({day:date(day),goal_id:item.id,text:texts[((day-first)*perDay+part)%texts.length]});
  }
}
for(let day=1;day<=9;day++)mockEvents.push({day:date(day),text:['午后泡了杯茶，坐在窗边发了会儿呆','出门买水果，顺路散了一小圈步','晚上把桌面和练琴本收拾了一下'][day%3]});

export function mockAt(day:string){
  return {goals:mockGoals.filter(goal=>goal.start_date<=day).map(goal=>goal.completed_date&&goal.completed_date>day?
    {...goal,status:'active' as const,completed_date:undefined}:goal),events:mockEvents.filter(event=>event.day<=day)};
}
