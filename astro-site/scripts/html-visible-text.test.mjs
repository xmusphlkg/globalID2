import assert from 'node:assert/strict';
import test from 'node:test';
import {visibleText,visibleTranslationPlaceholders,pendingTranslationMarkers} from './html-visible-text.mjs';
test('fast placeholder scan agrees with full visible-text extraction',()=>{
  for(const html of [
    '<p>Texte traduit.</p>', '<p>Translation pending.</p>',
    '<p>Traduction <b>française</b> en attente</p>',
    '<p>Translation\n pending.</p>', '<script>Translation pending.</script>',
    '<style>/* Traduction française en attente */</style>',
    '<p title="Translation pending.">Publié</p>', '<p>Trans<b>lation</b> pending.</p>',
  ]) assert.deepEqual(visibleTranslationPlaceholders(html),pendingTranslationMarkers.filter(m=>visibleText(html).includes(m)));
});
