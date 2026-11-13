# 国家模板隔离构建

2026-11-13 已实现并运行真实构建；未部署生产。

`astro-site/scripts/build-country-template.mjs` 只把国家首页的 EN、ZH、FR 三个路由入口交给独立 Astro/Vite workspace，复用现有 `shouldBuildPage` 限定国家范围。文章、报告、首页、疾病和目录路由不进入该次路由构建，因此不是单纯改变缓存 key。

```bash
node --experimental-strip-types astro-site/scripts/build-country-template.mjs \
  --baseline /absolute/path/to/astro-site/dist \
  --output /absolute/path/to/exports/country-template-stage-new \
  --countries all
```

可用 `--countries jp,ca` 生成代表页预览；真正的通用国家模板发布必须使用 `all`，否则只更新部分国家会遗漏受影响页面。输出路径必须不存在，且与基线和 workspace 分离。原 dist 保持不变。

构建过程冻结代码/工具链指纹、元数据、国家目录事实及基线文件身份，完成后再次检查。国家导航复用已有经过验证的轻量目录事实，热缓存无需解析历史观测 JSON。全新的缓存仍需建立目录事实，这部分成本不会伪装成零。

候选复制已有 dist 后，仅替换选中国家的三语 HTML，并合并该次编译产生的 `_astro` 资源。已有同名资源若字节不同则拒绝；保留旧资源供未更新页面与旧标签页使用。其他 HTML 的 SHA-256 必须保持不变，新的国家 HTML 引用的 `_astro` 文件必须存在。缺少基线路由、意外渲染其他路线或资源损坏均拒绝生成有效收据。

两份收据分别位于 `<candidate>.receipt.json` 和 `<candidate>/country-template-receipt.json`，记录国家、页面列表、源码摘要、正文渲染数、国家 HTML 数、模板编译数、保留 HTML 摘要、资源数量和分阶段时间。候选保留原 `release.json`；接入现有发布任务时，由原管线写入任务身份和执行部署、公开验证，不能直接把构建成功视为发布成功。

## 已执行的本地测量

以下都是单次样本，不是性能承诺或冷热缓存中位数。基线含 6,749 个 HTML 文件，约 872 MB 全部产物。

| 场景 | 国家 HTML | 文章/报告正文渲染 | 保留其他 HTML | 编译调用耗时 | 候选组装耗时 | 总耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| JP 三语预览 | 3 | 0 / 0 | 6,746 | 10.22 s | 12.91 s | 34.12 s |
| 全部 295 个国家/辖区 | 885 | 0 / 0 | 5,864 | 11.90 s | 9.90 s | 27.13 s |

两次均为目录缓存 295 hits、0 misses、0 data reads，新增 19 个资源。Astro 自身报告分别为 3 页 / 8.74 s 和 885 页 / 10.66 s；表中的编译调用耗时还包含命令启动。日志保存在 `/tmp/gids-country-template-build.log`、`/tmp/gids-country-template-all.log`，候选位于 `exports/country-template-stage-pilot`、`exports/country-template-stage-all`。

当前仍需复制并核验完整静态基线，因此组装与部署成本会随现有产物体积增长。已经消除的是无关文章/报告渲染与无关路由的 Vite 模块图编译，不能将其描述为所有成本均与全站规模无关。

## 适用边界

此路径只适用于既有国家首页模板族。新国家路由需要目录/sitemap 同步，国家疾病页属于其他模板族，不能复用这一范围。共享布局、全局样式、客户端协议或多个模板族变化仍需由分类器扩大影响范围，不能强行限制成国家发布。资源和主题契约保持原有兼容要求。

单元测试命令：

```bash
node --experimental-strip-types --test astro-site/scripts/build-country-template.test.mjs
```

测试覆盖精确三语范围、无关 HTML/旧资源/release.json 保留、新路由拒绝、意外路由拒绝、资产冲突和缺失引用。本地代表页与语言切换浏览器验证已完成，证据见下文；生产部署后仍需验证真实域名、CDN/Worker 路由和公开页面。

## 计划执行的资格与基线保护

现有发布任务已接入国家模板构建分支，只有相对已知发布源码的变更完全落在三条国家首页 `.astro` 路由，且 `exports/publicly-verified-site-release.json` 提供对应版本的真实公开验证证明时才可选择；不能仅凭目录名 `verified-pages-snapshot` 推断基线已公开验证。共享组件/JS/CSS、工具链、多模板族变更，以及发布源码未知、dirty baseline、盘点失败/不稳定或符号链接等情况均不能进入该快速路径。

计划冻结 verified Pages snapshot 的完整产物摘要与 `release.json` 原始 SHA；排队执行、构建开始均重新验证。CLI 对应参数为 `--baseline-digest` 与 `--baseline-release-sha256`。完整产物摘要沿用原发布器算法（按相对路径排序，排除 release.json，混合路径与各文件 SHA-256），不是仅比较可复用的 release ID。构建期间还检查所有基线文件身份未改变。

## 浏览器回归中发现的图表竞态

原始全国家候选在全页滚动触发延迟图表后，JP 法文桌面页出现 `Cannot read properties of null (reading 'layerStack')`。保留失败证据于 `exports/country-template-stage-all.browser/verification.json` 与 `exports/country-template-stage-all.browser-debug/verification.json`，后者含完整堆栈。

原因是锁定的 zrender 6.1.0 CanvasPainter 在 dispose 清空图层状态时，没有作废自己的渐进绘制 RAF continuation。新增 `safeCanvasPainter.ts` 通过现有 redraw-generation 防护使旧回调失效，再调用原 dispose；不屏蔽错误、不关闭渐进绘制。回归测试调用真实上游 `_paintList` 延迟路径，且已加入 `npm run test:charts`。

这是共享 renderer 修复，因此整体源码变更必须按更广的模板/代码发布处理，不能用国家范围覆盖全站。修后代表页候选 `exports/country-template-stage-chart-fix` 仅重建 JP/CN 三语 6 页，其他页面保留原基线；它用于定点验证，不代表所有页面已切换 renderer，也未部署生产。

修后浏览器矩阵已全部通过：JP/CN × EN/ZH/FR × 390/1280，共 12 例。测试全页滚动触发延迟图表，实际操作手机菜单和桌面语言菜单；核验主导航目标、语言目标、canonical、页面语言、无横向溢出，以及静态资源和脚本错误。每例实际读取 24–26 个静态资源、4–10 个冻结 JSON 请求，累计缺失资源 0、JavaScript 错误 0。

最终证据为 `exports/country-template-stage-chart-fix.browser/verification.json` 和同目录 12 张截图。所有页面资源来自本地候选，JSON 来自冻结的现有导出；外网全部拦截（实际出现的是 stats 分析请求），因此这不是生产 CDN/CORS 或请求额度验收。重现命令：

```bash
node astro-site/scripts/verify-country-template.mjs \
  exports/country-template-stage-chart-fix \
  exports/country-template-stage-chart-fix.browser
```

兼容 shim 依赖 zrender 的现有 generation fence，故将 `zrender` 显式锁定为 `6.1.0`；升级该依赖时必须保留真实延迟回调与浏览器回归，不应无检查移除或沿用内部字段兼容代码。
