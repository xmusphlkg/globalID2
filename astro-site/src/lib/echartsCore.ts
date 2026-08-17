import * as echarts from 'echarts/core';

import { CanvasRenderer } from 'echarts/renderers';

// Keep only the renderer in the shared registry. Components such as data zoom,
// visual maps and accessibility decals are feature-specific and sizeable.
// Each feature registry below imports exactly the option types it renders.
echarts.use([
  CanvasRenderer,
]);

export default echarts;
