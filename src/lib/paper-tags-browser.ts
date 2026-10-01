import { assignedIds, manualTagNames, validateManualTags, type ManualTags, type TagSnapshot } from './paper-tags';

/** Localhost editor only. All persistence goes through the local file-backed service. */
export async function setupPaperTags() {
  const tools = document.querySelector<HTMLElement>('[data-paper-tag-tools]');
  if (!tools) return;
  const base = tools.dataset.base || '';
  const status = tools.querySelector<HTMLElement>('[data-tags-status]')!;
  const dialog = document.querySelector<HTMLDialogElement>('[data-paper-tag-dialog]')!;
  const dialogStatus = dialog.querySelector<HTMLElement>('[data-tag-dialog-status]')!;
  let snapshot = JSON.parse(tools.querySelector('[data-tag-snapshot]')!.textContent!) as TagSnapshot;
  let config = snapshot.config, activePaper = '', busy = false;
  let selected = new Set<string>();
  const note = (message: string, error = false) => {
    status.textContent = message; status.dataset.error = String(error);
    dialogStatus.textContent = message; dialogStatus.dataset.error = String(error);
  };
  const request = async (method: string, body?: unknown): Promise<TagSnapshot> => {
    const response = await fetch(`${base}/__paper-tags`, { method, cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('本地标签编辑服务不可用，请使用 npm run dev。');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '标签编辑失败。');
    return { config: validateManualTags(data.config), revision: data.revision };
  };
  try { snapshot = await request('GET'); config = snapshot.config; }
  catch (error) { note(`读取项目标签失败：${String(error)}`, true); }
  const button = (label: string) => {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'paper-tag-button'; b.textContent = label; return b;
  };
  const choices = () => {
    const list = dialog.querySelector('[data-tag-choices]')!;
    list.replaceChildren();
    for (const tag of config.tags) {
      const label = document.createElement('label'), input = document.createElement('input');
      input.type = 'checkbox'; input.name = 'tag'; input.value = tag.id; input.checked = selected.has(tag.id);
      input.addEventListener('change', () => input.checked ? selected.add(tag.id) : selected.delete(tag.id));
      label.append(input, document.createTextNode(tag.name)); list.append(label);
    }
    if (!config.tags.length) list.textContent = '还没有标签，请在下方创建第一个标签。';
  };
  const render = () => {
    for (const region of document.querySelectorAll<HTMLElement>('[data-paper-tag-assignment]')) {
      const id = region.dataset.paperId!;
      const names = manualTagNames(config, id);
      const list = region.querySelector('[data-manual-tag-list]')!;
      list.replaceChildren();
      for (const name of names) {
        const li = document.createElement('li'), a = document.createElement('a');
        a.href = `${base}/papers/?tag=${encodeURIComponent(name)}`; a.textContent = name; li.append(a); list.append(li);
      }
      region.querySelector('[data-edit-paper]')!.textContent = names.length ? '设置标签' : '尚未设置标签，请开始设置';
      const card = region.closest<HTMLElement>('[data-paper-card]');
      if (card) { card.dataset.tags = JSON.stringify(names); card.dataset.search = `${card.dataset.searchBase || ''} ${names.join(' ')}`.toLowerCase(); }
    }
    const manager = document.querySelector('[data-tag-manager-list]');
    if (manager) {
      manager.replaceChildren();
      for (const tag of config.tags) {
        const row = document.createElement('form'); row.className = 'paper-tag-manager-row'; row.dataset.tagId = tag.id;
        const input = document.createElement('input'); input.value = tag.name; input.required = true; input.maxLength = 60; input.setAttribute('aria-label', `重命名 ${tag.name}`);
        const count = document.createElement('span'); count.textContent = `${Object.values(config.papers).filter(ids => ids.includes(tag.id)).length} 篇关联`;
        const rename = button('保存改名'); rename.type = 'submit';
        const remove = button('删除'); remove.dataset.deleteTag = tag.id;
        row.append(input, count, rename, remove);
        row.addEventListener('submit', async event => {
          event.preventDefault();
          const next = structuredClone(config); next.tags.find(t => t.id === tag.id)!.name = input.value;
          await attempt(() => persist(next));
        });
        remove.addEventListener('click', async () => {
          if (!confirm(`删除“${tag.name}”？它将从全部关联论文中移除，论文内容不受影响。`)) return;
          const next = structuredClone(config); next.tags = next.tags.filter(t => t.id !== tag.id);
          for (const id of Object.keys(next.papers)) next.papers[id] = next.papers[id].filter(t => t !== tag.id);
          await attempt(() => persist(next));
        });
        manager.append(row);
      }
      if (!config.tags.length) manager.textContent = '还没有标签，请先创建。';
    }
    document.dispatchEvent(new CustomEvent('paper-tags-change', { detail: config.tags.map(t => t.name).sort() }));
  };
  const persist = async (next: ManualTags) => {
    next = validateManualTags(next);
    snapshot = await request('POST', { config: next, base_revision: snapshot.revision });
    config = snapshot.config; render();
    note('已保存到项目 data/paper-tags.json，并更新论文索引。');
  };
  const attempt = async (operation: () => Promise<void>) => {
    if (busy) return false;
    busy = true; tools.setAttribute('aria-busy', 'true');
    try { await operation(); return true; }
    catch (error) { note(`未保存：${String(error)}。如其他页面已修改，请刷新后重试。`, true); return false; }
    finally { busy = false; tools.removeAttribute('aria-busy'); }
  };
  document.querySelectorAll<HTMLButtonElement>('[data-edit-paper]').forEach(b => b.addEventListener('click', () => {
    activePaper = b.dataset.editPaper!; selected = new Set(assignedIds(config, activePaper));
    dialog.querySelector('[data-tag-paper-title]')!.textContent = b.closest<HTMLElement>('[data-paper-tag-assignment]')!.dataset.paperTitle!;
    dialogStatus.textContent = ''; choices(); dialog.showModal();
  }));
  dialog.querySelector('[data-tag-cancel]')!.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => { if (e.target === dialog) { const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); } });
  dialog.querySelector('form[data-tag-selection]')!.addEventListener('submit', async event => {
    event.preventDefault(); const next = structuredClone(config); next.papers[activePaper] = [...selected];
    if (await attempt(() => persist(next))) dialog.close();
  });
  document.querySelectorAll<HTMLFormElement>('[data-tag-create]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault(); const input = form.querySelector<HTMLInputElement>('[name=name]')!;
    const id = `t-${crypto.randomUUID().replaceAll('-', '')}`;
    const next = structuredClone(config); next.tags.push({ id, name: input.value });
    if (await attempt(() => persist(next))) { input.value = ''; if (dialog.open) { selected.add(id); choices(); } }
  }));
  render();
  tools.dataset.ready = 'true';
}
