/** Loopback-only Astro dev editor. Absent from build/preview/GitHub Pages. */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export default function paperTagEditor() {
  return {
    name: 'local-paper-tag-editor',
    hooks: {
      'astro:server:setup': ({ server }) => {
        const siteRoot = process.cwd();
        const data = path.resolve(siteRoot, process.env.PAPER_DATA_DIR || 'data/papers');
        const root = path.dirname(path.dirname(data));
        const python = fs.existsSync(path.join(siteRoot, '.venv-papers/bin/python'))
          ? path.join(siteRoot, '.venv-papers/bin/python') : 'python3';
        const endpoint = `${server.config.base.replace(/\/$/, '')}/__paper-tags`;
        const loopback = host => ['localhost', '127.0.0.1', '[::1]', '::1', '::ffff:127.0.0.1'].includes(host);
        const run = (command, input) => new Promise((resolve, reject) => {
          const child = spawn(python, [path.join(siteRoot, 'tools/papers/tags.py'), command, '--root', root],
            { cwd: siteRoot, stdio: ['pipe', 'pipe', 'pipe'] });
          let output = '', error = '';
          const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error('标签保存超时，请刷新核对已保存配置后重试。')); }, 15000);
          child.stdout.on('data', chunk => { output += chunk; });
          child.stderr.on('data', chunk => { error += chunk; });
          child.on('error', e => { clearTimeout(timer); reject(e); });
          child.on('close', code => {
            clearTimeout(timer);
            if (code !== 0) reject(new Error(error.trim() || '标签服务失败，请安装 .venv-papers 的依赖。'));
            else { try { resolve(JSON.parse(output)); } catch { reject(new Error('标签服务输出无效。')); } }
          });
          child.stdin.on('error', () => {});
          child.stdin.end(input || '');
        });
        server.middlewares.use(async (req, res, next) => {
          const requestUrl = new URL(req.url || '/', 'http://localhost');
          if (requestUrl.pathname !== endpoint) { next(); return; }
          const respond = (status, value) => {
            res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
            res.end(JSON.stringify(value));
          };
          try {
            const host = new URL(`http://${req.headers.host || ''}`);
            if (!loopback(host.hostname) || !loopback(req.socket.remoteAddress) || path.basename(data) !== 'papers' || path.basename(path.dirname(data)) !== 'data') {
              respond(403, { error: '仅允许本机 loopback 编辑。' }); return;
            }
            if (req.method === 'GET') { respond(200, await run('read')); return; }
            if (req.method !== 'POST') { respond(405, { error: 'Method not allowed' }); return; }
            const origin = req.headers.origin;
            if (!origin || new URL(origin).host !== host.host || !loopback(new URL(origin).hostname) ||
                !['http:', 'https:'].includes(new URL(origin).protocol) ||
                req.headers['content-type']?.split(';')[0] !== 'application/json') {
              respond(403, { error: '仅允许同源 JSON 编辑请求。' }); return;
            }
            const chunks = []; let size = 0;
            for await (const chunk of req) {
              size += chunk.length;
              if (size > 1024 * 1024) { respond(413, { error: '标签配置不能超过 1 MB。' }); return; }
              chunks.push(chunk);
            }
            respond(200, await run('save', Buffer.concat(chunks).toString('utf8')));
          } catch (error) { respond(400, { error: error.message || '标签操作失败。' }); }
        });
      }
    }
  };
}
