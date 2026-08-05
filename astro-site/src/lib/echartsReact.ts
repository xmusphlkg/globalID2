import EChartsReactModule from 'echarts-for-react/lib/core.js';

// echarts-for-react 3.x is published as CommonJS. Depending on whether Vite
// pre-bundles the module, its default export is either the component itself or
// a { default: Component } namespace object. React 19 rejects the latter as an
// element type, so normalize the interop shape in one place.
const moduleValue = EChartsReactModule as typeof EChartsReactModule & {
  default?: typeof EChartsReactModule;
};

const EChartsReact = moduleValue.default ?? moduleValue;

export default EChartsReact;
