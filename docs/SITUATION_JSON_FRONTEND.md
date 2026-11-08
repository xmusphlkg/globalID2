# Situation 独立 JSON 与静态模板

Situation 浏览器入口为同源 `/site-data/situation/current.json`，可通过 `PUBLIC_GIDS_SITUATION_MANIFEST_URL` 改为其他公开数据入口。它与监测曲线 `/site-data/current.json` 使用独立版本和独立 R2 前缀 `site-data/situation/`，不会相互推进版本。

入口复用 manifest v1：`schema_version: 1`、`release_id`、`generated_at`、`files`。逻辑文件包含 `map-data.json`、`outbreaks/{id}.json`、`by-country/{CC}.json`、`by-disease/{ID}.json`、`weekly/index.json`、`weekly/{YYYY-Www}.json`、`sources.json`、`map.json` 与 `monitoring.json`。每项具有相对入口解析的 `objects/{sha256}.json`、SHA-256 和 JSON 原始字节数。发布者先上传不可变对象，最后提交 current；旧对象保留。

所有登记国家/辖区和疾病都提供 entity shard；没有通报时返回 `alert_count: 0, alerts: []`。缺少逻辑文件或缺少被引用对象仍是完整性错误，前端不会把它推断为零条通报。

`situation-data.ts` 复用完整性客户端，使用逻辑根 `/situation/`。一页只成功读取一次 Situation manifest，地图、国家/疾病通报、首页数字、归档与延迟打开的 outbreak 详情读取同版对象。两个数据通道各自固定版本：Situation 官方通报与国家统计保持独立发布周期，地图的监测背景继续从监测通道读取，并保留各自报告日期。

入口不存在时，仅既有地图和 outbreak 静态 URL 可用于迁移回退。入口存在后，对象缺失、大小/SHA 不符会关闭读取，不混用旧静态警报。新通报摘要文件没有旧静态对等项，因此首次必须先初始化 Situation JSON 和同源服务，再部署模板。

地图模板不再根据构建时有没有 map.json 决定是否渲染，也不嵌入通报日期、点位数或当前警报。国家、疾病、国家×疾病页面的通报面板按 entity shard 读取。旧的离线 local-snapshot 徽章不会带入新的通报面板；当前国家监测背景仍可在态势地图中查看。静态研究链接和登记实体身份由模板发布维护。

周/月归档目录及已存在的 period 页面均使用运行时周索引。新周与新月通过 `/situation/weekly/?period=YYYY-Www` 和 `/situation/monthly/?month=YYYY-MM` 显示，不依赖预先生成对应 HTML。疫情详情在展开时读取同版对象，并只展示该 ISO 周的警报。既有 period URL 继续作为模板别名存在；新增漂亮 URL 属于可选模板拓扑变化，不阻塞新周数据。

## 同源服务的两种入口

`cloudflare/surveillance-data/src/index.ts` 保留原 surveillance 协议，并支持 Situation namespace；standalone Worker 名仍为 `globalid-surveillance-data`。两个前缀共用现有 `DATA` R2 binding。current 使用 `no-store`，不可变对象长期缓存，R2 body 直接流式返回。gzip HTTP 表示保留编码头，浏览器对解压后的 JSON 校验。旧 `/situation/map-data.json` 与 `/situation/outbreaks/{id}.json` 由当前 manifest 返回无缓存跳转。

没有 zone Workers Routes 管理权限时，使用 Pages Advanced Mode：`src/pages.ts` 复用同一 handler，其他请求交给 `env.ASSETS.fetch`。`scripts/build-pages.mjs <dist>` 生成 `_worker.js` 与 `_routes.json`；路由只包含数据 namespace 和旧 JSON aliases，不接管普通 HTML、研究索引或静态资源。Pages 的 production 配置需要 `DATA` → `globalid-papers`，可显式设置 `DATA_PREFIX=site-data/surveillance` 与 `SITUATION_PREFIX=site-data/situation`，未配置变量时 handler 使用相同默认值。此方案遵循 [Pages Advanced Mode](https://developers.cloudflare.com/pages/functions/advanced-mode/) 与 [路由文件](https://developers.cloudflare.com/pages/functions/routing/) 的公开契约。

每次后续模板发布都必须保留两个文件，避免移除同源数据服务。`GIDS_PAGES_DATA_WORKER=1` 让 npm 构建在校验完成后打包 Worker；该模式也不再预渲染旧 outbreak JSON。Python 模板构建/完整收据必须纳入 Worker 源码、打包脚本和依赖锁文件。监测 JSON 与 Situation JSON 的日常发布无需构建这个 Worker。

受管生产构建同时提供 `GLOBALID_SKIP_SITE_DATA_GENERATION=1` 与 `PUBLIC_GIDS_RELEASE_ID` 时，要求 `GIDS_RUNTIME_DATA_READY=1` 与 `GIDS_RUNTIME_SITUATION_READY=1`；缺少任一初始化标志时，在任何构建 stage 前停止。标志由管理流程在首次验证通道后传入，不是网络就绪探测的替代品。

## 本地验证

Worker 单测覆盖双 prefix、旧 aliases、只读方法、缓存、HEAD/ETag 与 Pages 静态回退。真实 Miniflare 验证两个通道独立推进、旧对象继续可读、gzip，以及实际打包的 Pages Worker 读取 R2 与 ASSETS。客户端协议单测覆盖独立 Situation 指针与延迟 outbreak 固定版本。

`e2e/situation-publication.spec.ts` 在静态 fixture 上切换 JSON 指针，检查地图/延迟详情固定版本、国家通报/首页/新周索引更新、HTML SHA-256 不变，以及损坏对象不显示旧快照。验证使用隔离目录和模拟对象，没有生产写入或部署。

2026-11-07 验证通过：7 个 Worker 单测、2 个真实 Worker/Pages runtime 测试、11 个客户端协议/构建单测、16 个桌面/移动浏览器用例；隔离 112 页构建的 SEO、性能、多语言门均通过。Astro 检查 356 文件为 0 errors、0 warnings。日志与小结位于 `data/cache/build-verification/20261107-data-architecture/situation-frontend/`。
