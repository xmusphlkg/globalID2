/** Situation notices have an independent publication, pinned for one page. */
import { createSurveillanceDataClient } from './surveillance-data.ts';
import { mainSiteBase } from './papers.ts';

export const DEFAULT_SITUATION_MANIFEST_URL = '/site-data/situation/bootstrap.json';
const configured = import.meta.env?.PUBLIC_GIDS_SITUATION_MANIFEST_URL || DEFAULT_SITUATION_MANIFEST_URL;
export const situationData = createSurveillanceDataClient({
  manifestUrl: configured.startsWith('/') ? `${mainSiteBase()}${configured}` : configured,
  logicalRoot: '/situation/',
});
