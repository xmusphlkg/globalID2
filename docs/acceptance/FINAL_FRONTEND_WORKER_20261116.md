# v0.20.0 前端与 Worker 最终验收

2026-11-16，本轮仅执行本地测试、浏览器验收和部署入口只读核查；没有部署、提交、修改运行配置或写入生产数据库。

关键证据已归档至 [`evidence/20261116_fixes/frontend-worker/`](evidence/20261116_fixes/frontend-worker/README.md)：32 个原始文件，共 347,275 bytes，逐字节复制；
准确原件路径、字节数和 SHA-256 清单见 [`original-evidence-manifest.json`](evidence/20261116_fixes/frontend-worker/original-evidence-manifest.json)，
可验证清单为 [`SHA256SUMS`](evidence/20261116_fixes/frontend-worker/SHA256SUMS)。完整本地 trace 路径及尚未执行的线上三语双视口 / RSS 命令见归档 README。

后续 non-unit 验收发现的两个前端构建节点已修复并隔离复验：2 passed / 41.65 秒，SEO 全套 50 passed；精确 RSS 动态路径例外与 Situation 构建副本说明见 [`FIX_FRONTEND_BUILD_ISOLATION_20261116.md`](FIX_FRONTEND_BUILD_ISOLATION_20261116.md)。这不代表原 non-unit 全套已经全部通过。

## 已完成验证

| 范围 | 结果 | 证据目录 `/tmp/globalid-fixes-20261116/` |
| --- | --- | --- |
| Astro 全部非浏览器测试 | 递归收集 86 个 `.test.ts/.test.mjs/.test.js` 文件，413 passed，0 failed / skipped | `final-astro-all-tests.log` |
| Astro check 与 styles | E2E 修复后最终重跑：523 files，0 errors / warnings，14 个既有 hints；styles PASS，global.css 1800 行、447 files | `final-astro-check.log`、`final-astro-check-after-e2e.log` |
| 三语源级检查 | en/fr/zh，58 strings，392 French UI，PASS | `final-i18n-check.log` |
| 世界地图 ISO | 212 features，PASS | `final-map-iso-check.log` |
| Surveillance Worker | 32 passed；typecheck PASS | `final-worker-tests.log`、`final-worker-typecheck.log` |
| Surveillance 本地真实 Worker / Pages Worker | dry-run PASS；5 passed、1 skipped | `final-worker-runtime.log` |
| Subscriptions Worker verify | 169 passed；typecheck、generated types、production readiness 全部 PASS | `final-subscriptions-verify.log` |
| Papers wrapper | 3 passed；dry-run PASS；runtime 1 passed、1 skipped | `final-papers-worker-tests.log`、`final-papers-build.log`、`final-papers-runtime.log` |

两个 Worker runtime 的 skip 均为需要显式提供旧 v2 真实产物目录的同一个历史兼容用例，不计为通过。现有 v3 合成投影、独立发布指针、R2 gzip、RSS 和打包 Pages Worker 运行时用例已通过。

## 浏览器覆盖与修复

390px 手机、1280px 桌面，共 84 个不同的 Playwright 重点场景最终均有通过证据：

- changelog、data-updates、disease-literature、download-dialog、public-restoration、situation-publication、situation 共 78 个场景。主批次为 **76 passed、2 failed**；两条失败修复后双视口 **4 passed**，包含两个重复的桌面验证。证据为 `final-focused-fixed-e2e.log`、`final-situation-recheck.log`。
- 与本次行政区地图有关的 public-experience 三个测试双视口 **6 passed**，覆盖下级区域标题、七个父级的下级数据入口、中文国家与疾病完整模板。证据为 `final-country-public-final-e2e.log`。

初次默认 505 场景全套因冷编译、源码编辑触发 HMR、旧测试前提而提前停止，**没有完整执行，不计为全套通过**。保留原始日志 `final-astro-e2e.log` 和 `first-e2e-test-results/`，随后按任务范围完成上述重点验收。

修复仅涉及五个 E2E 文件，未因此更改产品行为：

1. 文献筛选使用当前可访问名称“区域”。
2. 下载与主页 HTML 稳定性只排除 Astro 开发环境的 `server-render-time` 属性，继续逐字节比较全部业务标记；现有发布测试已使用相同处理。
3. 主页 fixture 提供实际 `home-summary.json` 合同，分别断言上级和下级数量；通报 fixture 显式声明 human，保留人类本地快照、变化率与独立数据版本断言，并验证通报来源和病例数。
4. Situation 按 Disease / Area / Source / Time window 可访问名称定位控件，防止新增 Affected host 改变索引；手机深链先打开 Places 再读取控件。历史 MERS fixture 未声明宿主和单位，精确断言未知单位提示，同时保留病例、死亡数及每组来源链接数量。
5. Public 模板测试使用显式的七父级、七子级与 AU / D021 数据 fixture，不依赖缺失的本地旧产物。国家页严格断言 **9 面板 = 原 8 + 行政区地图**，疾病页严格断言 **6 面板 = 原 5 + 病原体资源**，并验证当前标题与可用路由的链接回退。

另有独立浏览器验收：

