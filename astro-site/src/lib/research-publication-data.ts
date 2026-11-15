/** Research JSON has an independent publication, pinned for one page. */
import { createSurveillanceDataClient } from './surveillance-data.ts';
import { mainSiteBase } from './papers.ts';

export const DEFAULT_RESEARCH_MANIFEST_URL = '/site-data/research-publication/bootstrap.json';
const configured = import.meta.env?.PUBLIC_GIDS_RESEARCH_MANIFEST_URL || DEFAULT_RESEARCH_MANIFEST_URL;
export const researchPublicationData = createSurveillanceDataClient({
  manifestUrl: configured.startsWith('/') ? `${mainSiteBase()}${configured}` : configured,
  logicalRoot: '/site-data/research-publication/',
});
