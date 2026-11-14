# Situation JSON publication

Situation facts, Surveillance observations, and static HTML have separate publication contracts. With `DATA_RELEASE__SITUATION_JSON_ENABLED=true`, Situation changes publish under `site-data/situation/` without Astro, CSV generation, or observation projection. The default is false; existing static publication remains available until migration.

Collection writes its public metadata/source-health changes and a `PublishDirty(line='situation', scope_key='*')` row in the same transaction. Each accepted extraction also records a marker in its result transaction when public facts or review metadata changed. Failed or rejected extractions retain earlier accepted alerts. Identical collection input, identical accepted facts, and unchanged outbreak reconstruction produce no extra markers. Multiple result transactions in one long AI task retain separate IDs, so publishing an earlier result cannot consume a later result.

Successful `refresh_situation_sources` completion invokes reconciliation immediately. The release scheduler also checks committed markers every poll, including when static releases, daily timers, and `auto_after_crawls` are disabled. Therefore a missed completion hook, partial refresh failure, or process restart does not lose committed changes. Automatic retry deadlines and failure cooldowns remain durable. Cooldowns are scoped by JSON line. Both JSON lines share one worker slot, and alternate when both have publishable work; disabled/blocked Surveillance scopes do not hold back Situation.

The queue freezes `data_only=true`, `data_line='situation'`, and exact selected `publish_dirty_ids`. Missing `data_line` on an older JSON task continues to mean Surveillance. New static tasks freeze `situation_detached=true` when the gate is enabled and exclude Situation IDs. Old static tasks without this contract cannot overlap Situation publication; execution also rejects incompatible old tasks after the gate is enabled. Static content, collection, and AI work can run alongside a compatible JSON task.

Before opening its read snapshot, a managed task captures the current remote pointer into its private run's `.publication/starting-baseline.json`. Repeated capture validates the same receipt without adopting a newer pointer. Managed upload requires this receipt; an old frozen candidate without it fails closed and needs a fresh task. A changed remote baseline rejects an old candidate with HTTP 412. This also protects a task interrupted between generation and its first upload, and a task whose upload succeeded before DB acknowledgement failed.

The export reads all Situation tables in one PostgreSQL `REPEATABLE READ, READ ONLY` transaction. Managed execution imports the caller's exported snapshot. Standalone export establishes the same isolation inside its single DB session. Outbreak grouping is derived from the accepted alerts in that snapshot rather than depending on a long refresh reaching its final materialized-table rebuild. No report full text is exported.

The task holds its output file lock through generation and freezing. It freezes an immutable manifest and selected-ID receipt under `exports/situation-runs/<task_uuid>/`, while shared content-addressed objects live under `exports/situation-data/objects/`. Retries reuse the candidate. Every selected ID is checked against actual DB rows before generation, before publication, and before acknowledgement; wrong lines, non-global scopes, missing IDs, or IDs consumed by another task are rejected.

The manifest uses schema version 1 and logical files `map-data.json`, `map.json`, `monitoring.json`, `sources.json`, `outbreaks/<id>.json`, `by-country/<code>.json`, `by-disease/<id>.json`, and weekly files. Entity shards retain `gids.situation_alerts.v1`. Every registered country/jurisdiction and current disease receives a shard, including explicit empty shards, so a missing manifest entry remains an error. Upload completes immutable objects before atomically replacing `site-data/situation/current.json`; historical objects remain valid. Only after success are the exact selected Situation IDs acknowledged. Surveillance IDs and published curves are untouched.

Read-only local initialization:

```sh
venv/bin/python scripts/export_situation_data.py --release-id situation-initial-YYYYMMDD
```

Root deployment owns upload, service activation, and production monitoring. Bootstrap upload uses `scripts/publish_surveillance_data.py --source-dir exports/situation-data --prefix site-data/situation`. Managed task output includes `data_line`, `release_id`, `manifest_sha256`, selected IDs, and `release_timing` (`export_seconds`, `publish_and_ack_seconds`, `elapsed_seconds`); `pages_deployed` is false.


## Browser protocol and same-origin delivery

Situation 浏览器入口为同源 `/site-data/situation/current.json`，可通过 `PUBLIC_GIDS_SITUATION_MANIFEST_URL` 改为其他公开数据入口。它与监测曲线 `/site-data/current.json` 使用独立版本和独立 R2 前缀 `site-data/situation/`，不会相互推进版本。

入口复用 manifest v1：`schema_version: 1`、`release_id`、`generated_at`、`files`。逻辑文件包含 `map-data.json`、`outbreaks/{id}.json`、`by-country/{CC}.json`、`by-disease/{ID}.json`、`weekly/index.json`、`weekly/{YYYY-Www}.json`、`sources.json`、`map.json` 与 `monitoring.json`。每项具有相对入口解析的 `objects/{sha256}.json`、SHA-256 和 JSON 原始字节数。发布者先上传不可变对象，最后提交 current；旧对象保留。

