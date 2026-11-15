# 主站分层发布实施记录

日期：2026-11-13。本文记录保留的实施范围；已撤销的独立 JSON CDN 草案已移除。
本次使用三个 subagent 并行完成发布后端、国家模板和 Papers 协议改造，
主流程负责控制台、预览、文献实时控制、集成检查与文档。
2026-11-13 最新范围：按用户要求撤销本轮 JSON CDN。恢复原同源数据路径，删除新增镜像代码与配置，并清理试建的公开桶、自定义域名和缓存规则。Bot Fight Mode 保持启用。其余发布与兼容改造继续生产上线，实际部署结果见 [生产部署记录](../releases/MAIN_SITE_PRODUCTION_DEPLOYMENT_20261113.md)。

## 已交付与上线边界

| 模块 | 已实现 | 上线或后续验收条件 |
| --- | --- | --- |
| 发布计划与执行 | capabilities、源/配置摘要、远端基线、15 分钟计划、幂等 runs、任务锁、冲突拒绝、阶段收据 | 内容字节在既有导出快照阶段冻结；规划时冻结范围，不是完整数据库快照 |
| 控制面板 | 内容/主题/模板/全量范围、影响范围、计划执行、任务状态、收据、主题预览/回滚、中英文、旧服务兼容 | 无法取得证据的数量或验证状态保持未知 |
| 文献实时控制 | literature 分类、pending、实时发布/暂停/恢复、独立控制文件、研究指针/绑定目录/分片及 JSON 样本探测 | 监控明确是抽样，不代表所有文章 HTML 已验证；须启用定时采集才能持续刷新 |
| 独立主题 | 严格 v1 token、零 HTML 候选、冻结执行、条件提交、公开验证、私有三语预览、已验证版本回滚 | 已切换；`globalid-theme` 与兼容 CSS 入口 ready，主站六个首页/国家模板已部署并引用不可变 v1 URL |
| 国家模板 | 独立三语 Astro 入口、完整基线局部替换、资源合并、无关 HTML 保留、接入原 Pages 发布器 | 只适用于既有国家首页路由的独立变化；共享布局等变更回退原流程 |
| Papers v3 | 正文记录/模板/资源独立摘要、v3 渲染、单篇正文与模板候选、单一研究指针发布与公开 canary | 已切换；初次 8,352 篇 × 三语候选是历史验收，后续普通文献与全站发布均要求 v3，旧 v2 对象保留但不作为回滚路径 |
| 运行观测 | 脱敏只读账户盘点、集中缓存采样、能力/额度未知态、阶段成本收据 | 套餐与缓存规则 API 权限不足；缺失账单额度、预测及完整冷热性能基准 |

旧 `full/reports_only/code_only` 入口保留。主题和内容的计划执行复用既有 EXPORT_DATA
任务系统；同一幂等键返回同一任务，不能把另一个计划套到已用幂等键上。
API 接受旧式发布参数或完整的计划参数组合，禁止两种方式混用。

## API 与控制台

```text
GET  /publish/capabilities
POST /publish/plans               {job_id?, kind?: auto|content|theme|template|full, channel?}
POST /publish/runs                {plan_id, plan_digest, idempotency_key}
GET  /publish/plans/{id}/receipt
GET  /publish/plans/{id}/previews
GET  /publish/plans/{id}/preview/{locale}/{family}
GET  /publish/theme-history
POST /publish/rollback-plans      {kind: theme, target_plan_id, job_id?}
GET  /publish/usage
```

计划私有持久化到配置数据目录 `publication-plans/`，对外 DTO 不暴露凭据和私有执行细节。
主题预览使用六个保留样本与精确候选 CSS，禁用脚本、表单、导航及主动请求，
以 sandbox iframe 展示；样本缺失或摘要损坏不会转而加载线上页面。

入队前核验到期时间、输入摘要、配置和版本基线；入队锁内再次检查任务配置与完整待发布范围，
防止检查后配置变化或 dirty 集合缩水。主题入队后使用冻结候选和目的地，暂停新发布不阻断恢复。
content/full/template 的新计划也会冻结非敏感发布配置、任务配置、账户与原始目标；
凭据不写入计划或恢复收据，重试时读取新凭据并检查账户身份。任务使用局部服务副本重放，
不会更改全局设置；子进程按类型与字段 alias 重放配置，未设置的受管环境变量真正移除。

