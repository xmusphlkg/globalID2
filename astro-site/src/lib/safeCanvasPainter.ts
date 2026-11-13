import CanvasPainter from 'zrender/lib/canvas/Painter.js';

/** zrender's progressive canvas loop schedules its own animation frame outside
 * Animation.stop(). In the pinned zrender version, dispose() clears layer state
 * without invalidating that loop's redraw ID. A queued frame can then paint a
 * disposed chart during a React remount or language navigation.
 */
export default class SafeCanvasPainter extends CanvasPainter {
  // The public PainterBase contract accepts null, while CanvasPainter narrows
  // its own signature. Normalize the public sentinel before delegating.
  override refresh(options?: Parameters<CanvasPainter['refresh']>[0] | null): this {
    super.refresh(options ?? undefined);
    return this;
  }

  override dispose(): void {
    // _paintList already fences every continuation by this generation. NaN
    // never equals a captured redraw ID, including another disposal sentinel.
    // Keep this compatibility shim covered against the actual upstream loop.
    Reflect.set(this, '_redrawId', Number.NaN);
    super.dispose();
  }
}
