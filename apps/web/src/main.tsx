import { Context } from '@deepseek-ai/cordis';
import * as UiRenderer from '@deepseek-ai/dsh-client-ui-renderer/client';
import { App } from './App';
import './styles/app.css';
import './styles/system.css';

async function start() {
  const context = new Context();
  const renderer = context.plugin({ inject: UiRenderer.inject, apply: UiRenderer.apply });
  await renderer.await();
  // DSH's renderer expects a session scope even for a root-only page. Miko's
  // conversation stays in ChatPage; no DSH session-controller UI is installed.
  const absent = { key: undefined, hooks: {}, keyedHooks: {}, props: {} };
  const source = { getSnapshot: () => absent, subscribe: () => () => {} };
  context.slots.installScope('session', { current: source, bindingSource: () => source });
  // This standalone application owns the root; the stock DSH layout is not mounted.
  context.slots.register({ name: 'root' }, App);
  const unmount = context.uiRenderer.mount(document.getElementById('app')!);
  window.addEventListener('pagehide', unmount, { once: true });
}

start().catch((error: unknown) => {
  document.getElementById('app')!.textContent = `页面启动失败：${String(error)}`;
});
