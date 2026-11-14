# 更新页、数据发布与控制面验收（2026-11-14）

本次复核 Grok 的 `5e52a1a`、`720fc4d` 和 `8ea11ca`，并继续推进日期范围、每日推送记录趋势及正常发布流程。本文区分已经取得的证据与仍在执行的验收，不以任务入队替代上线成功。

## 页面改动

- 中、英、法三语均支持包含首尾日期的 UTC 范围、跨月归档与更新类型筛选。
- 图表、列表与日期筛选使用同一套 UTC 归组；时间带偏移的记录不会落入不同日期。
- 每日数量同时包含已成功发布的数据更新及所选网站版本，提供所选范围的数据更新/网站版本数量和每日表格。
- 按用户要求移除图表与概览旁的解释段落；保留输入标签和必要的加载/错误反馈。
- 月度归档缺失、损坏或刷新失败时不会画出具有误导性的零值趋势；已知记录仍可查看。
- 柱图带数量刻度和跨年日期标签，手机宽度下保持可读。

## 已验证的数据证据

2026-11-14 **01:35 UTC / 09:35 上海时间**，从已完成 EXPORT_DATA 任务的 acknowledged markers 重新生成投影，与公开 `latest.json`、`2026-10.json`、`2026-11.json` 比较，三个文件全部一致。

| 通道 | 保留的发布记录 |
| --- | ---: |
| Surveillance | 2,218 |
| Situation | 96 |
| Literature | 33 |
| Knowledge | 0 |

11 月 2,338 条、10 月 9 条。计数单位为成功发布批次；不把采集到的每一条观测、失败任务、pending markers 或邮件投递算作成功推送。网站版本另由 changelog 提供。

真实任务中 CA 3,732 条、FR 901 条、US 327 条均具有 collect / organize / store 阶段证据和随后成功发布的关联：

| 来源 | 入库任务 | 关联发布任务 |
| --- | --- | --- |
| CA | `d29c1f22-2d37-4f2d-80b4-07c158089e4c` | `eb09ece8-978b-4d93-acd9-4ea7e8b5cded` |
| FR | `423963cf-d913-4f15-871b-122a65a7f2d9` | `6238fa68-5453-4860-8636-3c1228eec491` |
| US | `9caa4277-e399-4e4b-a6e9-6cb576f3ab6c` | 见脱敏审计 JSON 的 `source_chain_samples` |

另用原样保存的公开 feed 驱动本地新版页面，在 2026-10-01 至 2026-11-14 范围核对：2,350 条数据更新 + 33 个网站版本 = 2,383 条每日图表记录。四个数据类型逐一筛选的数量与源 feed 一致，360/1280 宽度均无横向溢出。该证据是本地新版 UI 结合真实数据，不替代后续线上页面验收。

独立 Literature 任务 `b2332e54-a085-4068-bf71-f13c0e39461a` 于 11 月 14 日 06:48 上海时间完成，数据库与公开 manifest 一致，且公开三语文章仍使用 Papers v3。这证明普通文献发布已修复原有 v2 路径问题。

## 发布问题与代码修复

此前计划任务 `5df136b1-0d76-5b5c-a588-453e136268e7` 的 code-only 发布失败于已发布源数据快照校验。旧清单 14,512 文件，审计时当前 15,368 文件：缺失 11、新增 867、变化 5,480。变化来自后台更新的 Literature / Situation 数据；没有可通过现有路径复用的完整源数据快照。

该场景会反复出现，本次将为后续代码发布保留并验证已发布源数据，在隔离工作目录构建，避免后台持续入库让普通 UI 修改反复要求全量发布。首次历史版本缺少可复用快照时仍需正常发布建立基线，不能修改旧成功收据来绕过校验。

修复 Literature 监控：只有带完整 authority/current/published 收据、三语 canary 与完成任务的 full/code-only 发布才可推进预期版本；按实际提交时间排序。积压报警遵循真实六小时发布周期，加原有 120/600 秒容差；仍保留积压数量并报告失败、卡住或版本不一致。

同时补齐 full-site Papers 的三语 v3 canary：只有公开渲染验证通过才写 `published.json`。提交后验证失败保留原冻结候选和权威提交收据；重试验证同一代，既不重复上传也不回滚。

## 测试与上线记录

已完成：

