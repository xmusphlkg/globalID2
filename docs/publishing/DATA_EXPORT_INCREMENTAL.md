# Independent surveillance JSON releases

The surveillance update path is separate from static site generation. Its Python
entry point is `src.generation.surveillance_data_export.export_surveillance_data`.
It does not run Astro, generate research/report content, or regenerate CSV/XLSX
archives. The site renderer can continue using a separately attested static
snapshot for its document shell and search metadata.

## Outputs and browser contract

- `exports/surveillance-state/` stores private full country/disease projections,
  projection dependency receipts, and `_release-projection/` for the final
  database acknowledgement. It is not uploaded.
- `exports/surveillance-data/` stores compact browser data, small page summaries,
  immutable content-addressed objects, and `current.json`.
- Logical browser names are `countries/{code}.json`,
  `countries/{code}-source-series.json`, `countries/{code}-summary.json`,
  `diseases/{id}.json`, `diseases/{id}-summary.json`, `meta.json`, and
  `site-status.json`. Country/disease IDs in filenames are lowercase.
- `current.json` has `schema_version: 1`, `release_id`, `generated_at`, and
  `files`. Each file entry has `url`, `sha256`, and `bytes`. By default the URL
  is relative, `objects/{sha256}.json`; `public_url_base` can provide an
  absolute prefix for other custom publishers. The built-in publisher and
  Worker routing contract accept only the default relative object URLs.

A browser reads the pointer once, then uses its pinned immutable file URLs for
all charts and page counters. The publisher uploads all referenced objects
before replacing the remote pointer. It must not replace the pointer if an
object upload fails. Prior objects remain readable for already-open pages.
Object retention/garbage collection belongs to a separate maintenance job: it
must retain every object reachable from retained release manifests, not merely
from the newest pointer.

Country summaries include totals, date coverage, per-disease totals/latest
values, source information, and approved country interpretation fields. Disease
summaries include reporting jurisdictions, coverage dates and a preferred chart
jurisdiction. Archive downloads retain their own generation timestamp; an older
download is not relabelled as a current JSON release. No global sum adds national
totals to their subdivisions.

## Incremental execution

1. The caller selects exact dirty IDs and opens the existing PostgreSQL exported
   read snapshot. The data subprocess imports that snapshot into every database
   transaction.
2. The collector reads only selected jurisdiction observations. It skips reports,
   literature, knowledge-page enrichment and Situation exports. Each country is
   projected once; data-only exports release raw row dictionaries immediately
   after constructing the full source-aware country projection.
   If every selected marker covering a jurisdiction is precise and all changed
   source-series identities are understood by its completed baseline, the
   collector reads only their dependent diseases across their complete history.
   SQL filters the effective reviewed mapping target, rather than assuming a
   registry's historical canonical disease ID is authoritative. Unknown/new
   source identities, coarse parent scopes, missing input summaries and changed
   source configuration fall back to a complete jurisdiction read.
3. Full initialization builds disease views from those country projections,
   rather than projecting the same raw observations a second time per disease.
   Ordinary incremental runs merge selected slices into their full disease
   baseline.
   Private per-disease input summaries retain raw-row incidence source counts,
   monthly heatmap totals and record coverage. Replacing an entire disease
   history also replaces these summaries and recomputes country totals, dates,
   source counts and heatmap; source-only facts and deletions retain their
   original semantics. Unit regressions compare this result with complete
   country projection.
4. A completed country-to-disease dependency receipt identifies which diseases
   actually changed. It hashes every series field, including source observations,
   definitions, quality and supplemental measures. Display/source metadata changes
   invalidate all affected disease views. Removed series invalidate their former
   views. A missing/incompatible receipt conservatively refreshes the whole
   selected country scope.
5. Only affected country/disease browser JSON and summaries are rewritten. The
   writer promotes dependency receipts after all curve writes succeed. Output
   identity checks invalidate reuse after deletion, replacement, or manual edits.
6. The exporter freezes `_release-projection` and watermarks from the same read
   snapshot, writes/reuses immutable JSON objects, and atomically writes
   `current.json` last. It then writes `completed-generation.json` in private
   state. This marker attests the current pointer, all retained baseline inputs,
   and the projection implementation version.
