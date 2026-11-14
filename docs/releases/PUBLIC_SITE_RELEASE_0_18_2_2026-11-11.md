# Public site 0.18.2 — 2026-11-11

本文合并初始发布、逐项闭环和最终生产修订。各阶段的时间、计数与证据分别保留；后续结果见[闭环与最终修订](#后续闭环与最终生产修订)。

## Release status

Published and verified on the production Main, Papers and Reports hosts.
App version: **0.18.2**. Pages deployment:
`https://3ffdef6f.globalidv2.pages.dev` (project `globalidv2`, production branch `master`).

Release task: `c6433552-87ca-423e-ba11-4b00776366e6`.
This maintenance release suppresses subscription dispatch.

## Disease literature

The disease surveillance page links to a dedicated Papers library, for example
`https://papers.globalinfectiousdisease.com/zh/research/diseases/influenza/`.
English, Chinese and French libraries contain both reviewed publications and
bibliographic records; the interface distinguishes their evidence availability.
Filters cover title/author/journal/identifier, country, topic, study type, years,
access, record type and publication status. Annual publication counts reflect all
matching records. PubMed, Europe PMC and Scholar links include visible, copyable
queries. These starting queries do not claim systematic-review completeness.

Each page displays 24 records, with previous/next and page selection. The URL
retains filters and the page number; changing filters resets the page. A slim
search index supplies filtering and trend counts. Complete cards are loaded only
from the immutable shards needed by the selected page, with bounded concurrency,
cached requests and retryable failures. Old cards are hidden while another page
loads or fails.

Library HTML, indexes and card shards move into the existing Papers R2 bundle.
Main Pages retains the surveillance pages and redirects former library URLs.
Papers reuses the main build's CSS, fonts, header, footer, theme and React runtime;
only the library layout adds styles. Content-addressed card shards upload before
mutable indexes, which upload before library HTML. Retired shards remain available
for 48 hours so cached indexes can finish loading.

## Public-site audit fixes

The resolved findings are recorded in the [closure section](#后续闭环与最终生产修订),
with the [original audit evidence](#初始审查证据2026-11-10) retained below.

| Finding | Resulting behavior |
| --- | --- |
| Common aliases and compound searches | Flu aliases rank influenza correctly; compound country/disease searches open focused Ask queries. |
| Stale comparison countries | Changing diseases removes invalid countries and announces the change. |
| Misleading chart source captions | Runtime captions and observed date ranges follow selected series and geography. |
| Language-switch state loss | Query parameters and fragments survive language changes. |
| Ask fetched the worldwide catalogue | Recognized entities select a relevant complete collection before filtering. |
| Search failure looked like an empty result | Explicit failure state and retry action. |
| Homepage and map counts differed | Both use the same deduplicated 30-day Situation scope. |
| Download totals and units were ambiguous | Current-file totals are separate from full history, with consistent decimal MB. |
| Unnatural or untranslated interface copy | Chinese labels, loading states, research enums and instructions revised. |
| Theme differed across hosts | Main, Papers and Reports share a parent-domain theme cookie. |
| Weak text contrast | Light/dark contrast improved in affected public views. |
| Relationship chips lacked targets | Chips identify the actual related entity. |
| Research count understated coverage | Counts include complete reviewed and bibliographic coverage. |

The shared GIDS loader uses a lightweight globe/orbit motif in SVG and CSS,
accessible status text and a reduced-motion mode. Public data views use the same
component and localized messages.

Live profiling also identified a literature-export hotspot: disease-title
association ran every alias regex against every title. An absent-literal guard
now skips impossible regex matches while retaining boundary, negation and
specificity checks. A 1,000-title sample from the real catalogue produced identical
associations; matcher median CPU time fell from 1.283 to 0.103 seconds (12.46 times
faster over three runs). This measures this function, not total export or page-load
time. The final generation regression run passed all 98 tests. The comparison is recorded in
[title-match-prefilter.json](../benchmarks/public_site_v0182_2026-11-11/title-match-prefilter.json).

## Verification before publication

- Astro check: 417 files, zero errors and zero warnings.
- Chart unit tests: 67 passed, including independent SVG rendering of the lean
  chart registries; research unit tests: 34 passed; public presentation
  tests: 23 passed; Papers routing/sitemap tests: 8 passed.
- Release regression tests: 120 passed; Papers storage/publishing tests: 35 passed.
- Title matching and cache tests: 92 passed. The cache stores pure title
  associations only; candidate eligibility, integrity and database relationships
  are checked afresh. Changed titles, disease names/slugs, aliases and matching
  implementation invalidate cached results. Deleted or ineligible entries are
  pruned; damaged caches are safely recomputed.
- Browser scoped-collection tests: 6 passed, including public noindex records,
  historical seeds, explicit withdrawals, complete membership and unchanged bytes.
- Stable monthly library shards and complete per-disease cache keys: 13 passed.
- Production-bundle routing browser tests: 2 passed at 390 and 1280 pixels.
- Desktop/mobile browser scenarios cover search retry, aliases, comparison
  reconciliation, language continuity, cross-host theme, disease library routing,
  database queries, page loading/retry, filter reset, year validation and scoped Ask.
- Five accessibility scans across home, subscription and article views reported
  no violations; loader layout and reduced motion were also inspected.

Evidence and screenshots: [public_site_v0182_2026-11-11](../benchmarks/public_site_v0182_2026-11-11).
One development-server restart delayed hydration; the affected scenario passed
when rerun after readiness. A theme-test teardown race was fixed by awaiting
pending request handlers, and the theme scenarios passed again.

## Incremental literature updates

Article pages already use per-article content fingerprints and verified page
bundles. Template or shared-style changes require rebuilding affected templates;
ordinary literature additions can reuse unchanged pages. Library cache keys now
include the complete per-disease projection, including bibliographic-only records
and published preprints. The main surveillance page's literature count shares
that dependency, so new records cannot leave a stale count behind.

Title associations now have an atomic, checksummed local cache. A real
1,000-title cold/warm comparison returned identical output: 0.1485 seconds cold
versus a 0.0220-second warm median, including cache read/write and verification.
This is a component measurement, not an end-to-end release time.
The real release retry logged **202,280 reused associations and 76 computed**;
see [the live cache evidence](../benchmarks/public_site_v0182_2026-11-11/title-cache-live-release.json).
Topic matching also skips absent literal terms before retaining the original
word-boundary check. A 1,000-title comparison returned identical topics and
reduced median CPU time from 0.664 to 0.036 seconds; see
[the topic benchmark](../benchmarks/public_site_v0182_2026-11-11/title-topic-prefilter.json).
Changing that matching implementation deliberately invalidates its disease-title
cache once on the next export.

Database eligibility scanning and the normal Astro release still run. R2 skips
unchanged objects by content identity. This is incremental computation, rendering
and upload reuse; it does not claim to eliminate every build or publication step.

Card shards are grouped by publication month, with at most 24 cards in each
physical shard. Adding or backfilling a record changes that disease's index and
the affected month's shards; other months and diseases retain identical bytes.
Undated records have a separate bucket. The browser still displays exactly 24
results per page and fetches only the shards required for those results.

The production follow-up repaired stale browser Ask collections from the frozen
canonical snapshot without another database export or Astro render. This updated
424 scoped JSON files and reused 48,210 other local bundle objects. All 48,634
local entries were checked against the committed remote R2 manifest with no
checksum or size mismatch. Retained older social cards account for additional
remote-manifest entries. See
[the incremental publication receipt](../benchmarks/public_site_v0182_2026-11-11/papers-collection-repair-publication.json).

## Production verification

The normal release task completed at `2026-11-10T18:57:50Z` (02:57:50 China time).
Subscription dispatch was suppressed. Production `release.json` matches release
`20261110T172935Z-1b4cdc3f3d90` and build digest
`3053f283aa60b52e887e7a0c3b0752554894896da558b52da0cd64eb7dd6641e`.
The [release receipt](../benchmarks/public_site_v0182_2026-11-11/release-receipt.json)
records the exact identity; browser verification was performed independently of
the release job's optional built-in website verification.

All build gates passed: SEO inspected 32,200 pages without errors, and performance
inspected 32,228 HTML files. The largest route resource graph is **391,586 gzip
bytes**, below the unchanged 395,000-byte cap. A feature-specific chart registry
removed unused legend/mark-area components; heatmaps retain their continuous
colour mapping. The original build checks caught broken country/RSS links, a
truncated-title collision and the oversized chart graph before any publication;
each issue was corrected and the complete gates rerun. See
[build gates](../benchmarks/public_site_v0182_2026-11-11/build-gates.json).

The final production browser checks passed:

- Public smoke: 11 checks at 390/1440 pixels, with no page errors or overflow.
  Version, aliases, compound-search routing, comparison language/query state,
  download labels and two-way theme continuity across all three hosts passed.
  Homepage and Situation both showed 227 places for the same data version.
- Disease library: 390/1280 pixels, 5,002 records, 24 visible cards per page.
  The first page read only 2 of 462 physical card shards (about 16.6 KB encoded);
  the next page added one shard (about 5.3 KB). The slim filtering index was
  approximately 0.97 MB encoded, fetched once. Filters reset to page one and
  publication-trend counts matched the complete filtered result set.
- Production-bundle tests also exercised a page spanning November/October and
  matched all 24 rendered titles to its 3 actual Papers shards. All three CSS
  files were byte-identical to the same Main build. The library HTML and catalogue
  are absent from the Main Pages bundle.
- Ask: the actual response contains **984 articles**, matching the current
  canonical reviewed collection and the library's evidence-summary count.
  “日本流感” uses one disease collection and zero worldwide catalogue requests;
  it returns 9 exact matches and cites JP NIID Weekly surveillance.
- Legacy library/catalogue URLs redirect to Papers; Chinese disease RSS returns
  HTTP 200; content-addressed card shards have immutable cache headers.

Production verification found two issues that local interception could not prove:
the R2 bucket initially lacked CORS, and the new scoped Ask reader reached legacy
133-record browser collections. The bucket now applies
[the production read-only CORS policy](../../cloudflare/papers/cors.json). The
generator now refreshes complete disease/country/topic browser collections on
each export, and the existing snapshot was repaired and incrementally published.
The original failure evidence is retained; the final full browser rerun passed.

Cloudflare Speed Brain refused four speculative prefetches with explicit
`cf-speculation-refused` headers and empty bodies. These were recorded separately;
actual document navigation to the same URLs returned HTTP 200. No real resource
failures remain in the final smoke results.

Final evidence:
[public smoke](../benchmarks/public_site_v0182_2026-11-11/production-public-smoke.json),
[literature and Ask](../benchmarks/public_site_v0182_2026-11-11/production-literature.json),
[CORS](../benchmarks/public_site_v0182_2026-11-11/production-cors.json),
[routing](../benchmarks/public_site_v0182_2026-11-11/production-routing.json).


## 后续闭环与最终生产修订

### 原审查 13 项

| 编号 | 现有实现与本轮复核 |
| --- | --- |
| 01 检索别名与组合词 | 普通流感及“日本流感”可识别，统一实体与别名检索。 |
| 02 比较国家残留 | 切换疾病时清理不可用国家，数量与实际曲线一致。 |
| 03 来源范围 | 来源随当前选择的序列收窄，日本流感使用 JP NIID Weekly。 |
| 04 语言连续性 | 五类实际页面通过 zh→en 查询和锚点保持检查：比较、国家目录、搜索、Ask、态势。 |
| 05 Ask 目录载荷 | 有实体问题使用完整范围集合；“日本流感”不请求全球目录。本轮将无实体的完整目录读取限制为 4 个并发、展示分片进度，并验证旧查询不能覆盖新查询。 |
| 06 加载错误 | 搜索失败与无结果分离，可重试。 |
| 07 地点统计 | 首页和态势使用一致地点范围，并说明时间窗口。 |
| 08 下载文件口径 | 当前文件和历史目录行数分开，单位统一；本轮补充跨国家/疾病目录累加含重复的说明。 |
| 09 中文界面 | 修复操作标签与状态枚举；本轮继续修复研究页全部国家选项及客户端筛选重绘的研究类型、主题、地点中文。 |
| 10 跨子域主题 | 主站、Papers、Reports 共享主题偏好，来回切换通过。 |
| 11 对比度 | 已修复原审查发现的低对比度界面文本。 |
| 12 文章关系 | 关系展示目标实体，不再把当前文章标题重复作为目标。 |
| 13 研究入口计数 | 采用研究指标，明确与监测报告数量的区别。 |

### 原报告四项待核查

| 问题 | 结论与证据 |
| --- | --- |
| 研究页 target-size / 部分遮挡 | 逐节点滚动、命中采样和实际点击均通过，未复现稳定遮挡。原两个链接尺寸超过 24px；专项 Axe 检查通过。未因此添加无依据的 CSS。见 `research-target-all-hit-tests.json`。 |
| null 缺失期间连线 | 使用真实 D002/AU-NSW 月序列 `[0,0,null,0,0]` 渲染 ECharts SVG，得到两段独立路径。零值保留，null 不连线。见 `real-comparison-gap.json`，可运行同目录 `verify-real-gap.mjs`。 |
| 下载“公开记录”是否去重 | 实际同一观察在国家和疾病数据集均出现。显示改为“目录行数（含重复）”，说明这是各数据集行数之和；英语和法语同步修正。计数算法与数据不变。 |
| Hib 联合疫苗被当作流感 | 原病例 DOI `10.1016/j.vaccine.2023.03.077` 的规范分类和线上范围集合均已排除 D038。另核实 CDC DOI `10.15585/mmwr.mm7336a4`，通过正式单篇重分类移除只有宽泛 OpenAlex topic 支持的 D038，并为将来采集增加保守规则。真实 Hib/流感共感染仍保留匹配。 |

另发现 DOI `10.1186/s12879-023-08560-x` 的提供方 MeSH 含“Influenza, Human”，但原文研究对象是细菌分离株。保留提供方元数据与原始疾病关联；仅在 Ask 流感查询中将这条经原文核查的记录标为背景证据，显示说明及原文链接。D100 查询与真实共感染阳性对照仍可精确匹配。该规则不是对所有 Hib 文献的统一排除。

### 新需求与性能边界

- 疾病完整文献页位于 Papers 子域，使用 Papers 存储；支持筛选、24 条分页、年度趋势、内置策略的 PubMed/Europe PMC 等检索链接。
- 文献页与主站使用同次构建的共享 CSS，保持导航、配色、语言和主题；主站部署包不包含完整文献页和分片。
- GIDS 轻量加载动画支持减少动态效果偏好，加载失败提供明确反馈。
- 文献卡片按月份与内容哈希分片，未变文件不重写、不上传；标题关联缓存、浏览器范围集合与 R2 实际库存比对已接入生成流程。旧哈希分片保留 48 小时，避免已打开页面翻页时失效。
- 增量不是“完全不构建”：当前导出仍检查完整规范数据和发布策略，未实现按单篇数据库变更事件驱动的独立发布任务。无实体的 Ask 为保证召回仍可能读取完整目录；本轮减少并发和等待不透明，未声称减少其总数据量。

### 验证与发布

本轮研究相关单元测试 39 项、分类相关测试 84 项通过；Astro 检查 417 文件，0 错误、0 警告。手机/桌面完成筛选中文、查询竞争、精确/背景证据和横向溢出检查。原始与新证据分别保存在：

- [v0.18.2 证据](../benchmarks/public_site_v0182_2026-11-11)
- [本轮闭环证据](../benchmarks/public_site_closure_2026-11-11)

最终生产验收见下方第二次修订结果；测试访问真实主站与 Papers，未以本地拦截代替生产资源加载。

### 线上复验发现的第二处性能遗漏

第一轮修订已于北京时间 04:00 前完成发布，release ID 为 `20261110T193716Z-1b4cdc3f3d90`。公开站 11 项与疾病文献页生产测试通过，但研究目录的数学建模筛选在手机第一次测试中超过 60 秒，不能据此宣布全部闭环。后续诊断确认全目录有 482 个分片、48,155 条记录；640 条数学建模文献分散在 351 个分片中。原页面既未利用筛选范围缩小读取，也没有显示真实加载进度。

研究类型专用集合已改为小分片：最多 1,000 条的小类按 100 条切页，大类按月份再按 100 条切页，避免常见类型成为巨型文件或新月份引起旧月份重排。数学建模 640 条恰好 7 页，逐条 ID 和完整 compact payload SHA 与原目录一致。其他实体使用安全分片预筛；旧 manifest 缺少 facet 时保守回退，防止漏召回。加载动画、真实进度、失败重试、迟到进度隔离和旧筛选请求隔离均已补齐。

最终本地 44 项研究测试、39 项 Papers Python 测试、418 文件类型检查及 4 个浏览器交互场景通过。原 60 秒超时和后续 45.335 秒完整读取 482 分片的诊断证据均保留。第二次代码修订已部署到生产，专项真实浏览器验收通过：390px 筛选耗时 2.907 秒、1440px 为 2.205 秒；均为 manifest + 7 个专属分片、0 个全球正文分片，640 条完整记录、顺序和前 N 个标题与当前 manifest 一致。国家选项及动态卡片中文正确，无资源/脚本错误或横向溢出。原诊断 45.335 秒与本次测试都是单次浏览器结果，不代表所有设备或网络的分位性能。

第一轮实际上传：Papers 上传 1,898 个、复用 47,710 个文件，807,692,878 字节；Reports 全部 1,617 个文件复用。端到端耗时 1,348.13 秒，超过 600 秒目标。不能将“多数文件可复用”等同于“发布已低于 10 分钟”。


### 最终生产修订与证据

- 应用版本：v0.18.2 修订版。
- Release ID：`20261110T201311Z-1b4cdc3f3d90`。
- Build digest：`403a0bc00256d64068ef0aae0dc0555e5d68ca745e83acf42af3802626a6fdba`。
- Pages 部署：<https://425887d8.globalidv2.pages.dev>；正式域名的 `release.json` 已核对一致。
- 性能门禁：32,285 个 HTML；最大路由资源图 391,585 gzip 字节，原 395,000 上限保持不变。
- 主站文件数 9,430，研究类型专属的 2,430 个分片位于 Papers，主站没有保留该目录。
- 新增研究类型分片合计原始体积 133,735,620 字节，按内容哈希发布；增量机制继续复用未变文件。

真实生产最后一轮验证均通过：

1. [公开站 11 项](../benchmarks/public_site_closure_2026-11-11/research-filter-release/production-public-smoke.json)：手机/桌面、下载重复口径、主题和地点统计等通过。
2. [研究筛选与中文](../benchmarks/public_site_closure_2026-11-11/research-filter-release/production-research-i18n.json)：完整 640 篇、7 个专属分片，两种宽度通过。
3. [Papers 承载约定](../benchmarks/public_site_closure_2026-11-11/research-filter-release/production-papers-contract.json)：三份 CSS 在 Main/Papers 均返回 200 且 SHA 一致；旧 Main study URL 返回 301；新分片具有一年 immutable 缓存、正确 Main Origin CORS，每片不超过 100 条。
4. [疾病文献及医疗匹配](../benchmarks/public_site_closure_2026-11-11/research-filter-release/production-literature.json)：390/1280px 每页 24 条，首屏仅 2/469 个卡片分片、下一页新增 1 个；筛选重置、趋势、PubMed、985 篇集合与 Ask 一致；Hib 三案例按核查结论呈现。
5. [原按钮实际点击](../benchmarks/public_site_closure_2026-11-11/research-filter-release/production-research-clickability.json)：390/1440px 点击和命中通过。

最后这次代码发布中，Papers 上传 26,469 个、复用 25,570 个文件，上传 1,183,354,629 字节；Reports 上传 1,572 个、复用 47 个。共享脚本引用变化导致 24,033 个已有 HTML 需要更新，因此完整代码发布耗时仍较长。文献新增的文件复用和上传跳过机制已经生效；尚未实现独立的单篇数据库事件发布，也未达到所有发布都在 10 分钟内完成的目标。

运行维护记录：一般采集 worker 已加载新的分类规则。重启等待超过服务停止上限后，有一条未完成采集被终止；已仅针对旧 worker 的死租约运行正式恢复并通过任务 API 重试，该任务已由新 worker 重跑并于北京时间 04:13 完成（100%，无错误）。独立 AI worker 未重启。见 [恢复证据](../benchmarks/public_site_closure_2026-11-11/worker-restart-recovery.json)。

发布收尾的只读线程采样显示：`StageReceipts.mark_published → _inventory → Path.stat` 与同进程 `BrazilSINANCrawler.aggregate_file → DBF._iter_records` 同时运行，后者采样持有 GIL。采样未读取局部变量，未中断采集。该并发瓶颈影响基线记录速度，独立发布执行环境仍是后续性能优化方向；本轮不把它标为已解决。见 [线程采样](../benchmarks/public_site_closure_2026-11-11/research-filter-release/worker-stack-publication-inventory.txt)。

最终正式任务 `47d654f4-f6bb-4ae4-a09e-e9a93b72a82a` 已完成（100%，无错误），总耗时 2,941.423 秒；生产部署和验收先完成，之后继续完成基线和快照保留。完整记录见 [发布回执](../benchmarks/public_site_closure_2026-11-11/research-filter-release/release-receipt.json)。


## 初始审查证据（2026-11-10）

原始审查的 13 项问题和 4 项待核查结果见上方闭环表。下列文件记录修复前的浏览器行为，不代表当前缺陷：

- [对比搜索截图](../benchmarks/public_ux_2026-11-10/compare-flu-search.png)
- [交互记录](../benchmarks/public_ux_2026-11-10/journeys.json)
- [切换后截图](../benchmarks/public_ux_2026-11-10/compare-stale-countries.png)
- [浏览器记录](../benchmarks/public_ux_2026-11-10/browser-observations.json)
- [edge-cases.json](../benchmarks/public_ux_2026-11-10/edge-cases.json)
- [route-audit.json](../benchmarks/public_ux_2026-11-10/route-audit.json)
- [证据目录](../benchmarks/public_ux_2026-11-10)
