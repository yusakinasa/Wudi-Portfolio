# Wudi — personal site

An intentionally small, content-driven personal site built with Astro and TypeScript. It is fully static and ready for GitHub Pages.

## Local development

```bash
npm install
npm run dev
```

Useful checks:

```bash
npm run check
npm run build
npm run preview
```

## Content workflow

- **Projects:** edit [`data/projects.json`](./data/projects.json). Add an object with `name`, `description`, `category`, `tags`, and links; `featured`, `date`, `status`, and `demo` are optional.
- **Notes:** add a Markdown file under [`src/content/notes`](./src/content/notes). Include `title`, `description`, `date`, `category`, and `tags` in frontmatter. Nested folders become nested URLs.
- **Experience posts:** add a Markdown file under [`src/content/experiences`](./src/content/experiences) with the same core frontmatter. Keep article images beside the content and reference them with relative Markdown paths.
- **Site details:** edit [`data/site.json`](./data/site.json).
- **Navigation:** edit [`data/navigation.json`](./data/navigation.json). Set `enabled` to `true` for a new section, then add its Astro page under `src/pages/`.

The collection schema lives in `src/content.config.ts`, so invalid note metadata is caught by `npm run check` or the production build.

## Finances

`/finances/` shows daily expenses from `src/content/finances/finance.xlsx`.
Edit the workbook, save it, then run `npm run build` (or restart `npm run dev`).
The `predev`, `precheck`, and `prebuild` hooks automatically import the workbook;
`npm run finances:sync` runs the import on its own. Python 3.10+ is required locally;
GitHub Actions installs Python automatically. No additional Python packages are needed.

Each month table must use contiguous headers `日期 / 餐饮 / 交通 / 其他 / 总计 / 备注`.
You can add tables side by side, below existing tables, or on new sheets. Keep
dates unique, skip merged month titles, and leave unused future days empty.
Amounts are RMB expenses; blank category cells remain blank and explicit zeroes
remain zero. Calculations use integer fen and recompute totals from category
amounts. Cached summary/percentage/average rows are not imported. Formula amounts
must be recalculated and saved in Excel; inconsistent manually entered daily
totals, duplicate dates, and invalid amounts stop the build with an error.

The page includes month/category filters, daily trends, monthly coverage,
average/median/peak spending, searchable sortable daily records, and CSV export.
Income is unrecorded, so balances and savings rates are not calculated. Settings
and category labels live in `data/finances.json`. Generated JSON is ignored by
Git and regenerated at build time. The deployed static page includes amounts
and notes; this is a publicly visible ledger, not a private accounting app.

## Paper Library

`/papers/` adds a searchable paper library and static per-paper detail pages, using
validated JSON in `data/papers/`. Offline PDF/Zotero ingest uses a fresh, isolated
Codex process per paper. Original PDFs and intermediate outputs are not published.
Evidence checking stays in the pipeline/JSON, without evidence sections or jump chips in the UI.
Paper tags are manually curated: run `npm run dev`, use “设置标签” on a paper and
`/papers/tags/` to manage names. The loopback-only editor saves directly to
`data/paper-tags.json` and updates the index; reanalysis never replaces these tags.
There is no online tag editing or browser-only persistence.
See [Paper Library guide](./docs/papers.md) for setup, ingest/update commands,
cache configuration, schema, extension points, and tests.

## GitHub Pages

`.github/workflows/deploy.yml` builds and deploys on pushes to `main`. `astro.config.mjs` derives the project-page base path from `GITHUB_REPOSITORY`; for a user site named `<user>.github.io`, it correctly uses the root path.

In the repository settings, set **Pages → Source** to **GitHub Actions**. The first deployment may need the workflow to finish before the Pages URL appears.
