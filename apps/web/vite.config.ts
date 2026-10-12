import { defineConfig } from 'vite';

// DSH publishes its browser plugin as a module-registration factory. Adapt that
// factory at build time so this standalone Vite app can use the same official code.
export default defineConfig({
  plugins: [{
    name: 'miko-dsh-client-factory',
    enforce: 'pre',
    transform(code, id) {
      if (!id.replaceAll('\\', '/').endsWith('/dsh-client-ui-renderer/lib/client.js')) return;
      const dependencies = [...new Set([...code.matchAll(/require\("([^"\n]+)"\)/g)].map(m => m[1]))];
      const imports = dependencies.map((name, i) => `import * as dep${i} from ${JSON.stringify(name)};`).join('\n');
      const modules = dependencies.map((name, i) => `${JSON.stringify(name)}: dep${i}`).join(',');
      if (!code.startsWith('window.__ModuleLoader__.load(')) throw new Error('Unexpected DSH client bundle format');
      return `${imports}\nconst modules = {${modules}};\n` +
        code.replace('window.__ModuleLoader__.load(', 'const plugin = ((entry) => entry.factory(name => modules[name]))(') +
        '\nexport const apply = plugin.apply;\nexport const inject = plugin.inject;\n';
    },
  }],
});