7. The caller uploads a task-specific frozen generation, conditionally updates
   the remote pointer and acknowledges only its explicitly selected dirty IDs
   using the frozen projection. Observations committed during export/upload
   remain pending.

A disease-scoped country projection combines the previous completed versions of
unselected diseases with current versions of selected diseases. It is a
materialized incremental snapshot, not a claim that every disease was re-read at
the same instant. Its country-level source watermark is explicitly unknown;
selected dirty IDs are the acknowledgement authority. Complete country reads
retain their actual country-level watermark.

Static content uses `site_data_export.export(..., content_only=True)`. That path
reads the frozen local country metadata and compact disease coverage; it never
reads current surveillance facts or publication states, generates downloads, or
creates an acknowledgement projection. Missing frozen baselines fail closed.
Coverage has its own local file-identity receipt, so research/report content
updates do not rescan all disease JSON on every export.

`completed_generation(state_dir, public_dir)` returns a valid local baseline or
`None`. Metadata by itself is not a completed baseline. A partial write, missing
file, changed pointer, changed projection implementation or absent completion
marker requires explicit full initialization. The data-only API refuses an
incremental export without this attestation. Initializing from arbitrary local
Astro files is not implicit.

## Measured dependency scope

A read-only test on the existing JP-25 through JP-32 country JSON files measured:

| Operation | Result |
| --- | --- |
| Read eight full country JSON files | 2.448 s |
| Hash complete per-disease inputs for all eight | 1.164 s |
| Change one D038 value and select exact disease dependencies | 0.181 s |
| Previous conservative disease scope | 105 diseases |
| Exact changed disease scope | D038 only |
| Unaffected full/browser disease files avoided | 1,895,440,992 bytes |

This is an input-dependency benchmark, not an end-to-end data release timing.
It did not query PostgreSQL, write production outputs or upload data. The first
receipt initialization remains conservative. Precise scopes still read their
affected diseases' full history; conservative scopes read their full jurisdiction
history from PostgreSQL. Time-window SQL projection and per-window
source-aware merge baselines remain future work. Independent JSON publication
removes static rendering and archive generation from the regular data-update
critical path without claiming that those database costs have disappeared.

## Real-database local JSON measurements

The integration benchmark used exported PostgreSQL read-only snapshots and
isolated `/tmp` outputs for JP-25 through JP-32. It never wrote formal data,
acknowledged changes, built HTML, or uploaded to the network. A synthetic case
correction changed one D038 source observation in memory after the read; the
same correction was used by the independent complete-country reference.

| Run | Measured result |
| --- | --- |
| Initial data-only full projection of eight regions | 119.533 s total; 504 immutable objects created |
| Repeating all eight regions without precise disease hints | 113.576 s; zero changed files or bytes |
| One-disease SQL filter after per-row category resolution | 47.286 s; source SQL/decoding 44.201 s |
| Optimized warm single-source correction | **3.903 s** total; source SQL/decoding 0.951 s; country builder 0.015 s |
| Independent complete JP-25 projection with the identical correction | 57.263 s; full country dictionaries equal, ignoring only the generation label |
| Eight-process complete projection of eight regions | 70.5 s projection, 77.692 s total; existing objects reused |

The SQL improvement materializes the requested active mapping categories once,
then filters a safe superset of candidate observations before applying the
original best-category selection and effective disease check. The earlier
query repeated the category/mapping lookup over unrelated historical rows.
Filtering only after that lookup did not solve the SQL bottleneck.

The warm correction changed five logical JSON files out of 504, representing
10,863,904 uncompressed bytes. A separate local simulated uploader verified and
compressed those actual objects in 0.786 s: 609,662 compressed object bytes plus
204,251 control bytes. That simulation did not exercise network upload, remote
storage or database acknowledgement and must not be presented as production
end-to-end latency.