- US / D094 的 390px、1280px 两组均通过：56 地区表格、仅切换趋势聚合不改变总数、病例与报告率切换、实际 CSV 下载、无效日期阻止导出与有效日期恢复、页面无横向溢出、pageerror 为零。证据 `map-ui-smoke.json`、`map-ui-390.png`、`map-ui-1280.png`。
- RSS 三语 × 两视口六组均通过，每组验证国家与疾病两个表单提交的精确筛选和语言参数。证据 `rss-ui-smoke.json`。浏览器 RSS 响应使用 fixture；真实 RSS Worker 的 gzip / R2 / generation 行为由前述 runtime 用例覆盖，正式域名仍需发布后检查。

## 地图性能复核

`astro-site/scripts/benchmark-subdivision-map.mjs` 使用实际 US D094 数据。旧实现、初次优化测量与最终重跑的输入 SHA、全年与第一季度输出 SHA、色阶和默认窗口完全一致。

| 3 次采样中位数 | 旧实现 ms | 初次优化 ms | 最终并发验收时 ms |
| --- | ---: | ---: | ---: |
| 冷 sharedCeiling，含 prepare / freeze | 2636.694 | 97.203 | 324.460 |
| 相同范围热调用 | 2430.184 | 0.184 | 0.125 |
| 仅切换趋势聚合 | 2489.834 | 0.131 | 0.083 |
| 切换到第一季度 | 2439.322 | 21.621 | 27.756 |
| 热 defaultWindow | 2805.494 | 0.576 | 0.621 |

证据为 `rc3-before.json`、`rc3-after.json`、`rc3-final.json`。最终重跑与全量 Node 测试、Astro check 并发，冷调用受到 CPU 竞争影响；该操作仍同步执行。此基准排除文件 I/O、JSON 解析和渲染，不能声称是浏览器 INP 或生产手机性能测量。

## 发布前尚需处理的产物门禁

`npm run check:surveillance-data` 当前本地旧产物 **未通过**：country 视图有 15,632 个身份，disease 视图有 15,559 个；仅 country 存在的 73 个身份全部属于 IE，disease-only 为零。完整名单为 `surveillance-identity-mismatches.json`，原日志为 `final-surveillance-data-check.log`。

这不是通过修改浏览器 fixture 能解决的生产产物问题。正式 full 生成后必须检查最终待发布产物的一致性，不得把这组旧缓存直接作为已验收发布输入。分渠道 manifest 的最终对象、哈希及主域读取由正式发布收据和发布后验收确认。

## 正式发布入口的只读核查

已读取最新 Wrangler 命令文档、本地 4.158.0 `deploy --help`、两个部署配置，并完成 dry-run。正式独立 Surveillance Worker 入口如下，本验收未执行它：

```bash
cloudflare/surveillance-data/node_modules/.bin/wrangler deploy --config cloudflare/surveillance-data/wrangler.jsonc
```

目标 `globalid-surveillance-data`，DATA 绑定 `globalid-papers`，14 条现有主域路由、`DATA_PREFIX`、`SITUATION_PREFIX` 与 `nodejs_compat` 齐全。RSS 位于已有 `/site-data/situation/*` 路由内；行政区数据通过 manifest 的 `/site-data/objects/*` 读取。凭据通过进程环境传入，不输出到日志或命令参数。

Papers wrapper 入口是同一 binary 加 `--config cloudflare/papers-json/wrangler.jsonc`，目标 `globalid-papers-template`。本轮 wrapper 及共享 Papers 内容路由没有变更。Subscriptions 配置也没有本轮变更；测试通过不等于需要额外部署这些 Worker。

Cloudflare 只读元数据确认 Pages 项目为 `globalidv2`，生产分支为 **master**，主域 `globalinfectiousdisease.com` active。`site-release` 任务的下载仓库分支 main 不等同于 Pages 生产分支；发布 pipeline 优先读取 Cloudflare 的 production_branch。脱敏证据 `deployment-cloudflare-readonly.json`。

正式混合变更需走 full 发布，单独 Worker 先部署兼容读取代码；managed Pages 流程不会部署独立 Worker。所有源码提交并停止修改后，先检查 dashboard OpenAPI 与 `scripts/release_readiness.py`，再使用：

```text
GET  /api/v1/publish/capabilities
POST /api/v1/publish/plans  {"job_id":"site-release","kind":"full"}
POST /api/v1/publish/runs   {"plan_id":...,"plan_digest":...,"idempotency_key":...}
GET  /api/v1/publish/plans/{plan_id}/receipt
```

计划有效期 15 分钟；先检查 executable、blocked_reason、冻结的源码与目标，然后使用该计划入队。计划创建后继续编辑源码会使检查失效。三条 JSON 渠道已启用，发布 receipt、数据库完成状态、真实主域 release / current / feed、不可变对象 SHA 和三语浏览器结果都需与本次发布对应，R2 上传成功本身不足以证明整站验收完成。

数据库只读核查确认已经应用 `0033_not_applicable_observations` head，控制面 schema preflight PASS；证据 `deployment-db-readonly.json`。运行时仍持续处理任务，协调更新后台应使用既有 `scripts/restart_task_runtime.py --include-api` 安全排空流程，保留队列和任务所有权。