恢复边界以完整 prepared 候选及 task/plan 绑定收据为准。在此之前仍要求源码和配置摘要一致。
在此之后可跨源码、任务设置、频道开关和默认目标变化恢复，但始终使用原候选、原目标与原 dirty IDs；
缺失或损坏的候选必须停止，不能重新导出、构建或重新选取目标。文献先执行无上传的 prepare-only，
冻结完整 Papers 目录、分片、模板和记录，再登记恢复收据；恢复时验证摘要、引用闭包及实际对象字节。
静态恢复校验原 StageReceipts 产物清单，并核对本地成功标记与生产 release.json；
研究指针保留原 CAS，报告也检查独立频道的原始/自身版本，旧候选遇到较新发布必须拒绝。
旧计划若未包含冻结执行环境，不会凭空推断恢复授权，仍保留原摘要保护。

收据展示权威提交、公开验证和数据库确认。上传成功、任务入队或本地构建完成均不能
替代公开验证。部分提交保留恢复状态，旧候选遇到新基线不能覆盖新版本。

## 国家模板实际测量

基线有 6,749 个 HTML 文件。以下是单次热缓存样本，不是中位数或生产部署耗时：

| 场景 | 生成国家页 | 文章/报告正文渲染 | 原样保留其他 HTML | 总耗时 |
| --- | ---: | ---: | ---: | ---: |
| JP 三种语言 | 3 | 0 / 0 | 6,746 | 34.12 秒 |
| 全部 295 个国家/地区，三种语言 | 885 | 0 / 0 | 5,864 | 27.13 秒 |

全量样本的编译调用 11.90 秒、候选组装 9.90 秒；目录缓存 295 hits、0 data reads。
候选位于 `exports/country-template-stage-all/`，旁边及内部均有构建收据。
原 `astro-site/dist` 未修改。复制、校验和部署完整静态基线仍随产物体积增长；
本次消除的是无关正文处理和无关模板编译。

计划器仅在变更严格属于 EN/ZH/FR 三个国家首页入口、完整已验证基线与发布版本匹配，
且 `exports/publicly-verified-site-release.json` 的实际字节摘要与基线一致时
选择 `country_template`，由原 Pages 发布器部署并验证。其他模板变更仍走 `code_only`。
新增国家路由、国家疾病页、共享布局或全局资源变更不能误用此快捷路径。

## Papers 协议与范围

新记录由公共文章投影直接生成，record/template/assets 各自独立摘要；当前发布和
Worker 要求 schema 3，schema 2 模板条目拒绝服务，不作为失败回退。初次迁移从验证过的旧 shell 提取模板，此后单篇正文更新无需
Vite/Astro 或旧文章 HTML。Worker 缓存隔离版本、路径、语言和三个摘要。

快速正文更新仅允许已有文章的 summary 和 updated-at 变化；标题、来源、关系、审核状态、
路由或目录变化需要正常依赖构建。首迁移必须覆盖完整文章目录，脚本候选依然通过既有
`publish_literature_candidate` 和唯一 research authority 提交，不产生第二个权威入口。

本地已验证两篇真实文章的六个语言页面，以及单篇更新只改一条记录及三种语言索引项。
浏览器检查 EN/ZH/FR 的 1440/390 视口，包含完整七项正文、来源、审定披露、canonical、
alternates 和 JSON-LD。证据在 `exports/papers-projection-validation-20261113/`。
补充第三篇含 Situation 事件的文章后，累计验证九个语言页面；三语桌面/手机另六例
确认原有事件分组、去重、日期/百分比与折叠交互保留。
完整上线与行为差异见 [Papers 迁移文档](PAPERS_TEMPLATE_MIGRATION.md)。

## 真实账户只读证据与上线顺序

- 北京时间 10:01 的快照 `exports/publication-audits/cloudflare-20261113T020151418508Z.json`：
  24 条 research 范围的 zone Worker routes；Pages 项目 `globalidv2`，生产分支 master；
  未发现拟用 static 域名或独立主站公开数据桶。