The warm correction's peak process RSS was 505,844 KiB. The eight-process run
observed a 4,181,440 KiB sum of process-tree RSS, sampled every 0.2 s after
startup; shared mappings are counted more than once. Its parent peak was
713,268 KiB and largest completed child peak 503,840 KiB. Process workers have
independent database engines and import the same exported snapshot. They return
projected country data and small input summaries, not raw observation rows.
Cancellation terminates and joins every reader before the snapshot owner can
close. The CLI sizes workers conservatively from the service cgroup budget;
the Python API defaults to one process.

The complete-projection runs had different object cache/format states, so the
reported totals are observations rather than a controlled claim of speedup from
process count alone. Cold full history remains materially slower than an
ordinary precise update. The following optimization now resolves each distinct
source identity once, instead of once per historical row, for complete reads.

## Complete-projection optimization

The full reviewed-mapping query materializes eligible observations without raw
provenance, computes the effective source code/label once and resolves each
distinct identity's category before joining its dated assertions. JP-25 category
lookups fell from 60,319 to 112. Controlled read-only-snapshot comparisons returned
the same 59,704 complete normalized rows: original query 49.248 s to identity
resolution 5.916 s; a second comparison reduced identity resolution from 5.654 s
to precomputed identities at 5.085 s. The final SQL server time was 3.460 s.

Call-local source-summary, period-key and cutover checks reduce pure computation
on the same captured JP-25 input from 3.959 to 2.757 CPU seconds (medians of five
alternating old/new runs). Complete country, browser and source payloads equal
the saved reference on every run. These are not persistent
caches and do not permit reuse after a projection-policy change.

The new eight-process run started with empty output directories: projection
19.7 s, full local export 27.682 s, 504 newly created immutable objects. Every
logical public object's hash equals the previous complete run. The process-tree
RSS observation peaked at 4,302,128 KiB; parent peak 709,172 KiB. PostgreSQL buffers
were warm and the earlier full run reused objects, so these whole-run observations
are not a controlled process-count comparison.

A synthetic single-D038 correction on that newly initialized baseline took
4.608 s, with source reading/selection 0.929 s and country builder 0.006 CPU s.
Five newly created objects changed; 499 remained unchanged. It excludes network
publication and database acknowledgement, as do the other local export timings.

Projection-source fingerprints now cover the series, reporting-period and cutover
policy helpers. Mapping release activation invalidates the old/new country scope
union transactionally, including withdrawn targets. Further period-sharded
materialization remains future work. See [PROJECTION_PERFORMANCE.md](PROJECTION_PERFORMANCE.md)
for the limits and the distinction between database work, pure projection and
publication.

Raw logs, benchmark scripts, complete-equivalence results, memory samples and
the local uploader simulation are archived under
`data/cache/build-verification/20261107-data-architecture/`.


## Browser protocol and template boundary

国家数据更新不再执行 Astro。Astro 负责页面布局、已登记的国家/疾病 URL、审定知识、报告和研究内容；浏览器从独立数据发布读取当前监测数据。数据导出和上传不以修改静态 HTML 为完成条件。

### 数据读取协议

默认入口为同源 `/site-data/current.json`；`PUBLIC_GIDS_DATA_MANIFEST_URL` 可显式配置其他数据域。跨域配置需要该域正确提供 CORS。入口包含 `schema_version: 1`、`release_id`、`generated_at` 和 `files` 映射。每个逻辑文件具有相对入口解析的 `url`、原始字节 `sha256` 及可选 `bytes`。例如：

```json
{
  "schema_version": 1,
  "release_id": "publication-identity",
  "files": {
    "countries/jp.json": {
      "url": "objects/content-sha256.json",
      "sha256": "64-character-sha256"
    }
  }
}
```

发布者先上传全部不可变对象，最后替换入口。旧对象必须保留，供已经打开的页面继续读取。入口应使用 `no-store`/重新验证语义；内容寻址对象可长期缓存。

`astro-site/src/lib/surveillance-data.ts` 在一页中只成功读取一次入口。指标摘要、曲线、热图和稍后打开的来源详情共享这个版本。入口切换不会让同页混入新数据；新导航读取新的入口。每个对象在 JSON 解析前验证长度和 SHA-256。入口存在但文件缺失、长度不符、摘要不符或格式损坏时显示数据不可用，不回退到其他版本。

