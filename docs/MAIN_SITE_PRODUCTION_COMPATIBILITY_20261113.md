# 2026-11-13 生产兼容性基线与客户端候选

后续状态补充：本文记录的是切换前兼容基线。主题 v1 已于同日发布并在首页、
`/zh/`、`/fr/` 及三语中国国家页验证引用
`https://theme.globalinfectiousdisease.com/theme/compat/v1/theme.css`；Papers v3
已由 `exports/site-research-runs/papers-v3-cutover-20261113/published.json` 记录并
通过三语公开 canary。本文中 `template`/主题关闭描述仅适用于基线快照。

本文记录生产基线、兼容候选和部署后浏览器验收；生产写操作由主任务执行。

## 改造前生产产物

- 公开 `release.json`：`20261112T152513Z-d1d67eca1c36`。
- `astro-site/dist`、`exports/verified-pages-snapshot` 的全量产物摘要均为 `a0b975cbe244bb5b31bb185d054e4a95fbf89e484009ab4f3668775dfc12705f`，与公开身份一致；公开与本地 `release.json` 字节相同。
- 原产物 9,765 文件、6,749 HTML、865,858,447 字节。
- `successful-site-release.json` 的数据输入记录有 14,512 项；当前 5,473 项 stat 变化、11 项缺失，变化集中于 Research 与 Situation international。因此本次不接受当前数据重新渲染的任何 HTML，也不把当前输入冒充已发布数据快照。
- 证据：`exports/production-browser-baseline-20261113/retained-artifact-verification.json`。

## 浏览器基线

`exports/production-browser-baseline-20261113-complete/` 包含：首页、JP/CN 国家页、Influenza 疾病页、Situation、Research 目录、Downloads，三语与 390/1280 宽度共 42 场景。初始导航与实际语言切换最终响应全部 200；保留文本、链接、SEO、样式、图表、截图和 245 份响应归档。6 个 JP 下载对话框场景验证打开、12 个原始文件链接、JSON 格式筛选及关闭；未下载完整数据文件。

生产旧客户端在 JP 桌面语言切换重现一次 zrender `layerStack` 异常，属于候选前已有缺陷。背景同 URL 503 请求与最终导航分开记录，不能把背景响应解释为语言切换失败。

初次截图中部分懒加载区域仍处于 Loading；严格候选对照另外使用同一归档、两遍动态全页滚动、等待加载完成，对旧产物和候选分别回放，不将这些加载时序差异计为正文修改。

## Papers Worker 基线

`exports/papers-v2-production-before-20261113/` 保存同一线上精选文章 × 三语 × 主域/www/papers 共 9 个 URL 的原始 HTML、正文/链接/SEO/资源/JSON-LD 合约及响应头。全部 `X-GIDS-Paper-Mode=template`，研究发布身份保持上述 release。

部署后执行：

```bash
python3 scripts/capture_papers_compatibility.py \
  --output exports/papers-v2-production-after-20261113 \
  --compare exports/papers-v2-production-before-20261113
```

脚本比较正文、链接、canonical/hreflang、description/OG、JS/CSS 资源、JSON-LD 以及三个 X-GIDS 身份头；仅规范化 Cloudflare 注入的邮件保护、探针和空链接。发生差异退出非零。

## 最终兼容候选：保留原数据链路

用户明确决定保留 Bot Fight Mode、放弃独立 CDN。本轮新增的前端 CDN 读取/凭据/schema v2/回退逻辑及其测试已全部撤回：`surveillance-data.ts`、`situation-data.ts`、`surveillance-data.test.ts` 精确恢复 HEAD。原有数据入口配置能力保留，构建时使用其原默认值。测试工具中的独立 CDN 路径重写亦已删除。主题和国家模板功能保留；本段主题入口关闭仅适用于本兼容基线，主题 v1 随后的生产切换见顶部补充。

独立构建 `exports/compat-client-build-20261113/` 使用撤回后的源码，Node 22.23.4 / Astro 7.4.7，6 GiB 堆上限，耗时 1 分 16 秒。257 个临时输出全部弃用，仅接受 `_astro`。产出的 `surveillance-sources.Bzwt9QOP.js` 和 `situation-data.BsEBAbPj.js` 与原生产文件逐字节完全相同，因此原数据 URL、凭据和读取行为均保持不变。

最终候选路径为 `exports/compat-client-candidate-20261113`。仍由 `assemble-client-assets.mjs` 以唯一逻辑文件名映射 HTML 直接引用的 JS 入口；没有候选、多候选、不可逆映射或不可变资源碰撞均拒绝。新编译的正文、SSR、props、SEO、CSS 链接均不进入候选，原有资源、Pages Worker、路由和响应头逐字节保留。唯一实际客户端修复为 SafeCanvasPainter 销毁后终止渐进绘图回调。

候选的 `release.json` 在验收阶段保留原值和原 `built_at`，避免改变页脚展示日期。主任务负责生成真实新发布身份、部署与更新标记；沿用原生产数据快照记录，不能将已漂移的当前源数据冒充生产快照。任何此前包含独立 CDN 客户端的候选均已作废，不得部署。

原数据读取器与 SafeCanvasPainter 的 10 项测试通过。最终候选已用同一冻结数据归档对照首页、JP 国家页、Situation 三语双视口的 18 个场景，18/18 合约通过，正文/链接/SEO/计算样式/图表与页面尺寸完全一致，语言切换均 200，JavaScript/HTTP 错误为 0；所有数据请求仍来自原主域，独立 CDN 请求为 0。下载对话框另有 6/6 对照通过。候选 9,798 文件、866,771,311 字节；6,749 HTML 中只有 1,767 页的 4,086 个 JS 引用改变，其余字节恒等。最终详情见同名 `.receipt.json`。

## 校验脚本修复

`countryBaselineDigest` 曾按 JS 字符串排序，生产 Python 则按 Path 组件排序，导致 `a-b.html` 与 `a/z.html` 顺序不同、误拒绝合法基线。已修复为组件排序，增加 Python 固定向量回归；5 个国家构建测试通过。客户端组装的独立测试覆盖 HTML/资源保留、重复逻辑入口拒绝、不可变资源碰撞拒绝和损坏基线拒绝，2 个测试通过。

## 真实生产验收

主任务已部署 `20261113T040327Z-d1d67eca1c36`，Pages production deployment 为 `dfc0610b.globalidv2.pages.dev`。随后对真实主域串行检查 18 个首页/JP/Situation 三语双视口场景，全部通过：正文、链接、SEO、计算样式、图表和页面尺寸与已验候选完全一致；初始导航及实际语言切换全部 HTTP 200；JavaScript 错误、缺失核心资源、独立 CDN 请求均为 0。

Research 三语目录及一篇英文文章的另外 4 个浏览器场景全部通过，目录图表和文章资源正常。Research 保留 `20261112T152513Z-d1d67eca1c36` 发布身份，文章仍为 `template` 模式。原有语言预取 HTTP 503 与发布前基线逐项相同，实际导航均成功，没有将这些既有背景响应误计为新发布故障。

真实生产总验收记录：`exports/production-compatible-after-20261113.browser/production-verification.json`；Research 详细记录：`exports/production-research-after-20261113.browser/verification.json`。验收过程没有修改任何生产配置。
