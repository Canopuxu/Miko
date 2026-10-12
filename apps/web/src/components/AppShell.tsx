import { createContext, useContext, type ReactNode } from 'react';

export type View = 'chat' | 'qq' | 'profiles' | 'memory';
export const views: { id: View; label: string; icon: string; title: string }[] = [
  { id: 'chat', label: '聊天', icon: '◌', title: '最近对话' },
  { id: 'qq', label: 'QQ 好友', icon: '♧', title: 'QQ 好友' },
  { id: 'profiles', label: '设定与资料', icon: '▤', title: '资料库' },
  { id: 'memory', label: '关系与经历记忆', icon: '◇', title: '记忆对象' },
];
export const NavigationContext = createContext<{
  active: View; expanded: boolean; select: (view: View) => void; toggle: () => void;
} | null>(null);

export function AppShell({ view, sidebar, children }: { view: View; sidebar: ReactNode; children: ReactNode }) {
  const navigation = useContext(NavigationContext)!;
  const current = views.find(item => item.id === view)!;
  return <div className={`workspace-frame ${navigation.expanded ? '' : 'sidebar-collapsed'}`}>
    <aside className="primary-sidebar">
      <button className="brand" onClick={() => navigation.select('chat')} aria-label="Miko 首页">
        <span className="brand-mark">m</span><span>Miko</span>
      </button>
      <div className="sidebar-label">我们的空间</div>
      <nav className="primary-navigation" aria-label="一级导航">
        {views.map(view => <button key={view.id} className={`primary-item ${view.id === navigation.active ? 'selected' : ''}`}
          aria-current={view.id === navigation.active ? 'page' : undefined}
          aria-expanded={view.id === navigation.active ? navigation.expanded : false}
          aria-controls={`secondary-${view.id}`} title={view.label} onClick={() => navigation.select(view.id)}>
          <span className="primary-icon" aria-hidden="true">{view.icon}</span><span>{view.label}</span>
        </button>)}
      </nav>
      <button className="sidebar-toggle" onClick={navigation.toggle}
        aria-label={navigation.expanded ? '收起二级侧栏' : '展开二级侧栏'}>
        <span aria-hidden="true">{navigation.expanded ? '«' : '»'}</span><span>{navigation.expanded ? '收起列表' : '展开列表'}</span>
      </button>
    </aside>
    <aside id={`secondary-${view}`} className="secondary-sidebar" aria-label={`${current.label}二级侧栏`} hidden={!navigation.expanded}>
      <header className="secondary-header"><h2>{current.title}</h2>
        <button onClick={navigation.toggle} aria-label="关闭二级侧栏" title="收起列表">‹</button>
      </header>
      {sidebar}
    </aside>
    {children}
  </div>;
}
