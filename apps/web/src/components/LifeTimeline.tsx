import { useState } from 'react';
import Masonry from 'react-masonry-css';
import { EmptyState } from './ui';

export type LifeEntry = {text: string; goal_id?: string};
export type LifeEvent = LifeEntry & {day: string};
export type LifeGoal = {id: string; text: string; start_date: string; target_date: string;
  status: 'active' | 'completed' | 'paused'; completed_date?: string};
export const goalStatus = {active: '进行中', completed: '已完成', paused: '暂时搁置'};

export function LifeTimeline({goals, events, onGoal, onDay}: {goals: LifeGoal[]; events: LifeEvent[];
  onGoal: (goal: LifeGoal) => void; onDay: (day: string) => void}) {
  const [filter, setFilter] = useState('all');
  const visible = events.filter(e => filter==='all' || (filter==='ordinary' ? !e.goal_id : e.goal_id===filter));
  const days = [...new Set(visible.map(e => e.day))].sort().reverse();
  const byId = new Map(goals.map(g => [g.id,g]));
  return <section className="life-timeline" aria-label="生活轨迹">
    <div className="life-timeline-filter"><label>活动<select aria-label="生活轨迹目标筛选" value={filter} onChange={e => setFilter(e.target.value)}>
      <option value="all">全部活动</option><option value="ordinary">日常活动</option>
      {goals.map(g => <option key={g.id} value={g.id}>{g.text}</option>)}</select></label></div>
    {!days.length && <EmptyState title="暂无生活记录"/>}
    <Masonry breakpointCols={{default:2,1400:1}} className="life-masonry" columnClassName="life-masonry-column">
      {days.map(day => {
        const entries = visible.filter(e => e.day===day);
        const groups = [...new Set(entries.map(e => e.goal_id ?? ''))];
        return <article className="life-day-card" key={day}>
          <header><h2><time dateTime={day}>{day}</time></h2>
            <button className="text-button" onClick={() => onDay(day)}>编辑当天</button></header>
          {groups.map(id => <section className="life-activity-group" key={id}>
            {byId.has(id) && <button className="life-goal-reference" onClick={() => onGoal(byId.get(id)!)}>
              {byId.get(id)!.text}</button>}
            <ul>{entries.filter(e => (e.goal_id ?? '')===id).map((entry,i) => <li key={i}>{entry.text}</li>)}</ul>
          </section>)}
        </article>;
      })}
    </Masonry>
  </section>;
}