- 更新页日期工具 5 项单元测试。
- 更新页 5 个视口共 45 项浏览器测试；自适应图最终调整后另复测 360/1280 两个视口 18 项。
- Astro 类型检查 0 errors / 0 warnings；样式边界、三语检查通过。
- Papers 发布、投影与 fence 回归 33 passed / 1 skipped；恢复回归 32 passed。
- 公开更新 feed 后端测试 5 passed。
- 按要求移除说明段落、增加范围内数据/版本数量后，更新页 360/1280 浏览器 18 项通过。
- Literature 公共版本与按调度计算延迟的监控回归 109 项通过；只读采样保留 1,416 个 pending，同时正确显示等待计划时间。
- 文献缺口关联查询改为每批 10,000 IDs；包含真实 32,768 篇边界与 PostgreSQL 展开参数检查的相关测试 128 项通过。
- 控制面板 OpenAPI JSON 与 TypeScript 合同检查通过。
- 重启后真实浏览器登录控制面板：新计划/任务绑定一致，capabilities、任务详情与同源 receipt GET 均返回 200；任务详情显示正确 UUID、运行状态和发布日志，无浏览器错误。运行中的 receipt 保持未确认，未误报上线。
- 文献缺口查询在真实库以只读事务、每条 30 秒超时验证：75,114 篇、85,053 疾病关联、49,209 国家关联，17 条 SELECT 共 13.24 秒，最慢 2.84 秒，最多 10,000 参数。

**历史阶段：前两次 full 候选在上传前失败。0.19.2 后续发布已于 2026-11-14 07:20 UTC 完成；见下文发布证据。**

- 前次任务 `6eda2677-85d3-5fe7-93a9-4d288776e823` 完成全站构建及 Papers 候选准备后，读取基线收据的路径错误导致上传前失败。已改为 capture helper 实际生成的 `.publication/starting-baseline.json`，相关 85 项回归通过。没有修改旧计划、成功收据或线上指针来绕过失败。
- 新计划 `470828c7-410e-453d-97e0-948c4637cfd6`，任务 `bba4cfee-5780-5b81-8f5b-145e88047af0`，通过正式 plan → run API 执行。首次完整快照建立需要 full；后续 code-only 使用独立快照。code-only 不发布独立 Reports，报告模板改动使用 reports_only/full。
- 第二次任务于 02:47:17 UTC 因构建期间源码变化失败，required ready receipt 未保存。已将具体指纹差异提前报告；不伪造收据、不复用失效计划，源码冻结后另建新计划。
- 按用户指示直接重启 worker；确认旧进程退出后按精确 owner 恢复任务，API/worker/AI worker/scheduler 健康检查通过。第二次加载路径修复时 14 个被中断任务均通过 retry API 重新入队，持续运行的 Literature 任务保留。
- 冻结 Papers 候选与当前已发布版本完整比较：8,583 篇、三语各 8,583 路由，共 25,749；缺失 0、描述符变化 0。8,587 个依赖文件的 SHA-256 与长度逐个通过，浏览器 JSON 投影不变。该比较验证本次候选保留文章，不代替新一代上线 canary。

最终需记录计划 ID、任务 UUID、Pages deployment、公开 release 身份、收据的 authoritative/public/database 确认，以及真实公开页面的日期筛选和趋势计数复核。

## 未覆盖或仍有问题的部分

- Literature 最近同步有 28 新增、297 更新；补采因审核积压 73,962（阈值 2,500）暂停，不能表述为全部赶上。
- Situation 聚合任务完成，但 ECDC events 子源存在 HTTP 429；其他来源成功不能掩盖子源失败。
- 最近 24 小时还出现 BR 上游文件、AU 质量隔离、NZ 连接池错误；需按来源持续验收。
- Knowledge 无保留的成功发布记录，不能声称其自动发布链路已验证。
- 本次公共 canary 验证稳定链接、版本响应头与正文来源，不是旧版与新版全部历史文章逐字一致的证明。
- 未发送任何订阅通知；数据发布收据不代表邮件等通知已投递。
- 控制面板 `/publish` 的计划/回执卡目前只保存在当前页面会话；重新打开页面不会自动恢复从其他会话/API 提交的计划，可从 `/publish/tasks?detail=<task_uuid>` 查看任务。此限制尚未在本次页面发布中修复。

