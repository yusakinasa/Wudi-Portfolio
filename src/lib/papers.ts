/** Build-only file storage. Never ship full analyses to the library browser. */
import fs from 'node:fs';
import path from 'node:path';
import Ajv from 'ajv';
import schema from '../../schemas/paper.schema.json';
import { createHash } from 'node:crypto';
import { validateManualTags, manualTagNames, type TagSnapshot } from './paper-tags';

export interface Evidence { id: string; section: string; page: number | null; content: string }
export interface Claim { text: string; evidence_ids: string[] }
export interface RelatedPaper {
  title: string; paper_id: string | null; relation_type: string; description: string;
  source: string; doi: string; arxiv_id: string; url: string; evidence_ids: string[];
}
export interface Paper {
  schema_version: string; id: string;
  metadata: { title: string; short_title: string; authors: string[]; year: number | null; venue: string;
    doi: string; arxiv_id: string; paper_url: string; pdf_url: string; project_url: string; code_url: string;
    zotero: { item_key: string; library_id: string; collections: string[] } };
  overview: { abstract: string | null; one_sentence: string; research_problem: string | null;
    motivation: string | null; core_idea: string | null; method_summary: string | null; evidence_ids: string[] };
  contributions: { id: string; title: string; description: string; evidence_ids: string[] }[];
  method: { inputs: string[]; outputs: string[]; components: { name: string; description: string; evidence_ids: string[] }[];
    pipeline: string[]; details: string | null; evidence_ids: string[] };
  experiments: { datasets: string[]; benchmarks: string[]; metrics: string[]; baselines: string[];
    main_results: Claim[]; evidence_ids: string[] };
  limitations: Claim[]; interpretation: { summary: string | null; open_questions: string[] };
  evidence: Evidence[]; related_papers: RelatedPaper[];
  figures: { id: string; caption: string; description: string; page: number | null; path: string; type: string }[];
  tags: string[]; analysis_meta: { analyzer: string; analyzed_at: string; schema_version: string;
    prompt_version: string; source_hash: string; parser_version: string };
}
export interface PaperSummary {
  id: string; title: string; short_title: string; authors: string[]; year: number | null; venue: string;
  doi: string; arxiv_id: string; tags: string[]; one_sentence: string; thumbnail: string; updated_at: string;
}