指标和目录复用小型 JSON 响应。曲线解码器只缓存归一化结果，并通过 `retain: false` 在解码后释放传输层的原始 compact 对象，避免长期同时持有两份历史数组；并发读取仍合并为一次对象请求。

入口返回 404 时保留旧曲线 URL 的迁移兼容。网络错误、403 或格式错误不被当作“尚未初始化”。初始化后的文件请求失败可以重试，仍使用已经固定的发布版本。

### 轻量摘要与曲线

国家摘要 `countries/{code}-summary.json` 提供覆盖期、疾病数、病例/死亡总数、疾病目录、监测说明、来源和明确版本的下载元数据。疾病摘要 `diseases/{id}-summary.json` 提供国家级覆盖、报告国家目录和下载元数据。列表和计数不用下载整段历史。曲线继续使用既有 compact schema；来源观测保持单独按需加载。

首页统计/最近数据、About 覆盖日期与数据统计、国家目录筛选与地图、国家详情指标/疾病目录/分省目录、疾病详情覆盖/国家目录、国家×疾病详情指标和下载入口均读取当前摘要或 `meta.json`。比较表从运行时曲线生成行，不再传递构建时的病例数。图表组件不根据构建时有没有数据而省略，已登记但暂未有数据的国家也预生成模板。

对比工具的疾病身份来自模板目录，可选性和国家数量来自当前 `meta.disease_coverage` 或已加载曲线，不再取旧下载档案的国家覆盖。页脚的数据日期也固定到同页发布。全站搜索保留静态身份和内容目录，国家摘要不再嵌入会变化的疾病数/观测日期。疾病目录卡片只显示身份、编码和审定描述，季节性热图读取当前曲线。

Situation 的审核通报保留独立内容版本；其“监测背景”不再将国家疾病数、最新观测或 sparkline 写进 HTML。浏览器用当前 meta 更新国家背景，并在选择疾病/通报时读取轻量疾病摘要的最后十二个病例点重建 glance，数据未加载或失败时不显示旧观测。

下载 CSV/XLSX 有独立生成周期。摘要保留下载文件自己的时间戳；没有下载元数据时提供现有 `/downloads/` 入口并说明独立更新，不把旧档案标成当前曲线版本。

SEO 标题/描述不嵌入变化中的病例数或覆盖国家数。审定知识、来源说明和索引策略属于模板/内容发布，监测 JSON 更新不隐式改变 robots 策略。首次生成索引元数据仍可使用既有离线目录投影；这不处于日常 JSON 发布的关键路径。

### 首次上线与后续流程

1. 先初始化不可变数据对象和 `current.json`，启用同源数据路由并验证摘要、曲线及校验值可读。
2. 再部署一次前端模板。新摘要在旧站点目录并不存在，不能通过旧 compact 曲线安全推断权威总数或下载版本；上线顺序保证用户不会遇到迁移空窗。
3. 后续已有国家/疾病数据更新只执行数据库快照导出、变更对象上传和入口提交。浏览器新导航即读取新版本，HTML、Vite 构建和全站输出扫描均不参与。
4. 布局、客户端代码、审定内容或新增国家/疾病身份/slug 改变时，再运行模板/内容发布。当前静态路由无法在没有模板发布时凭空新增 URL；这是拓扑变更边界，与已有 URL 的观测更新不同。

受管生产入口同时提供 `GLOBALID_SKIP_SITE_DATA_GENERATION=1` 与 `PUBLIC_GIDS_RELEASE_ID` 时，构建管线要求 `GIDS_RUNTIME_DATA_READY=1`，在任何 stage 开始前拒绝缺少初始化标志的旧 worker。普通本地和 fixture 构建不受此迁移条件限制。Python 发布入口也必须在使用完整构建收据之前检查该配置，不能靠跳过 npm 绕过初始化边界。

`GIDS_EXTERNAL_SURVEILLANCE_DATA=1` 进一步约束模板产物：`build-site.mjs` 建立独立 public 输入视图，仅排除 `site-data/countries/`、`diseases/`、`objects/`、`current.json`、`meta.json`、`site-status.json`。Research、Reports、地图、flags 和其他静态资源保留。视图用源 symlink 避免再复制大目录，Vite 写出到自己的 dist；源 public 目录不被重命名或修改。构建后删除输入视图。模式和 manifest 配置进入共享 fingerprint，不能复用另一模式的页面缓存。默认 legacy 模式仍复制原始 public，便于分阶段迁移。