- 套餐 subscriptions API 返回 403，未假定 Free/Paid；Reports CORS 元数据返回 404，保持未知。
- 北京时间 10:40 的快照 `exports/publication-audits/cloudflare-20261113T024003769455Z.json`：
  Cache Rules entrypoint 返回 403，无法核验现有 JSON 缓存规则。
- 只读采样的历史账户请求数属于 GraphQL 自适应估算，不能当作账单、当前余额或套餐额度。
  采集文件 `data/cloudflare-publication-usage.json`；scheduler 五分钟采集默认关闭。
- 文献公开监控实际只读探测成功：
  `exports/publication-audits/literature-monitor-20261113T030046Z.json`。
  校验单一指针、目录摘要及 generation、256 个目录分片中的一个和 418 个 JSON 对象中的一个，
  并复读指针排除版本切换；没有把这一抽样结果表述为所有文章页面通过。

补充只读就绪检查 `scripts/check_literature_publication_readiness.py`：2026-11-13
实际探测主站 Literature 指针返回 200，研究目录包含 397 个不可变对象和 256 个
分片，抽样对象校验通过；当前报告为 `ready=true`，`automatic_publication_enabled=true`。
生产 `.env` 已将 `DATA_RELEASE__LITERATURE_JSON_ENABLED=true`，因此 Literature
JSON 已是运行中的独立数据通道。该检查只验证指针、目录、分片和一个 JSON 对象，
不等同于 Papers v3 HTML 投影的逐篇验收；逐篇公开契约由 v3 canary 收据和 Worker
运行时检查覆盖。

JSON CDN 方案已撤销。原始设计草案已移除，不代表当前实施范围。新增公开分发基础设施的删除和验证结果记录在生产验收报告；当前发布继续使用原有同源 JSON 入口。主题独立 CSS 与 Papers v3 HTML 已完成切换；历史对象保留，当前 reader 不回退到 v2。
新增的完整代码变更覆盖共享资源和发布协议，初次上线需要完整发布；
国家模板隔离路径用于后续符合条件的单一模板变化，不能用于本次整体变更。

生产过程与已完成验证以最新部署验收记录为准，不再将历史候选测试当作生产结果。
2026-11-14 的数据源限制、快照门禁与更新页实际发布见 [补充验收](../acceptance/DATA_UPDATES_20261114.md)。
还需要实际路由与请求指标、慢网/旧标签页/额度异常验证、冷热构建多次中位数、
首屏与布局偏移对照、生产故障恢复和回滚演练，才能声称设计各阶段全部通过验收。

发布前先运行 `cd dashboard && npm run openapi:check`。该检查把后端生成的
OpenAPI 和 TypeScript 客户端写入临时目录后逐字节比较当前文件，不修改工作区，
也不依赖 Git diff。随后运行 `venv/bin/python scripts/release_readiness.py`；它会
把脏工作区、当前提交和合同状态写入只读收据。只要存在脏路径或合同不是 `ok`，
readiness 就返回失败，不能把 `commit_dirty=true` 的版本当作可复现生产发布。

## 验证与复现

分组回归包括计划与 API、队列幂等和冲突、主题条件提交及恢复、
三通道实时控制、旧 schema、Papers 双读和隔离模板构建。具体本轮最终结果在下方记录。

| 检查 | 结果 |
| --- | --- |
| 发布后端集中回归，26 个文件 | 424 passed，41.16 秒；一个既有 Starlette/httpx 弃用提示 |
| 文献监控及控制回归 | 86 passed，其中监控 63、控制 23；与其他分组有重叠，不相加 |
| 控制台组件集成回归 | 56 passed；TypeScript 通过 |
| 控制台生产构建 | Next 构建成功，83/83 页面 |
| Astro 类型与样式检查 | 447 个文件检查通过，0 errors / 0 warnings；13 个既有 hints |
| 控制台浏览器 | 中英文 × 桌面/平板/手机 6 例通过，API 完全由夹具拦截 |
| 图表 | 68 项整组回归通过；随后新增 nullable refresh 兼容的两项专项通过 |
| Papers Worker | 23 项单元测试、TypeScript 通过；workerd 4 passed，1 个已有可选夹具跳过 |
| 国家候选浏览器 | 修复图表销毁竞态后 12 例通过，无缺失资源、JS 错误或横向溢出 |
| 主题浏览器 | 原有 36 场景；新增带实际 HTTP CSP/sandbox 的私有预览 12 场景通过 |

