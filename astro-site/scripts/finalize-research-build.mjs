import { finalizeResearchBuild } from './research-build-cache.mjs';
import { finalizeSitePages } from './page-build-cache.mjs';

if (process.env.GIDS_RESEARCH_PUBLICATION_ENABLED !== '1') finalizeResearchBuild();
finalizeSitePages();
