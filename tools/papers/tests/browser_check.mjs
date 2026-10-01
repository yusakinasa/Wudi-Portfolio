/** Optional dependency-free Chrome/CDP checks against an already-built fixture site. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';

const root = process.cwd();
const dist = path.join(root, 'dist');
const base = process.env.PAPER_TEST_BASE ?? '/Portfolio';
const chromePath = process.env.PAPER_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const cache = path.join(root, '.cache/papers/browser');
await fs.mkdir(cache, { recursive: true });
const profile = await fs.mkdtemp(path.join(cache, 'profile-'));
const server = http.createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (base && !pathname.startsWith(`${base}/`)) { response.writeHead(404).end(); return; }
    const relative = pathname.slice(base.length).replace(/^\//, '');
    let file = path.resolve(dist, relative);
    if (!file.startsWith(`${dist}/`)) { response.writeHead(403).end(); return; }
    if ((await fs.stat(file)).isDirectory()) file = path.join(file, 'index.html');
    const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.webp': 'image/webp' };
    response.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    response.end(await fs.readFile(file));
  } catch { response.writeHead(404).end(); }
});
if (!process.env.PAPER_TEST_ORIGIN) await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const origin = process.env.PAPER_TEST_ORIGIN || `http://127.0.0.1:${server.address().port}`;
const browser = spawn(chromePath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-default-apps', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  let port;
  for (let i = 0; i < 100; i++) {
    try { port = (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break; }
    catch { await delay(100); }
  }
  assert(port, 'Chrome did not start');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(t => t.type === 'page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  const pending = new Map();
  let serial = 0;
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject, timeout } = pending.get(message.id);
      clearTimeout(timeout);
      pending.delete(message.id);
      if (message.error) reject(new Error(JSON.stringify(message.error))); else resolve(message.result);
    }
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    pending.set(id, { resolve, reject, timeout });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const navigate = async url => {
    await command('Page.navigate', { url });
    for (let i = 0; i < 50; i++) {
      if (await evaluate(`location.href === ${JSON.stringify(url)} && document.readyState === 'complete'`)) return;
      await delay(100);
    }
    throw new Error('Page did not load');
  };
  await command('Page.enable');
  await command('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false });
  await navigate(`${origin}${base}/papers/`);
  assert.equal(await evaluate('document.querySelectorAll("[data-paper-card]").length'), 2);
  const visible = () => evaluate('document.querySelectorAll("[data-paper-card]:not([hidden])").length');
  await evaluate('document.querySelector("[name=q]").value="Example Author"; document.querySelector("[name=q]").dispatchEvent(new Event("input",{bubbles:true}))');
  assert.equal(await visible(), 2, 'Author search');
  await evaluate('document.querySelector("[name=year]").value="2025"; document.querySelector("[name=year]").dispatchEvent(new Event("change",{bubbles:true}))');
  assert.equal(await visible(), 1, 'Year filter');
  await evaluate('document.querySelector("[name=tag]").value="testing"; document.querySelector("[name=tag]").dispatchEvent(new Event("change",{bubbles:true}))');
  assert.equal(await visible(), 0, 'Combined filters');
  assert.equal(await evaluate('document.querySelector("[data-paper-empty]").hidden'), false);
  await evaluate('document.querySelector(".paper-filters").reset()');
  await delay(100);
  assert.equal(await visible(), 2, 'Reset');
  await evaluate('document.querySelector("[name=q]").value="Fixture Study"; document.querySelector("[name=q]").dispatchEvent(new Event("input",{bubbles:true}))');
  assert.equal(await visible(), 2, 'Title token search');
  const summary = await (await fetch(`${origin}${base}/papers/index.json`)).json();
  assert.equal(summary.papers.length, 2);
  assert.equal(summary.papers.some(p => p.evidence || p.method || p.experiments), false, 'Index must remain lightweight');
  const fixture = summary.papers.find(p => p.year === 2026);
  const detail = `${origin}${base}/papers/${fixture.id}/`;
  await navigate(detail); // Direct access, not a client-side route transition.
  assert(await evaluate('document.querySelector("#method") !== null'));
  assert.equal(await evaluate('document.querySelectorAll("#figures .paper-figure").length'), 1, 'Overview figure');
  assert(await evaluate(`new Promise(resolve => {
    const image = document.querySelector('#figures img');
    image.scrollIntoView();
    if (image.complete && image.naturalWidth > 0) return resolve(true);
    image.addEventListener('load', () => resolve(true), { once: true });
    image.addEventListener('error', () => resolve(false), { once: true });
    setTimeout(() => resolve(false), 5000);
  })`), 'Lazy figure asset loaded');
  await evaluate('document.querySelector("[data-figure-open]").click()');
  assert.equal(await evaluate('document.querySelector("[data-paper-image-dialog]").open'), true, 'Figure enlargement');
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
  assert.equal(await evaluate('document.querySelector("[data-paper-image-dialog]").open'), false, 'Keyboard dismissal');
  assert(await evaluate(`!!document.querySelector('a[href="${base}/papers/companion-fixture-2025/"]')`), 'Internal related link');
  assert.equal(await evaluate('document.querySelector("#evidence, [data-evidence-panel], [data-evidence-link]")'), null, 'Evidence remains internal, not rendered');
  assert.equal(await evaluate('document.querySelectorAll("a[href^=\\"#evidence\\"]").length'), 0, 'No evidence jump chips');
  await command('Page.reload');
  await delay(300);
  assert(await evaluate('document.querySelector("#method") !== null'), 'Detail refresh');
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await navigate(detail);
  await evaluate('document.querySelector("#figures").scrollIntoView({ behavior: "instant", block: "start" })');
  assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '390px detail overflow');
  let screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await fs.writeFile(path.join(cache, 'detail-mobile.png'), Buffer.from(screenshot.data, 'base64'));
  await evaluate('document.querySelector("[data-figure-open]").click()');
  assert(await evaluate('document.querySelector("[data-paper-image-dialog]").getBoundingClientRect().width <= window.innerWidth'), 'Mobile image dialog overflow');
  screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await fs.writeFile(path.join(cache, 'figure-mobile.png'), Buffer.from(screenshot.data, 'base64'));
  await evaluate('document.querySelector("[data-paper-image-dialog]").close()');
  await navigate(`${origin}${base}/papers/companion-fixture-2025/`);
  assert(await evaluate(`!!document.querySelector('#related a[href="https://example.org/reference"][rel="noopener noreferrer"]')`), 'External related link');
  await navigate(`${origin}${base}/papers/?tag=testing`);
  assert.equal(await visible(), 1, 'Shareable tag URL');
  assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '390px library overflow');
  screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await fs.writeFile(path.join(cache, 'library-mobile.png'), Buffer.from(screenshot.data, 'base64'));
  const requests = await evaluate('performance.getEntriesByType("resource").map(e => e.name)');
  assert.equal(requests.some(url => /\/papers\/(?!index\.json)[^/]+\.json/.test(url)), false, 'No full paper JSON fetches');
  if (process.env.PAPER_TEST_ORIGIN) {
    const api = `${origin}${base}/__paper-tags`;
    const read = async () => (await fetch(api)).json();
    const save = async (config, revision, extra = {}) => fetch(api, { method: 'POST',
      headers: { Origin: origin, 'Content-Type': 'application/json', ...extra },
      body: JSON.stringify({ config, base_revision: revision }) });
    const original = await read();
    const waitFor = async (expression, label) => {
      for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await delay(100); }
      throw new Error(`Timed out: ${label}`);
    };
    const ready = () => waitFor('document.querySelector("[data-paper-tag-tools]")?.dataset.ready === "true"', 'tag editor');
    const companionId = 'companion-fixture-2025';
    try {
      await navigate(`${origin}${base}/papers/`); await ready();
      assert.match(await evaluate(`document.querySelector('[data-edit-paper="${companionId}"]').textContent`), /尚未设置/);
      await evaluate(`document.querySelector('[data-edit-paper="${companionId}"]').click()`);
      assert(await evaluate('document.querySelector("[data-paper-tag-dialog]").open'), 'Tag dialog');
      assert(await evaluate('document.querySelector("[data-paper-tag-dialog]").getBoundingClientRect().width <= innerWidth'), '390px tag dialog');
      screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      await fs.writeFile(path.join(cache, 'tag-dialog-mobile.png'), Buffer.from(screenshot.data, 'base64'));
      for (const name of ['人工测试', '结构方法']) {
        await evaluate(`document.querySelector('[data-paper-tag-dialog] [name=name]').value=${JSON.stringify(name)}; document.querySelector('[data-paper-tag-dialog] [data-tag-create]').requestSubmit()`);
        await waitFor(`Array.from(document.querySelectorAll('[data-tag-choices] label')).some(el => el.textContent === ${JSON.stringify(name)})`, `create ${name}`);
      }
      await evaluate('document.querySelector("[data-tag-selection]").requestSubmit()');
      await waitFor('!document.querySelector("[data-paper-tag-dialog]").open', 'save assignment');
      let current = await read();
      assert.equal(current.config.papers[companionId].length, 2, 'Multi-tags actually persisted');
      await navigate(`${origin}${base}/papers/${companionId}/`); await ready();
      assert.equal(await evaluate('document.querySelectorAll("[data-manual-tag-list] li").length'), 2, 'Persistence across pages');
      await command('Page.reload'); await delay(500); await ready();
      assert.equal(await evaluate('document.querySelectorAll("[data-manual-tag-list] li").length'), 2, 'Persistence across refresh');
      await navigate(`${origin}${base}/papers/?tag=${encodeURIComponent('人工测试')}`); await ready();
      assert.equal(await visible(), 1, 'Manual tag filters paper list');
      await navigate(`${origin}${base}/papers/tags/`); await ready();
      const first = current.config.tags.find(t => t.name === '人工测试');
      await evaluate(`const row=document.querySelector('[data-tag-id="${first.id}"]'); row.querySelector('input').value='人工分类'; row.requestSubmit()`);
      await waitFor(`document.querySelector('[data-tag-id="${first.id}"] input')?.value === '人工分类' && document.querySelector('[data-tags-status]').textContent.includes('已保存')`, 'rename');
      current = await read(); assert(current.config.papers[companionId].includes(first.id), 'Rename preserves stable assignment');
      screenshot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      await fs.writeFile(path.join(cache, 'tag-manager-mobile.png'), Buffer.from(screenshot.data, 'base64'));
      await evaluate(`document.querySelector('.paper-tag-manager > [data-tag-create] [name=name]').value=' TESTING '; document.querySelector('.paper-tag-manager > [data-tag-create]').requestSubmit()`);
      await waitFor('document.querySelector("[data-tags-status]").dataset.error === "true"', 'duplicate tag rejected');
      assert.equal((await read()).config.tags.length, current.config.tags.length, 'Duplicate name not written');
      const accept = event => {
        const message = JSON.parse(event.data);
        if (message.method === 'Page.javascriptDialogOpening') command('Page.handleJavaScriptDialog', { accept: true });
      };
      socket.addEventListener('message', accept);
      await evaluate(`document.querySelector('[data-delete-tag="${first.id}"]').click()`);
      await waitFor(`!document.querySelector('[data-tag-id="${first.id}"]')`, 'delete');
      socket.removeEventListener('message', accept);
      current = await read();
      assert.equal(current.config.papers[companionId].length, 1, 'Delete clears assignments');
      await navigate(`${origin}${base}/papers/${companionId}/`); await ready();
      await evaluate('document.querySelector("[data-edit-paper]").click(); document.querySelectorAll("[data-tag-choices] input:checked").forEach(input => {input.checked=false; input.dispatchEvent(new Event("change"))}); document.querySelector("[data-tag-selection]").requestSubmit()');
      await waitFor('!document.querySelector("[data-paper-tag-dialog]").open', 'clear paper tags');
      assert.match(await evaluate('document.querySelector("[data-edit-paper]").textContent'), /尚未设置/);
      current = await read(); assert.deepEqual(current.config.papers[companionId], []);
      assert.equal((await save(original.config, original.revision)).status, 400, 'Stale write refused');
      assert.equal((await save(current.config, current.revision, { Origin: 'https://malicious.example' })).status, 403, 'Cross-origin write refused');
      assert.equal((await save({ ...current.config, papers: { 'fixture-2026': ['t-missing'] } }, current.revision)).status, 400, 'Unknown tag rejected');
      assert.deepEqual((await read()).config, current.config, 'Failed saves preserve config');
      console.log('PASS: localhost tag dialog, multiple tags, real file writes, reload persistence, manual filtering, taxonomy create/rename/delete, duplicate/stale/unsafe writes rejected');
    } finally {
      const latest = await read();
      assert.equal((await save(original.config, latest.revision)).status, 200, 'Restore fixture taxonomy only');
    }
  }
  console.log('PASS: figure load/enlargement/Escape/mobile dialog, search, combined filters, reset, lightweight index, direct detail/refresh, internal/external related links, evidence hidden, 390px layout');
  console.log(`Screenshots: ${path.relative(root, cache)}`);
} finally {
  socket?.close();
  browser.kill('SIGTERM');
  await Promise.race([new Promise(resolve => browser.once('exit', resolve)), delay(2000)]);
  if (browser.exitCode === null) browser.kill('SIGKILL');
  if (server.listening) await new Promise(resolve => server.close(resolve));
}