## 本地脱敏证据

- `exports/data-updates-acceptance-20261114/publication-audit.json`
- `exports/data-updates-acceptance-20261114/release-flow-audit.json`
- `exports/data-updates-acceptance-20261114/public-feed-database-check.json`

这些运行证据保存在本地忽略目录。本文保留必要的时间、数量、任务标识和验收边界供版本库审阅。

## 0.19.2 隔离集成验收

49个核心候选的本轮职责拆分已经集成，保留事务、锁、任务和发布指针的原所有者。
Compare支持真实下级区域、深链接和JSON刷新，地理标签覆盖中英法；统一页眉页脚、地图隐藏页脚、版本与数据记录独立JSON更新均保留。

主树验证：Python完整首轮4,092通过、21条件跳过，7个失败修正后相关165项全部通过；Dashboard167项，Astro224项，Workers169项测试通过。OpenAPI159路径/208模型无变化，493个Astro文件0错误/0警告，Worker类型、生产配置和dry-run打包通过。66个独立浏览器用例通过，覆盖更新页、Compare、Situation、共享页壳和订阅；截图与脱敏日志在`exports/refactor-0.19.2/`。

首次新full仍是后续小改动复用已发布独立快照的前置条件。此处测试通过不等于生产已切换；实际发布身份及收据在完成后追加。


## 0.19.2 线上发布与收尾（07:20 UTC 后）

- 源码 `29f63aafadbe969d3d2af8fe7bf65d97a738ec35` 已推送 development；生产 API、普通/AI worker、scheduler 和 dashboard 已加载更新。运行时 `weekly_ai_reviews.json` 保留。
- 计划 `1a95832b-d17c-4eed-b9dd-9e5ba93ac3fd`，任务 `49b4ecb0-e229-5d9e-a20e-d4931345204d` 于 07:20:24 UTC 完成，100%；收据 `authoritative_committed`、`public_verified`、`database_confirmed` 均 true。
- 主站 release `20261114T064220Z-29f63aafadbe`；Pages deployment `eeba6591-68e0-4764-b4db-c88994e6f296`。主域与部署域 release.json 一致；3,321 个上传、1,083 个复用。
- Papers 当前 v3 generation `c59ac326861d75d9f0db8ae2e41bdd5ce9355ee530728277e3fe62e16de575f8`，8,770 篇文章、26,310 个三语路由。当前清单引用的 48,684 个对象全部存在。三语 canary、正文/模板哈希、主站与 Papers 同代通过。Reports 上传 1,569、复用 31、清除 40。
- Subscription Worker 已发布 `14d2ab5f-5ad0-43cf-a837-877817f0dfcd`，17 个迁移均已应用，health 200。仍为 pilot 模式，使用 D1 outbox 与 cron；本次维护发布抑制订阅投递，没有为验收发送真实消息。
- 主站 `/site-data/updates/changelog.json` 曾返回 404，而 R2 内容已是 0.19.2；旧 surveillance Worker 路由覆盖了正确的 Pages 产物。部署 `globalid-surveillance-data` 版本 `b1266a5f-c2ec-4107-9618-9032b89bd5ca`，部署前 23 项测试、类型检查及 dry-run 通过。
- 主题兼容 CSS 受 zone 默认 browser TTL 14,400 秒覆盖。新增仅匹配主题 host 和 `/theme/compat/v1/theme.css` 的 respect-origin Cache Rule，普通与带查询 URL 均返回 `max-age=0,s-maxage=30,must-revalidate`，内容哈希不变。浏览器已保存的旧四小时缓存仍需自身到期。
- 收尾延迟根因是 BR DBF 解析线程与发布任务竞争同一解释器 GIL；不是 IPv6 或上传网络。独立进程修复含实际 spawn、数据等价、取消回收和临时目录清理测试。
- 发布完成后只读监控 literature、situation、surveillance 全部 ok；监测数据指针已继续推进。`generated_at` 取观测内容时间，因此重复抓取无新观测时不等于当天发布日期。新鲜度须结合 release 与任务完成时间判断。

此次通过不等于所有上游始终正常：运行尝试仍有部分来源连接池超时与 AI 上游鉴权/空响应，后台重试状态需持续查看；未用 task 最终状态掩盖中间失败。