恢复回归包含实际 JSON 发布器提交指针后模拟连接中断，再修改源码、默认桶、频道开关和任务配置，
确认重试没有重复上传且只确认原 dirty IDs；同时覆盖静态部署后中断、原候选复用、较新远端拒绝，
以及候选缺失、目录分片/模板依赖损坏、凭据 URL 拒绝与真实子进程配置解析。
测试仅使用本地夹具与内存远端；调度夹具隔离控制文件，不读取或修改生产维护暂停状态。

国家浏览器检查确实发现了 zrender 销毁竞态；修复和原失败证据均保留。
最终修后代表候选证据为 `exports/country-template-stage-chart-fix.browser/verification.json`。
这是共享 renderer 变化，因此修后候选用于代表页验证，初次发布仍需完整模板构建。
私有主题预览证据为 `exports/frozen-theme-preview-check/private-preview-browser-verification.json`。

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_publication_planning.py tests/unit/test_publication_run_admission.py \
  tests/unit/test_theme_publication.py tests/unit/test_theme_preview.py \
  tests/unit/test_papers_projection.py \
  tests/unit/test_publication_usage_collector.py tests/unit/test_publication_cloudflare_audit.py \
  tests/unit/test_collection_api.py tests/unit/test_publish_dashboard_channels.py \
  tests/unit/test_realtime_control.py tests/unit/test_realtime_channel_control.py

cd dashboard
npm run openapi:generate
npm run lint
npm test -- src/features/control-plane/publication-planning.test.tsx \
  src/features/control-plane/models-publish.test.tsx \
  src/features/realtime-publish/view.test.tsx src/features/operations/task-scope.test.tsx
npm run build
npx playwright test e2e/publication-planning.spec.ts
```

Astro/Worker 的分组命令与协议细节分别见：

- [主题 v1 发布与预览](MAIN_SITE_THEME_PILOT.md)
- [国家模板本地构建与验证](#国家模板本地构建与验证)
- [Papers v3 迁移](PAPERS_TEMPLATE_MIGRATION.md)


## 国家模板本地构建与验证

以下构建样本属于 2026-11-13 本地验证；生产切换以部署记录为准。

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

### 适用边界

此路径只适用于既有国家首页模板族。新国家路由需要目录/sitemap 同步，国家疾病页属于其他模板族，不能复用这一范围。共享布局、全局样式、客户端协议或多个模板族变化仍需由分类器扩大影响范围，不能强行限制成国家发布。资源和主题契约保持原有兼容要求。

单元测试命令：

```bash
node --experimental-strip-types --test astro-site/scripts/build-country-template.test.mjs
```

测试覆盖精确三语范围、无关 HTML/旧资源/release.json 保留、新路由拒绝、意外路由拒绝、资产冲突和缺失引用。本地代表页与语言切换浏览器验证已完成，证据见下文；生产部署后仍需验证真实域名、CDN/Worker 路由和公开页面。

### 计划执行的资格与基线保护

现有发布任务已接入国家模板构建分支，只有相对已知发布源码的变更完全落在三条国家首页 `.astro` 路由，且 `exports/publicly-verified-site-release.json` 提供对应版本的真实公开验证证明时才可选择；不能仅凭目录名 `verified-pages-snapshot` 推断基线已公开验证。共享组件/JS/CSS、工具链、多模板族变更，以及发布源码未知、dirty baseline、盘点失败/不稳定或符号链接等情况均不能进入该快速路径。

计划冻结 verified Pages snapshot 的完整产物摘要与 `release.json` 原始 SHA；排队执行、构建开始均重新验证。CLI 对应参数为 `--baseline-digest` 与 `--baseline-release-sha256`。完整产物摘要沿用原发布器算法（按相对路径排序，排除 release.json，混合路径与各文件 SHA-256），不是仅比较可复用的 release ID。构建期间还检查所有基线文件身份未改变。

### 浏览器回归中发现的图表竞态

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