### 验证

`surveillance-data.test.ts` 覆盖固定版本、入口失败可重试、404 迁移、缺失对象及错误 SHA/字节数。`e2e/surveillance-publication.spec.ts` 用不可变对象和入口切换验证：静态 HTML 摘要不变，指标/覆盖与图表随 JSON 更新；晚加载的图表仍使用本页原版本；损坏对象不显示旧摘要。

HTTP gzip 的对象摘要和长度以 Fetch 解压后的 JSON 原始字节为准，不使用压缩表示的 `Content-Length`。读取使用 `credentials: same-origin`，保留主站已有浏览器 cookie，同时不向显式跨域数据源发送 cookie。相应协议单测及 public 输入隔离/生产迁移 guard、Compare 当前覆盖、Situation 当前监测背景的针对回归均已补充。

2026-11-07 的隔离 fixture 构建在 legacy/external 两种模式均生成 112 个页面，通过 SEO、性能预算、多语言和缓存提升；external 模式实际产物不含监测 JSON，源 public、Research/Reports 和静态资产完整保留。12 个协议/构建单测通过。桌面 1440px 与移动 390px 的五项浏览器回归共 10 个用例均通过：国家指标/曲线、疾病指标/曲线和延迟固定版本、Compare 当前覆盖、Situation 当前监测背景、损坏对象关闭读取。国家/疾病 HTML SHA-256 在 JSON 更新前后不变。

另一个真实浏览器联调包含国家总数 120→900、图表数据 20→520、疾病覆盖年份切换、404 曲线兼容，浏览器无脚本异常。Astro 检查 349 个文件为 0 errors、0 warnings；9 个既有 vendor hints。这些是协议和浏览器集成验证，不是生产网络延迟或数据库导出吞吐量基准。日志保存在 `data/cache/build-verification/20261107-runtime-shell/`。


## Surveillance publication queue

`DATA_RELEASE__SURVEILLANCE_JSON_ENABLED=true` 启用独立监测数据通道，默认关闭，
保留原有站点发布行为。启用后，scheduler 在每次 release poll 以及上游任务完成后
检查待发布监测变更，不依赖站点的每日时间、`auto_after_crawls` 或静态发布总开关。
目的配置沿用第一个已启用的 release job；禁用全部 job 会停止新的自动 JSON 发布。

独立任务继续使用持久化 `EXPORT_DATA` 类型，以 `input_data.data_only=true` 和
同名 metadata 字段区分。模式与 `full`、`reports_only`、`code_only` 互斥。
任务仅选择 `publish_dirty.line=surveillance`；普通站点任务在此开关开启后排除该行。
明确冻结的 dirty IDs、国家范围和批次 continuation 保持原有一致性保护。
普通站点任务同时冻结 `content_only=true`，包括 full 模式；code-only、reports-only
和 data-only 的该标记为 false。筛选条件与标记来自同一次入队配置快照，执行或重试
时不能根据后来的配置值改变此合同。JSON 在获取队列预留后再次核对所选 dirty IDs
仍待发布，避免等待期间其他发布已经确认这些 ID 却创建过期候选。

JSON 任务进入独立单槽 worker 通道。同一时刻只运行一个 JSON 发布，因为它们更新
同一个 JSON manifest；站点构建、原始归档、JSON 发布和采集可使用各自的通道。
该通道不占用静态 release 或普通采集计数，即使其他通道容量耗尽也能被领取。
当前 `release_admission.task_scope` 不对 EXPORT_DATA 获取任务级独占屏障，JSON
任务的只读快照因此不会停止采集。

过渡期保守处理旧任务：缺少 `content_only=true` 的旧普通站点任务仍可能导出并
确认监测数据，因此同时占有静态和 JSON 通道。JSON 与这些任务的 queued、running
和 retry reservation 双向互斥；新的 content-only、code-only 和 reports-only
任务可与 JSON 并行。若关闭 JSON 开关时仍留有 data-only 的 RETRYING 预留，必须
先完成或明确取消该任务，旧站点数据流程才能继续，不能绕过预留混合发布状态。

