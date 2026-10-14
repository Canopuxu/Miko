import http from 'node:http';
import { spawn } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const local = resolve(repo, '../MikoLocal');
const home = resolve(local, 'dsh-workbench/home');
const workspace = resolve(local, 'dsh-workbench/workspace');
for (const dir of [home, workspace, resolve(local, 'dsh-workbench/agents')]) mkdirSync(dir, { recursive: true });
const theme = () => readFileSync(resolve(repo, 'apps/web/src/styles/workbench-native.css'));
let launchUrl;
let ready = false;
const child = spawn(process.execPath, [resolve(local, 'runtime/dsh-workbench/node_modules/@deepseek-ai/dsh/lib/bin.js'),
  'web', '--no-open', '--port', '7872', '--public-url', 'http://127.0.0.1:7871/'], {
  cwd: workspace, windowsHide: true,
  env: { ...process.env, DSH_HOME: home, DSH_AGENTS_HOME: resolve(local, 'dsh-workbench/agents') },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let buffered = '';
child.stdout.on('data', chunk => {
  buffered += chunk.toString();
  const lines = buffered.split(/\r?\n/); buffered = lines.pop();
  for (const line of lines) {
    const match = line.match(/http:\/\/127\.0\.0\.1:7871\/\S+/);
    if (match) { launchUrl = new URL(match[0]); ready = true; console.log('DSH workbench ready on 7871'); }
    else console.log(line);
  }
});
child.stderr.on('data', chunk => process.stderr.write(chunk));
child.on('exit', code => { ready = false; console.error('DSH exited', code); });

const server = http.createServer((req, res) => {
  if (!['127.0.0.1:7871', 'localhost:7871'].includes(req.headers.host)) { res.writeHead(403).end(); return; }
  if (req.url === '/_miko/health') {
    res.setHeader('Access-Control-Allow-Origin', 'http://127.0.0.1:7870');
    res.writeHead(ready ? 200 : 503, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ready })); return;
  }
  if (req.url === '/_miko/theme.css') { res.writeHead(200, { 'Content-Type': 'text/css', 'Cache-Control': 'no-cache' }).end(theme()); return; }
  if (req.url === '/_miko/start') {
    if (!launchUrl) { res.writeHead(503).end('DSH is starting.'); return; }
    res.writeHead(302, { Location: launchUrl.pathname + launchUrl.search, 'Cache-Control': 'no-store' }).end(); return;
  }
  const upstream = http.request({ hostname: '127.0.0.1', port: 7872, path: req.url, method: req.method,
    headers: { ...req.headers, 'accept-encoding': 'identity' } }, response => {
    const headers = { ...response.headers };
    if (headers['content-type']?.includes('text/html')) {
      const chunks = []; response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => {
        const html = Buffer.concat(chunks).toString().replace('</head>', '<link rel="stylesheet" href="/_miko/theme.css"></head>');
        delete headers['content-length']; delete headers.etag;
        res.writeHead(response.statusCode, headers).end(html);
      });
    } else { res.writeHead(response.statusCode, headers); response.pipe(res); }
  });
  upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('DSH is unavailable.'); });
  req.pipe(upstream);
});
server.on('upgrade', (req, socket, head) => {
  if (!['127.0.0.1:7871', 'localhost:7871'].includes(req.headers.host)) { socket.destroy(); return; }
  const upstream = http.request({ hostname: '127.0.0.1', port: 7872, path: req.url, headers: req.headers });
  upstream.on('upgrade', (response, peer, peerHead) => {
    socket.write(`HTTP/1.1 101 Switching Protocols\r\n${Object.entries(response.headers).map(([k,v]) => `${k}: ${v}`).join('\r\n')}\r\n\r\n`);
    if (head.length) peer.write(head); if (peerHead.length) socket.write(peerHead);
    socket.pipe(peer).pipe(socket); socket.on('error', () => peer.destroy()); peer.on('error', () => socket.destroy());
  });
  upstream.on('response', response => { socket.end(`HTTP/1.1 ${response.statusCode} Rejected\r\nConnection: close\r\n\r\n`); response.resume(); });
  upstream.on('error', () => socket.destroy()); upstream.end();
});
server.listen(7871, '127.0.0.1');
writeFileSync(resolve(local, 'dsh-workbench/process.json'), JSON.stringify({ pid: process.pid, child: child.pid }));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { child.kill(); server.close(); });
