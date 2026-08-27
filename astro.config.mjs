import { defineConfig } from 'astro/config';

const repository = process.env.GITHUB_REPOSITORY?.split('/')[1] ?? 'Portfolio';
const owner = process.env.GITHUB_REPOSITORY_OWNER ?? 'wudi';
const isGithubPages = process.env.GITHUB_ACTIONS === 'true';
const isUserSite = repository.toLowerCase() === `${owner.toLowerCase()}.github.io`;

export default defineConfig({
  site: `https://${owner}.github.io`,
  // GitHub project pages need the repository name as a base path. User pages do not.
  base: isGithubPages && !isUserSite ? `/${repository}` : '',
  output: 'static',
  build: {
    format: 'directory'
  }
});
