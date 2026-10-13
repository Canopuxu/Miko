import { createContext, useContext, type ReactNode } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, MessageCircle, NotebookPen, Sprout, Users, UserRound, type LucideIcon } from 'lucide-react';
import { RuntimeStatus } from './RuntimeStatus';

export type View = 'chat' | 'qq' | 'profiles' | 'memory' | 'life' | 'review';
export const views: { id: View; label: string; icon: LucideIcon; title: string }[] = [
  { id: 'chat', label: '聊天', icon: MessageCircle, title: '最近对话' },
  { id: 'life', label: '生活', icon: Sprout, title: '生活记录' },
  { id: 'qq', label: 'QQ 好友', icon: Users, title: 'QQ 好友' },
  { id: 'review', label: '回复回顾', icon: NotebookPen, title: '回顾与批注' },
  { id: 'profiles', label: '设定与资料', icon: UserRound, title: '资料库' },
  { id: 'memory', label: '记忆', icon: BookOpen, title: '记忆对象' },
];
export const NavigationContext = createContext<{
  active: View; expanded: boolean; select: (view: View) => void; toggle: () => void;
  navigate: (view: View, params?: Record<string, string | undefined>) => void;
  replace: (params: Record<string, string | undefined>) => void;
  search: URLSearchParams;
} | null>(null);

export function AppShell({ view, sidebar, children }: { view: View; sidebar: ReactNode; children: ReactNode }) {
  const navigation = useContext(NavigationContext)!;
  const current = views.find(item => item.id === view)!;
  return <div className={`workspace-frame ${navigation.expanded ? '' : 'sidebar-collapsed'}`}>
    <aside className="primary-sidebar">
      <button className="brand" onClick={() => navigation.select('chat')} aria-label="Miko 首页">
        <span className="brand-mark"><span className="brand-wordmark" aria-hidden="true">Miko</span></span>
      </button>
      <nav className="primary-navigation" aria-label="一级导航">
        {views.map(view => <button key={view.id} className={`primary-item ${view.id === navigation.active ? 'selected' : ''}`}
          aria-current={view.id === navigation.active ? 'page' : undefined}
          aria-expanded={view.id === navigation.active ? navigation.expanded : false}
          aria-controls={`secondary-${view.id}`} title={view.label} onClick={() => navigation.select(view.id)}>
          <view.icon className="primary-icon" size={19} strokeWidth={1.6} aria-hidden="true"/><span>{view.label}</span>
        </button>)}
      </nav>
      <div className="primary-bottom"><RuntimeStatus navigate={view => navigation.navigate(view)}/>
      <button className="sidebar-toggle" onClick={navigation.toggle}
        aria-label={navigation.expanded ? '收起二级侧栏' : '展开二级侧栏'}>
        {navigation.expanded ? <ChevronLeft size={16}/> : <ChevronRight size={16}/>}
        <span>{navigation.expanded ? '收起列表' : '展开列表'}</span></button></div>
    </aside>
    <aside id={`secondary-${view}`} className="secondary-sidebar" aria-label={`${current.label}二级侧栏`} hidden={!navigation.expanded}>
      <header className="secondary-header"><h2>{current.title}</h2>
        <button onClick={navigation.toggle} aria-label="关闭二级侧栏" title="收起列表"><ChevronLeft size={18}/></button>
      </header>
      {sidebar}
    </aside>
    {children}
  </div>;
}