JSON 触发不更新静态 job 的 `next_run_at`、状态计数或每日定时器。任务与重试结果
仍通过普通任务列表可见。瞬时失败使用原有持久化 automatic retry 规则；active
reservation、失败冷却及重试重排均按 JSON/静态通道隔离。JSON 失败不会阻止模板
发布，模板失败也不会停止 JSON 数据重试。关闭 JSON 开关后，自动 continuation
与尚未到期/尚未重排的 JSON 重试保留在数据库中，重新启用后恢复检查。

上线前按当前运行时流程排空并重启 scheduler 和 worker，以载入新的领取规则。
运行中的旧代码不会获得新通道。首先完成独立数据端点及静态客户端的部署准备，再
启用配置；启用开关不是静态客户端切换或外部存储配置的替代步骤。

验证包括真实 SQLite 任务队列上的数据/站点互不阻塞、同 JSON 串行、监测 dirty
过滤、旧开关行为、冻结 continuation、隔离冷却和重试，以及禁用静态定时器时仍
执行 JSON poll。该测试证明编排边界，不代表数据库导出或网络上传的时延测量。


## Curve query and presentation contract

### 产品边界

这个模块帮助用户选择监测序列、时间和指标，再查看同一查询结果的图表或表格。文件下载使用所在页面的“数据下载”区域；曲线内不重复放下载按钮。国家、疾病、报告和研究入口共用查询与展示逻辑，入口只负责提供初始选择和数据来源。

默认展示一条有有效观察值的序列。勾选第二条序列即可比较，取消勾选即时移除。常用操作直接生效；来源和方法细节按需展开。全屏按钮沿用站点图表控件，退出后回到原页面位置。手机端用选择弹窗，桌面端用紧凑的左栏；两端共享状态和结果。

### 数据契约

- 完整国家、疾病 JSON 和浏览器紧凑投影必须包含相同的观察日期、病例数、死亡数、周等价值及来源身份。来源明细按需加载，不会混入默认发布曲线。
- 频率优先使用来源声明。旧数据缺少声明时，只在日期序列足够规律的情况下推断展示频率，标记为 `date_pattern`，界面加 `*` 并说明依据。稀疏或混杂日期保留 `unknown`。
- 日期推断只用于显示提示；历史对齐、周等价换算、完整窗口总计和跨来源加总仍要求可信的来源周期与可比性。缺少依据时显示空值或解释，不推造结果。
- 没有默认曲线但有来源观察值的疾病保留在索引中，供用户按需选择来源；其默认日期和数值数组保持为空。
- 图表与表格消费相同的查询结果。分享链接保存选择、指标和时间范围；数据下载区域仍提供完整机器可读文件。

### 实现边界

`surveillance/adapters` 把各入口数据映射到统一目录；`analysis` 计算查询结果和诊断；`state` 保存当前配置；`components` 只负责操作与呈现；`presentation` 放标签与显示导出。入口组件不复制筛选、频率判断或图表逻辑。样式由 `surveillance.css` 使用站点设计变量，避免页面级零散覆盖。

新增来源或指标时，先完善数据契约与适配，再为其特殊口径增加必要的查询测试。预测、异常识别、地区求和等独立分析任务不塞进日常查询界面；确有用户需求时另行设计。

### 验证

`npm run check:surveillance-data` 对账完整与紧凑投影，以及懒加载来源文件。2026-10-12 本地快照：239 个疾病文件、110 个国家文件，各有 5,657 条对应序列；45 个仅有来源观察值的条目保留；4,339 条来源序列对账一致。原先 2,128 条未声明频率的曲线中，1,966 条得到保守的日期规律提示，162 条仍为未定义。

`npm run test:charts`、`npm run check`、`npm run build:with-data` 和浏览器桌面/手机回归验证查询、全屏、来源选择、表格、样式与构建。这些检查保证投影和界面行为一致，不代表上游来源口径已得到外部核实。