所有登记国家/辖区和疾病都提供 entity shard；没有通报时返回 `alert_count: 0, alerts: []`。缺少逻辑文件或缺少被引用对象仍是完整性错误，前端不会把它推断为零条通报。

`situation-data.ts` 复用完整性客户端，使用逻辑根 `/situation/`。一页只成功读取一次 Situation manifest，地图、国家/疾病通报、首页数字、归档与延迟打开的 outbreak 详情读取同版对象。两个数据通道各自固定版本：Situation 官方通报与国家统计保持独立发布周期，地图的监测背景继续从监测通道读取，并保留各自报告日期。

入口不存在时，仅既有地图和 outbreak 静态 URL 可用于迁移回退。入口存在后，对象缺失、大小/SHA 不符会关闭读取，不混用旧静态警报。新通报摘要文件没有旧静态对等项，因此首次必须先初始化 Situation JSON 和同源服务，再部署模板。

地图模板不再根据构建时有没有 map.json 决定是否渲染，也不嵌入通报日期、点位数或当前警报。国家、疾病、国家×疾病页面的通报面板按 entity shard 读取。旧的离线 local-snapshot 徽章不会带入新的通报面板；当前国家监测背景仍可在态势地图中查看。静态研究链接和登记实体身份由模板发布维护。

周/月归档目录及已存在的 period 页面均使用运行时周索引。新周与新月通过 `/situation/weekly/?period=YYYY-Www` 和 `/situation/monthly/?month=YYYY-MM` 显示，不依赖预先生成对应 HTML。疫情详情在展开时读取同版对象，并只展示该 ISO 周的警报。既有 period URL 继续作为模板别名存在；新增漂亮 URL 属于可选模板拓扑变化，不阻塞新周数据。

### 同源服务的两种入口

`cloudflare/surveillance-data/src/index.ts` 保留原 surveillance 协议，并支持 Situation namespace；standalone Worker 名仍为 `globalid-surveillance-data`。两个前缀共用现有 `DATA` R2 binding。current 使用 `no-store`，不可变对象长期缓存，R2 body 直接流式返回。gzip HTTP 表示保留编码头，浏览器对解压后的 JSON 校验。旧 `/situation/map-data.json` 与 `/situation/outbreaks/{id}.json` 由当前 manifest 返回无缓存跳转。

没有 zone Workers Routes 管理权限时，使用 Pages Advanced Mode：`src/pages.ts` 复用同一 handler，其他请求交给 `env.ASSETS.fetch`。`scripts/build-pages.mjs <dist>` 生成 `_worker.js` 与 `_routes.json`；路由只包含数据 namespace 和旧 JSON aliases，不接管普通 HTML、研究索引或静态资源。Pages 的 production 配置需要 `DATA` → `globalid-papers`，可显式设置 `DATA_PREFIX=site-data/surveillance` 与 `SITUATION_PREFIX=site-data/situation`，未配置变量时 handler 使用相同默认值。此方案遵循 [Pages Advanced Mode](https://developers.cloudflare.com/pages/functions/advanced-mode/) 与 [路由文件](https://developers.cloudflare.com/pages/functions/routing/) 的公开契约。

每次后续模板发布都必须保留两个文件，避免移除同源数据服务。`GIDS_PAGES_DATA_WORKER=1` 让 npm 构建在校验完成后打包 Worker；该模式也不再预渲染旧 outbreak JSON。Python 模板构建/完整收据必须纳入 Worker 源码、打包脚本和依赖锁文件。监测 JSON 与 Situation JSON 的日常发布无需构建这个 Worker。

受管生产构建同时提供 `GLOBALID_SKIP_SITE_DATA_GENERATION=1` 与 `PUBLIC_GIDS_RELEASE_ID` 时，要求 `GIDS_RUNTIME_DATA_READY=1` 与 `GIDS_RUNTIME_SITUATION_READY=1`；缺少任一初始化标志时，在任何构建 stage 前停止。标志由管理流程在首次验证通道后传入，不是网络就绪探测的替代品。

### 本地验证

Worker 单测覆盖双 prefix、旧 aliases、只读方法、缓存、HEAD/ETag 与 Pages 静态回退。真实 Miniflare 验证两个通道独立推进、旧对象继续可读、gzip，以及实际打包的 Pages Worker 读取 R2 与 ASSETS。客户端协议单测覆盖独立 Situation 指针与延迟 outbreak 固定版本。

`e2e/situation-publication.spec.ts` 在静态 fixture 上切换 JSON 指针，检查地图/延迟详情固定版本、国家通报/首页/新周索引更新、HTML SHA-256 不变，以及损坏对象不显示旧快照。验证使用隔离目录和模拟对象，没有生产写入或部署。

2026-11-07 验证通过：7 个 Worker 单测、2 个真实 Worker/Pages runtime 测试、11 个客户端协议/构建单测、16 个桌面/移动浏览器用例；隔离 112 页构建的 SEO、性能、多语言门均通过。Astro 检查 356 文件为 0 errors、0 warnings。日志与小结位于 `data/cache/build-verification/20261107-data-architecture/situation-frontend/`。
