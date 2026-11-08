# 静态模板与监测数据发布边界

国家数据更新不再执行 Astro。Astro 负责页面布局、已登记的国家/疾病 URL、审定知识、报告和研究内容；浏览器从独立数据发布读取当前监测数据。数据导出和上传不以修改静态 HTML 为完成条件。

## 数据读取协议

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

## 轻量摘要与曲线

国家摘要 `countries/{code}-summary.json` 提供覆盖期、疾病数、病例/死亡总数、疾病目录、监测说明、来源和明确版本的下载元数据。疾病摘要 `diseases/{id}-summary.json` 提供国家级覆盖、报告国家目录和下载元数据。列表和计数不用下载整段历史。曲线继续使用既有 compact schema；来源观测保持单独按需加载。

首页统计/最近数据、About 覆盖日期与数据统计、国家目录筛选与地图、国家详情指标/疾病目录/分省目录、疾病详情覆盖/国家目录、国家×疾病详情指标和下载入口均读取当前摘要或 `meta.json`。比较表从运行时曲线生成行，不再传递构建时的病例数。图表组件不根据构建时有没有数据而省略，已登记但暂未有数据的国家也预生成模板。

对比工具的疾病身份来自模板目录，可选性和国家数量来自当前 `meta.disease_coverage` 或已加载曲线，不再取旧下载档案的国家覆盖。页脚的数据日期也固定到同页发布。全站搜索保留静态身份和内容目录，国家摘要不再嵌入会变化的疾病数/观测日期。疾病目录卡片只显示身份、编码和审定描述，季节性热图读取当前曲线。

Situation 的审核通报保留独立内容版本；其“监测背景”不再将国家疾病数、最新观测或 sparkline 写进 HTML。浏览器用当前 meta 更新国家背景，并在选择疾病/通报时读取轻量疾病摘要的最后十二个病例点重建 glance，数据未加载或失败时不显示旧观测。

下载 CSV/XLSX 有独立生成周期。摘要保留下载文件自己的时间戳；没有下载元数据时提供现有 `/downloads/` 入口并说明独立更新，不把旧档案标成当前曲线版本。

SEO 标题/描述不嵌入变化中的病例数或覆盖国家数。审定知识、来源说明和索引策略属于模板/内容发布，监测 JSON 更新不隐式改变 robots 策略。首次生成索引元数据仍可使用既有离线目录投影；这不处于日常 JSON 发布的关键路径。

## 首次上线与后续流程

1. 先初始化不可变数据对象和 `current.json`，启用同源数据路由并验证摘要、曲线及校验值可读。
2. 再部署一次前端模板。新摘要在旧站点目录并不存在，不能通过旧 compact 曲线安全推断权威总数或下载版本；上线顺序保证用户不会遇到迁移空窗。
3. 后续已有国家/疾病数据更新只执行数据库快照导出、变更对象上传和入口提交。浏览器新导航即读取新版本，HTML、Vite 构建和全站输出扫描均不参与。
4. 布局、客户端代码、审定内容或新增国家/疾病身份/slug 改变时，再运行模板/内容发布。当前静态路由无法在没有模板发布时凭空新增 URL；这是拓扑变更边界，与已有 URL 的观测更新不同。

受管生产入口同时提供 `GLOBALID_SKIP_SITE_DATA_GENERATION=1` 与 `PUBLIC_GIDS_RELEASE_ID` 时，构建管线要求 `GIDS_RUNTIME_DATA_READY=1`，在任何 stage 开始前拒绝缺少初始化标志的旧 worker。普通本地和 fixture 构建不受此迁移条件限制。Python 发布入口也必须在使用完整构建收据之前检查该配置，不能靠跳过 npm 绕过初始化边界。

`GIDS_EXTERNAL_SURVEILLANCE_DATA=1` 进一步约束模板产物：`build-site.mjs` 建立独立 public 输入视图，仅排除 `site-data/countries/`、`diseases/`、`objects/`、`current.json`、`meta.json`、`site-status.json`。Research、Reports、地图、flags 和其他静态资源保留。视图用源 symlink 避免再复制大目录，Vite 写出到自己的 dist；源 public 目录不被重命名或修改。构建后删除输入视图。模式和 manifest 配置进入共享 fingerprint，不能复用另一模式的页面缓存。默认 legacy 模式仍复制原始 public，便于分阶段迁移。

## 验证

`surveillance-data.test.ts` 覆盖固定版本、入口失败可重试、404 迁移、缺失对象及错误 SHA/字节数。`e2e/surveillance-publication.spec.ts` 用不可变对象和入口切换验证：静态 HTML 摘要不变，指标/覆盖与图表随 JSON 更新；晚加载的图表仍使用本页原版本；损坏对象不显示旧摘要。

HTTP gzip 的对象摘要和长度以 Fetch 解压后的 JSON 原始字节为准，不使用压缩表示的 `Content-Length`。读取使用 `credentials: same-origin`，保留主站已有浏览器 cookie，同时不向显式跨域数据源发送 cookie。相应协议单测及 public 输入隔离/生产迁移 guard、Compare 当前覆盖、Situation 当前监测背景的针对回归均已补充。

2026-11-07 的隔离 fixture 构建在 legacy/external 两种模式均生成 112 个页面，通过 SEO、性能预算、多语言和缓存提升；external 模式实际产物不含监测 JSON，源 public、Research/Reports 和静态资产完整保留。12 个协议/构建单测通过。桌面 1440px 与移动 390px 的五项浏览器回归共 10 个用例均通过：国家指标/曲线、疾病指标/曲线和延迟固定版本、Compare 当前覆盖、Situation 当前监测背景、损坏对象关闭读取。国家/疾病 HTML SHA-256 在 JSON 更新前后不变。

另一个真实浏览器联调包含国家总数 120→900、图表数据 20→520、疾病覆盖年份切换、404 曲线兼容，浏览器无脚本异常。Astro 检查 349 个文件为 0 errors、0 warnings；9 个既有 vendor hints。这些是协议和浏览器集成验证，不是生产网络延迟或数据库导出吞吐量基准。日志保存在 `data/cache/build-verification/20261107-runtime-shell/`。
