import { readResearchIndex } from './generated-site-data.ts';
import { buildResearchFeedDefinitions, createResearchFeedSelector, type ResearchFeedData } from './research-feed';
import { memoizeForBuild } from './build-memo';

export const researchFeedCatalogue = memoizeForBuild(() => {
  const data = readResearchIndex() as ResearchFeedData;
  const select = createResearchFeedSelector(data);
  return { definitions: buildResearchFeedDefinitions(data, select), select };
});
