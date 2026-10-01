/** Shared browser/build validation. This file has no filesystem or credential access. */
export interface ManualTags {
  version: '1.0';
  tags: { id: string; name: string }[];
  papers: Record<string, string[]>;
}
export interface TagSnapshot { config: ManualTags; revision: string }
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const keys = (v: Record<string, unknown>, expected: string[]) => Object.keys(v).sort().join(',') === expected.sort().join(',');
export function validateManualTags(value: unknown): ManualTags {
  if (!object(value) || !keys(value, ['version', 'tags', 'papers']) || value.version !== '1.0' ||
      !Array.isArray(value.tags) || value.tags.length > 500 || !object(value.papers) || Object.keys(value.papers).length > 10000) {
    throw new Error('标签配置格式无效，需要 version、tags、papers。');
  }
  const ids = new Set<string>(), names = new Set<string>();
  const tags = value.tags.map(tag => {
    if (!object(tag) || !keys(tag, ['id', 'name']) || typeof tag.id !== 'string' ||
        !/^t-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(tag.id) || tag.id.length > 80 ||
        typeof tag.name !== 'string' || !tag.name.trim() || [...tag.name].length > 60 || /[\u0000-\u001f\u007f]/.test(tag.name)) {
      throw new Error('标签名称不能为空、超过 60 字或包含控制字符。');
    }
    const name = tag.name.trim();
    if (ids.has(tag.id) || names.has(name.toLowerCase())) throw new Error('标签重复：名称不区分大小写。');
    ids.add(tag.id); names.add(name.toLowerCase());
    return { id: tag.id, name };
  }).sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const papers = Object.fromEntries(Object.entries(value.papers).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([id, assigned]) => {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || !Array.isArray(assigned) || assigned.length > 50 ||
        assigned.some(t => typeof t !== 'string' || !ids.has(t)) || new Set(assigned).size !== assigned.length) {
      throw new Error('论文标签分配无效，引用了不存在或重复的标签（每篇最多 50 个）。');
    }
    return [id, [...assigned].sort() as string[]];
  }));
  return { version: '1.0', tags, papers };
}
export function assignedIds(config: ManualTags, paperId: string): string[] {
  return Object.hasOwn(config.papers, paperId) ? config.papers[paperId] : [];
}
export function manualTagNames(config: ManualTags, paperId: string): string[] {
  const assigned = new Set(assignedIds(config, paperId));
  return config.tags.filter(t => assigned.has(t.id)).map(t => t.name).sort();
}
