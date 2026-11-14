# 主站、Papers 与依赖 Worker 上线验收

计划发布的 `public_verified` 需要 Pages 发布身份与生产部署匹配，也需要读者实际
访问域名的 `/site-data/updates/changelog.json` 可用并符合完整三语 schema。
`main_site_origin` 指定该读者域名；不能用 Pages 项目的 `*.pages.dev` 代替它，
因为自定义域名绑定的独立 Worker 可能覆盖新 Pages 中相同路径。

网站版本记录独立发布，校验不要求它的最新版本号等于模板版本。接口 404、非法
schema、缺失翻译或非法日期均不得记录成功；错误提示检查并部署
`cloudflare/surveillance-data` Worker、发布网站版本记录。保留已部署产物，修复后
通过原计划恢复，不回滚数据。

## 发布依赖和先后顺序

| 变更 | 必须更新的目标 | 真实网络检查 |
| --- | --- | --- |
| 同源数据路由、bootstrap、updates 协议 | `globalid-surveillance-data` Worker 及其路由 | 主站新接口 200、正确 schema、mutable 入口 no-store |
| Papers 渲染、路由、版本资源规则 | `globalid-papers-template` Worker | 三语文章 projection-v3、内容/模板 SHA、稳定 canonical |
| 页面/组件/共享样式 | 原有 Pages 模板发布；研究页面同时走绑定研究发布 | release.json 身份匹配、真实浏览器资源和交互 |
| 网站版本记录 | 公共 updates 发布器 | 主站 changelog.json 完整；R2 直读成功不能代替主站验证 |
| 主题 v1 token | 独立主题发布器 | 首页/国家页/Compare 引用兼容入口；入口立即重验、内容 CSS SHA 正确 |

新模板依赖新接口时先发布兼容的 Worker，并保留其部署版本和检查结果。部署 Pages
不会自动更新独立 zone Worker。监测、态势、研究的日常 JSON 发布无需重建 Worker；
只有协议/代码/路由发生变化时更新其部署。

2026-11-14 的实际故障：新主站页面请求 `updates/changelog.json`，R2 已有完整
记录，但旧 Worker 不认识该路径而返回 404。默认“全部更新”不能计算完整数量，
趋势图因此隐藏。只部署当前 Worker 即恢复，无需修改趋势逻辑或伪造缺失数量。

## 自动发布门禁和补充浏览器验收

`verify_cloudflare_production_release` 在身份匹配后验证上述公开依赖；失败保持
公开校验未完成，不写 `publicly-verified-site-release.json`，不确认本次发布。
其单元/集成回归包含“release.json 正确但主域 changelog 404”场景。

以下脚本使用真实公开响应，只发 GET/HEAD。浏览器拦截非 GET/HEAD/OPTIONS，
不提交订阅，不发送邮件。先安装 Astro 的项目依赖和 Playwright Chromium。
在仓库根目录执行：

```bash
EXPECTED_RELEASE_ID='<冻结收据中的模板release>' \
EXPECTED_SOURCE_COMMIT='<完整commit>' \
EXPECTED_SURVEILLANCE_RELEASE='<已完成监测发布release>' \
EXPECTED_RESEARCH_RELEASE='<已完成研究发布release>' \
astro-site/node_modules/.bin/node astro-site/scripts/audit-live-release.mjs

astro-site/node_modules/.bin/node astro-site/scripts/verify-data-updates.mjs
```

第一项检查主站身份、监测 current/bootstrap/对象 SHA、研究指针/目录 SHA、三语
双域文章、旧链接和已退役路径、主题 CSS、手机/桌面布局与真实资源加载。可选：

- `LIVE_BASELINE_REPORT`：上一次 `report.json`，输出各通道前后 release 比较。
- `EXPECTED_MIN_SURVEILLANCE_DATE`：要求监测观测内容水位至少达到此 UTC 日期（不是发布日期）。
- `EXPECTED_APP_VERSION`：本次明确要求的网站记录首版本；自动门禁不作此要求。
- `LIVE_AUDIT_OUTPUT`：证据目录；默认 `exports/live-acceptance/<UTC时间>/`。
- `LIVE_API_ONLY=1`：仅接口排查；不能替代完整浏览器验收。
- `LIVE_ARTICLE_SLUG`：有意下架默认样本时，指定当前已发布文章 slug。

第二项验证英/中/法 × 360/1280 六种组合，逐通道及逐日对账真实发布 feed，检查
日期筛选与无横向溢出。默认查询上月首日至当前 UTC 日；可用
`DATA_UPDATES_FROM`、`DATA_UPDATES_THROUGH` 指定范围。`LIVE_UI_OUTPUT` 指定证据
目录；默认 `exports/data-updates-acceptance/<UTC时间>/`。

这些证据验证公开读取链路，不能替代“各来源最新入库水位 → 已发布对象”的数据
比对，也不能证明真实推送已送达。不同通道允许独立 release。若验收期间 current
变化，重新核对新发布代次，不将正常的并发发布误称为数据损坏。

## 缓存及旧路径

稳定研究页面和数据 current 使用 `no-store`；带哈希资源使用 immutable。
正常请求和带查询参数请求应指向相同当前代次。旧主站文章链接保留可访问并以
Papers 为 canonical；`/research-publication/<generation>/research/.../` 的历史
HTML 返回 410，版本化 JS/CSS/JSON 仍供已打开页面保持版本一致。

0.19.4 将三语 Compare 纳入独立主题路由，连同首页/国家页检查兼容入口实际加载。
Compare 筛选面板样式仍由组件负责，不能用“主题已加载”代替标题、边框和移动布局检查。
兼容 CSS 入口应返回
`public, max-age=0, s-maxage=30, must-revalidate`，内容 CSS 使用一年 immutable。
如果 zone Browser Cache TTL 把入口改为 14400 秒，可新增仅匹配
`theme.globalinfectiousdisease.com` 与 `/theme/compat/v1/theme.css` 的 Cache Rule，
设置 `browser_ttl.mode=respect_origin`。更新规则前读取并保留已有规则。CDN purge
不能撤销访问者浏览器中已缓存且未过期的副本。

当前 Papers v3 物理清理使用 `scripts/cleanup_papers_generations.py`，见
[代次保留与清理](../operations/papers-generation-retention.md)。旧
`cleanup_legacy_papers.py` 属于静态站到 v2 的迁移工具，不能直接用于 v3。
清理保留当前代次、最近至少 48 小时的代次及共享依赖；过期代次仅删除精确清单。
路由返回 404/410 不代表存储对象已删除，物理清理必须另有 inventory 和执行收据。

## 研究集合完整性

文章 projection-v3 可访问并不能证明研究目录完整。0.19.3 的模板流程曾过滤所有旧
非文章 HTML，却未重新生成独立通道负责的区域、疾病、主题和每周集合，因此误删
4,782 个有效三语路由。修复必须从当前冻结 JSON 和当前模板重新生成集合，不能
回指旧代次或把历史 HTML 整批补回。

模板流程使用明确的 `collections` 构建范围，跳过文章 HTML，不读取或覆盖文章
HTML 缓存。发布前校验当前全部集合路径、冻结索引摘要及生成文件摘要；疾病目录
包含仅有书目信息的疾病，不能只读取经过筛选的顶层 facets。发布过程对 HTML 的
合法链接重写须通过源摘要和重写后摘要建立对应关系，不能简单跳过摘要检查。

线上补查应覆盖四类集合、中英法及主站/Papers 两个域名；监测数据正常推进导致
验收前后代次变化时，保留首次结果并对新代次重新核对 current/bootstrap/对象摘要。
