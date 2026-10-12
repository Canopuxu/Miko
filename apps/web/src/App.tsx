import { useEffect, useState } from 'react';
import { NavigationContext, views, type View } from './components/AppShell';
import { ChatPage } from './pages/ChatPage';
import { QqPage } from './pages/QqPage';
import { ProfilesPage } from './pages/ProfilesPage';
import { MemoryPage } from './pages/MemoryPage';

function currentView(): View {
  const view = new URLSearchParams(location.search).get('view');
  return views.find(item => item.id === view)?.id ?? 'chat';
}
const pages = { chat: ChatPage, qq: QqPage, profiles: ProfilesPage, memory: MemoryPage };

export function App() {
  const [active, setActive] = useState<View>(currentView);
  const [visited, setVisited] = useState<View[]>([active]);
  const [expanded, setExpanded] = useState(true);
  function activate(view: View) {
    setActive(view); setExpanded(true);
    setVisited(previous => previous.includes(view) ? previous : [...previous, view]);
  }
  function select(view: View) {
    if (view === active) { setExpanded(value => !value); return; }
    activate(view);
    const url = new URL(location.href);
    if (view === 'chat') url.searchParams.delete('view'); else url.searchParams.set('view', view);
    history.pushState(null, '', url);
  }
  useEffect(() => {
    const restore = () => activate(currentView());
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  // Keep visited pages mounted so navigation preserves drafts and live streams.
  return <NavigationContext.Provider value={{ active, expanded, select, toggle: () => setExpanded(value => !value) }}>
    {visited.map(view => { const Page = pages[view];
      return <div className="page-host" key={view} hidden={view !== active}><Page /></div>;
    })}
  </NavigationContext.Provider>;
}
