import assert from 'node:assert/strict';
import test from 'node:test';
import SafeCanvasPainter from './safeCanvasPainter.ts';
import CanvasPainter from 'zrender/lib/canvas/Painter.js';

test('a queued upstream progressive paint exits after the painter is disposed', () => {
  // Use the real upstream _paintList implementation. The fixture supplies only
  // the DOM and generation needed before disposal; no drawing API is mocked.
  const painter = Object.create(SafeCanvasPainter.prototype);
  painter.root = { innerHTML: '<canvas></canvas>' };
  painter._i = { layerStack: [] };
  painter._redrawId = 0.75;
  const scheduledFrame = () => painter._paintList([], [], 0.75);
  painter.dispose();
  assert.equal(painter._i, null);
  assert.equal(painter.root, null);
  assert.doesNotThrow(scheduledFrame);
});


test('the public null refresh option delegates as the canvas default', () => {
  const painter = Object.create(SafeCanvasPainter.prototype);
  const descriptor = Object.getOwnPropertyDescriptor(CanvasPainter.prototype, 'refresh')!;
  const calls: unknown[] = [];
  Object.defineProperty(CanvasPainter.prototype, 'refresh', { ...descriptor,
    value: function (this: CanvasPainter, options: unknown) { calls.push(options); return this; } });
  try {
    assert.equal(painter.refresh(null), painter);
    assert.equal(painter.refresh(true), painter);
    assert.deepEqual(calls, [undefined, true]);
  } finally { Object.defineProperty(CanvasPainter.prototype, 'refresh', descriptor); }
});
