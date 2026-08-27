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
- **Site details:** edit [`data/site.json`](./data/site.json).
- **Navigation:** edit [`data/navigation.json`](./data/navigation.json). Set `enabled` to `true` for a new section, then add its Astro page under `src/pages/`.

The collection schema lives in `src/content.config.ts`, so invalid note metadata is caught by `npm run check` or the production build.

## GitHub Pages

`.github/workflows/deploy.yml` builds and deploys on pushes to `main`. `astro.config.mjs` derives the project-page base path from `GITHUB_REPOSITORY`; for a user site named `<user>.github.io`, it correctly uses the root path.

In the repository settings, set **Pages → Source** to **GitHub Actions**. The first deployment may need the workflow to finish before the Pages URL appears.
