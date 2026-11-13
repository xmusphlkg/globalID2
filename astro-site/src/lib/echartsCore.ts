import * as echarts from 'echarts/core';

import { registerPainter } from 'zrender/lib/zrender.js';
import SafeCanvasPainter from './safeCanvasPainter';

// Keep only the renderer in the shared registry. Components such as data zoom,
// visual maps and accessibility decals are feature-specific and sizeable.
// Each feature registry below imports exactly the option types it renders.
registerPainter('canvas', SafeCanvasPainter);

export default echarts;