const root = process.cwd();
// Override is build-only, useful for fixture verification without publishing demo papers.
const directory = path.resolve(root, process.env.PAPER_DATA_DIR || 'data/papers');
export function paperTagSnapshot(): TagSnapshot {
  const filename = path.join(path.dirname(directory), 'paper-tags.json');
  const content = fs.existsSync(filename) ? fs.readFileSync(filename, 'utf8') : null;
  return { config: validateManualTags(content ? JSON.parse(content) : { version: '1.0', tags: [], papers: {} }),
    revision: content === null ? 'missing' : createHash('sha256').update(content).digest('hex') };
}
const validateSchema = new Ajv({ allErrors: true }).compile<Paper>(schema);
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return /^https?:$/.test(url.protocol) && !url.username && !url.password &&
      !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) && !/[\s\\\u0000-\u001f]/.test(value)
      ? url.href : null;
  } catch { return null; }
}
const doiUrl = (doi: string) => doi ? safeExternalUrl(`https://doi.org/${doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '')}`) : null;
const arxivUrl = (id: string) => id ? safeExternalUrl(`https://arxiv.org/abs/${id}`) : null;
export function metadataLinks(paper: Paper) {
  const m = paper.metadata;
  return [['Paper', safeExternalUrl(m.paper_url)], ['DOI', doiUrl(m.doi)], ['arXiv', arxivUrl(m.arxiv_id)],
    ['PDF ↗', safeExternalUrl(m.pdf_url)], ['Project', safeExternalUrl(m.project_url)], ['Code', safeExternalUrl(m.code_url)]]
    .filter((entry): entry is [string, string] => Boolean(entry[1]));
}
export function assetUrl(value: string, base = ''): string | null {
  return /^\/paper-assets\/[a-z0-9-]+\/[a-zA-Z0-9_-]+\.webp$/.test(value) ? `${base}${value}` : null;
}
function checkPaper(value: unknown, filename: string): Paper {
  if (!validateSchema(value)) throw new Error(`Invalid paper ${filename}: ${JSON.stringify(validateSchema.errors)}`);
  const paper = value;
  if (filename !== `${paper.id}.json` || !slugPattern.test(paper.id) || ['index', 'tags'].includes(paper.id)) throw new Error('Paper filename/ID mismatch or reserved ID');
  if (/(?:\/Users\/|\/home\/|file:\/\/|[A-Za-z]:\\Users\\)/.test(JSON.stringify(paper))) throw new Error('Private path in paper data');
  const ids = paper.evidence.map(e => e.id);
  if (new Set(ids).size !== ids.length) throw new Error('Duplicate evidence IDs');
  const inspect = (object: unknown): void => {
    if (!object || typeof object !== 'object') return;
    for (const [key, item] of Object.entries(object)) {
      if (key === 'evidence_ids' && (item as string[]).some(id => !ids.includes(id))) throw new Error('Unknown evidence reference');
      inspect(item);
    }
  };
  inspect(paper);
  for (const claim of [...paper.experiments.main_results, ...paper.limitations]) {
    if (!claim.evidence_ids.length) throw new Error('Paper results/limitations require evidence');
  }
  for (const related of paper.related_papers) {
    if (related.source !== 'external_metadata' && !related.evidence_ids.length) throw new Error('Paper-derived relation requires evidence');
  }
  for (const group of [paper.contributions, paper.figures]) {
    if (new Set(group.map(item => item.id)).size !== group.length) throw new Error('Duplicate contribution/figure IDs');
  }
  for (const f of paper.figures) {
    if (!assetUrl(f.path) || !f.path.startsWith(`/paper-assets/${paper.id}/`) ||
        !fs.existsSync(path.join(root, 'public', f.path.slice(1)))) throw new Error(`Invalid/missing figure: ${paper.id}`);
  }
  for (const key of ['paper_url', 'pdf_url', 'project_url', 'code_url'] as const) {
    if (paper.metadata[key] && !safeExternalUrl(paper.metadata[key])) throw new Error('Unsafe paper URL');
  }
  for (const p of paper.related_papers) if (p.url && !safeExternalUrl(p.url)) throw new Error('Unsafe related URL');
  return paper;
}
export function loadPapers(): Paper[] {
  if (fs.existsSync(path.join(directory, '.transaction.json'))) throw new Error('Interrupted ingest: run paper rebuild-index to recover before building');
  return fs.readdirSync(directory).filter(name => name.endsWith('.json') && name !== 'index.json' && !name.startsWith('.'))
    .sort().map(name => checkPaper(JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8')), name))
    .sort((a, b) => (b.metadata.year ?? 0) - (a.metadata.year ?? 0) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}
export function paperIndex(papers = loadPapers()): { schema_version: string; papers: PaperSummary[] } {
  const { config } = paperTagSnapshot();
  return { schema_version: '1.0', papers: papers.map(p => ({ id: p.id,
    ...Object.fromEntries(['title', 'short_title', 'authors', 'year', 'venue', 'doi', 'arxiv_id'].map(key => [key, p.metadata[key as keyof Paper['metadata']]])) as Pick<PaperSummary, 'title' | 'short_title' | 'authors' | 'year' | 'venue' | 'doi' | 'arxiv_id'>,
    tags: manualTagNames(config, p.id), one_sentence: p.overview.one_sentence,
    thumbnail: p.figures.find(f => ['concept_overview', 'architecture', 'method_overview'].includes(f.type))?.path ?? '', updated_at: p.analysis_meta.analyzed_at })) };
}
export function relatedLink(related: RelatedPaper, rows: PaperSummary[], base = ''): { href: string; internal: boolean } | null {
  const title = (value: string) => value.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  const matching = rows.filter(row => (related.doi && doiUrl(row.doi) === doiUrl(related.doi)) ||
    (related.arxiv_id && row.arxiv_id.replace(/v\d+$/, '') === related.arxiv_id.replace(/v\d+$/, '')) || title(row.title) === title(related.title));
  const id = rows.some(row => row.id === related.paper_id) ? related.paper_id : matching.length === 1 ? matching[0].id : null;
  if (id) return { href: `${base}/papers/${id}/`, internal: true };
  const external = doiUrl(related.doi) || arxivUrl(related.arxiv_id) || safeExternalUrl(related.url);
  return external ? { href: external, internal: false } : null;
}
