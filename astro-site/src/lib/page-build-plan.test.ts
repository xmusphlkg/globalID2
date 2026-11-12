import assert from 'node:assert/strict';
import { test } from 'node:test';
import { independentResearchPages, shouldBuildPage } from './page-build-plan.ts';

test('only main-site builds skip independently published research pages', () => {
  const previousFlag = process.env.GIDS_RESEARCH_PUBLICATION_ENABLED;
  const previousRoot = process.env.GIDS_RESEARCH_BUILD_DIR;
  try {
    process.env.GIDS_RESEARCH_PUBLICATION_ENABLED = '1';
    delete process.env.GIDS_RESEARCH_BUILD_DIR;
    assert.equal(independentResearchPages(), true);
    assert.equal(shouldBuildPage('research:diseases:influenza'), false);
    assert.equal(shouldBuildPage('country:us'), true);
    process.env.GIDS_RESEARCH_BUILD_DIR = '/tmp/isolated-research';
    assert.equal(independentResearchPages(), false);
    assert.equal(shouldBuildPage('research:diseases:influenza'), true);
  } finally {
    if (previousFlag === undefined) delete process.env.GIDS_RESEARCH_PUBLICATION_ENABLED;
    else process.env.GIDS_RESEARCH_PUBLICATION_ENABLED = previousFlag;
    if (previousRoot === undefined) delete process.env.GIDS_RESEARCH_BUILD_DIR;
    else process.env.GIDS_RESEARCH_BUILD_DIR = previousRoot;
  }
});
