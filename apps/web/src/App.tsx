import { useEffect, useState } from 'react';
import { NavigationContext, views, type View } from './components/AppShell';
import { ChatPage } from './pages/ChatPage';
import { QqPage } from './pages/QqPage';
import { ProfilesPage } from './pages/ProfilesPage';
import { MemoryPage } from './pages/MemoryPage';
import { LifePage } from './pages/LifePage';
import { RuntimeProvider } from './components/RuntimeStatus';

function currentView(): View {
  const view = new URLSearchParams(location.search).get('view');
  return views.find(item => item.id === view)?.id ?? 'chat';
}
const ReviewPage = () => <MemoryPage view="review"/>;
const pages = { chat: ChatPage, qq: QqPage, profiles: ProfilesPage, memory: MemoryPage, life: LifePage, review: ReviewPage };

export function App() {
  const [active, setActive] = useState<View>(currentView);
  const [visited, setVisited] = useState<View[]>([active]);
  const [expanded, setExpanded] = useState(true);
  const [search, setSearch] = useState(() => new URLSearchParams(location.search));
  function activate(view: View) {
    setActive(view); setExpanded(true);
    setVisited(previous => previous.includes(view) ? previous : [...previous, view]);
  }
  function select(view: View) {
    if (view === active) { setExpanded(value => !value); return; }
    navigate(view);
  }
  function navigate(view: View, params: Record<string, string | undefined> = {}) {
    activate(view);
    const url = new URL(location.href);
    if (view === 'chat') url.searchParams.delete('view'); else url.searchParams.set('view', view);
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined) url.searchParams.delete(key); else url.searchParams.set(key, value);
    }
    history.pushState(null, '', url);
    setSearch(new URLSearchParams(url.search));
  }
  function replace(params: Record<string, string | undefined>) {
    const url = new URL(location.href);
    for(const [key,value] of Object.entries(params)) {
      if(value===undefined)url.searchParams.delete(key);else url.searchParams.set(key,value);
    }
    history.replaceState(null,'',url);setSearch(new URLSearchParams(url.search));
  }
  useEffect(() => {
    const restore = () => {activate(currentView()); setSearch(new URLSearchParams(location.search));};
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  // Keep visited pages mounted so navigation preserves drafts and live streams.
  return <RuntimeProvider><NavigationContext.Provider value={{ active, expanded, select, navigate, replace, search, toggle: () => setExpanded(value => !value) }}>
    {visited.map(view => { const Page = pages[view];
      return <div className="page-host" key={view} hidden={view !== active}><Page /></div>;
    })}
  </NavigationContext.Provider></RuntimeProvider>;
}
