import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const loaderSource = readFileSync(
  new URL('../public/scripts/analytics.js', import.meta.url),
  'utf8',
);

function executeLoader(measurementId) {
  const appendedScripts = [];
  const documentListeners = new Map();

  class FakeElement {}
  class FakeHtmlScriptElement extends FakeElement {
    constructor() {
      super();
      this.dataset = { measurementId };
    }
  }

  const window = {
    addEventListener() {},
    requestIdleCallback(callback) {
      callback();
    },
    setTimeout(callback) {
      callback();
    },
  };
  const document = {
    currentScript: new FakeHtmlScriptElement(),
    readyState: 'complete',
    addEventListener(name, callback) {
      documentListeners.set(name, callback);
    },
    createElement() {
      return {};
    },
    head: {
      appendChild(script) {
        appendedScripts.push(script);
      },
    },
  };

  vm.runInNewContext(loaderSource, {
    document,
    Element: FakeElement,
    HTMLScriptElement: FakeHtmlScriptElement,
    window,
  });

  return { appendedScripts, documentListeners, window };
}

test('queues GA configuration and defers the remote analytics script', () => {
  const { appendedScripts, documentListeners, window } = executeLoader('g-test1234');

  assert.equal(typeof window.gtag, 'function');
  assert.equal(window.dataLayer.length, 2);
  assert.deepEqual(Array.from(window.dataLayer[1]), ['config', 'G-TEST1234']);
  assert.equal(documentListeners.has('click'), true);
  assert.equal(appendedScripts.length, 1);
  assert.equal(
    appendedScripts[0].src,
    'https://www.googletagmanager.com/gtag/js?id=G-TEST1234',
  );
  assert.equal(appendedScripts[0].async, true);
});

test('ignores invalid measurement identifiers', () => {
  const { appendedScripts, documentListeners, window } = executeLoader('not-a-ga-id');

  assert.equal(window.gtag, undefined);
  assert.equal(window.dataLayer, undefined);
  assert.equal(documentListeners.size, 0);
  assert.equal(appendedScripts.length, 0);
});
