# v0.18.0 实时数据发布切换

代码分支为 `development`，公开版本为 `0.18.0`。部署继续使用现有 Pages 项目
`globalidv2` 的 production branch `master`；这不要求合并代码仓库的 master 分支。

## 生产设置

| 项目 | 设置 |
| --- | --- |
| Surveillance 自动发布 | `DATA_RELEASE__SURVEILLANCE_JSON_ENABLED=true` |
| Situation 自动发布 | `DATA_RELEASE__SITUATION_JSON_ENABLED=true` |
| Pages 同源数据入口 | `GIDS_PAGES_DATA_WORKER=1` |
| Surveillance manifest | `/site-data/current.json` |
| Situation manifest | `/site-data/situation/current.json` |
| R2 DATA binding | `globalid-papers` |
| 两个独立 R2 前缀 | `site-data/surveillance`、`site-data/situation` |
| 正常国家批次预算 | 8；粗粒度父级范围原子展开 |
| 本地只读监测 | `globalid-realtime-data-monitor.timer`，每 60 秒 |

配置示例中的开关仍默认关闭，先初始化两个 JSON 通道再启用。
生产 `.env` 保持原来的 `0600` 权限；本文件不记录凭据。

现有令牌可以部署 Pages，但缺少独立 Workers Routes 权限。因此数据入口采用 Pages
Advanced Mode：每次模板构建携带 `_worker.js` 与 `_routes.json`，普通 HTML 和资源走
`ASSETS`，JSON 直接流式读取 R2。production 和 preview 已配置 `DATA` binding。
模板发布必须保留 `GIDS_PAGES_DATA_WORKER=1`；删除该打包阶段会撤掉数据入口。

## 初始化与校验

- 全局 Surveillance 初始化：294 个国家及地区、1,362 个逻辑对象，本地导出 233.4 秒；
  原始约 1.12 GB JSON 首次压缩上传约 66.13 MB。
- Situation 初始化：3,831 条审核通报、1,112 个疫情分组、153 周，补齐所有登记国家、
  辖区和疾病的空分片后共 2,011 项；首次上传约 4.84 MB。
- 预览入口已真实读取两个 R2 manifest、地图、国家摘要、疾病曲线和疫情详情。
  `current.json` 为 `no-store`，对象为 `immutable`；gzip/Brotli 解码后字节长度及 SHA-256
  与 manifest 一致。
- 综合回归 775 项通过；密集疾病查询另在隔离 worktree 验证 148 项，含 15 个真实
  PostgreSQL 场景。前端 357 文件检查无错误，16 个桌面/移动数据切换用例通过。

首次受管 Surveillance 回补选择 67 条变更、154 个展开辖区，全部确认至该任务，
未确认其他 ID。此次手工回补临时使用 500 的选取预算，生产日常预算仍为 8。
随后 Situation 自动处理原有 11 条变更及新增事务标记；没有依赖每日静态发布。
停用 Ireland 的 15 条标记保持未发布，监测单列 warning，不能把它们当成功或让它们
阻塞另一个通道。

首轮静态迁移成功渲染 28,227 页，但 SEO 门发现两条法语国家×疾病标题过长而拒绝发布。
修复保留完整国家后缀，未关闭 SEO 检查。密集更新的真实回补也发现旧精确查询成本过高：
68 个疾病同快照查询从 57.831 秒降到 3.871 秒，逐行与完整国家投影相等；现在 ≥16
个疾病使用来源身份去重，1–15 个保持原路径。查询实现变化后需要重新建立完整基线，
由受管发布冻结、上传并确认其明确选中的变更 ID。

本次迁移静态任务设置 `suppress_subscription_dispatch=true`，只执行站点与数据发布。
已有独立订阅配置不随此次切换修改。

第二轮通过 SEO、多语言和性能门后，缓存收尾仍要求已迁往 Worker 的 outbreak JSON，
因此同样在上传前停止。路由和缓存计划现复用唯一模式判断；带两个疫情分组的回归
验证 legacy 继续缓存，而 Pages 模式能够收尾并且不会恢复旧 JSON。
现场监测另发现知识目录批量质量评估占住 worker 事件循环；线程栈确认根因后，
按病种让出执行权，目录结果完全不变，数据回调、心跳和取消可以及时推进。
该后台调度修复通过 151 项知识相关回归，使用平滑重启加载。

## 上线验收

1. API `/health/live`、`/health/ready` 返回 200；scheduler、worker 和监测 timer 正常运行。
2. 生产两个 manifest 返回 200，release ID 与完成任务一致，抽样对象解码后 SHA 匹配。
3. 首页/Changelog 显示 v0.18.0；国家/疾病及 Situation 页面从各自固定版本加载。
4. 新变更可产生独立 JSON 任务，其输出 `pages_deployed=false`；可发布积压归零。
5. 监测最新记录位于 `logs/realtime-data-monitor/latest.json`。完整部署证据位于
   `data/cache/build-verification/20261107-v018-rollout/`，包含任务、HTTP、队列与校验记录。

## 实际生产切换结果

2026-11-07 12:21:41（Asia/Shanghai），静态迁移任务
`bc8ccd0e-f711-45f1-882f-7ee5a6265fe8` 完成，进度 100%，无错误。
站点 release 为 `20261107T034507Z-50787429e5d7`，实现提交为 `5078742`。
Cloudflare canonical production deployment 为
`175bfecf-8417-4c12-9ca3-bee0cfb20c9b`，绑定正式域名
<https://globalinfectiousdisease.com>；同一部署地址为
<https://175bfecf.globalidv2.pages.dev>。

这次首轮模板迁移总计 2,214.305 秒，超过原有 600 秒目标，不能当作日常 JSON
更新耗时。Astro 构建约 236 秒；Papers 上传约 996 秒，上传 21,089 项、复用
9,006 项、清理 296 项，共约 1.944 GB；Reports 上传约 134 秒，共约 439 MB。
Pages 上传新增 7,808 项、复用 505 项，文件传输约 48 秒。部署命令成功后还用了约
342 秒保存本地发布检查点及回退快照。这些步骤不属于两个独立 JSON 发布流程。
本轮 worker 内存峰值约 10.50 GiB，未观察到 OOM 或意外进程重启。

正式域名验收结果：

- 两个 manifest 和 9 个抽样不可变对象均 HTTP 200；解码后的字节数、SHA-256
  与 manifest 及数据库成功收据一致。manifest 为 `no-store`，未遇安全挑战。
- Surveillance release：`data-b3e40074-46f1-48e8-8b67-90458aeebbe8`；manifest
  SHA-256：`2b6e0ffad38dbd0f9ab090512017823988fb702bf1bdf8bc84b0a45e8d4581b1`。
- Situation release：`situation-ce8eacdf-fa7f-4126-9c1a-98939153582b`；manifest
  SHA-256：`e428bd2b66a89714ddfd65e79479c6da0803e30f4764e81af91bf8330c326363`。
- 每分钟 timer 已自然运行多轮，两个通道可发布积压均为 0，无 critical；唯一 warning
  是保留的 Ireland 15 条停用范围。API 健康检查 200，API、worker、scheduler 正常。

同一 production deployment 的浏览器 7 项功能验收全部通过：v0.18.0、两条法语
标题、Situation 地图/周归档/详情懒加载，以及国家和疾病曲线；32 个数据请求成功，
无页面异常或完整性错误。正式主域名的自动浏览器被 Cloudflare 人机验证阻断，
因此未将这项记录为通过，也未修改 WAF。Pages 域名的 Cloudflare Insights RUM
跨域控制台错误单独留存，不属于 JSON 数据请求失败。

真实事务已验证两个通道能独立发布并精确确认所选标记。调度公平性修复重启后的
10 分钟窗口没有新 Situation/Surveillance 事务，因此其真实新发布延迟仍待下一笔
自然变更验证；没有人为修改正式观测数据制造样本。

正式数据验收证据在上述目录的 `production-acceptance/`；浏览器、任务终态与
阶段耗时在 `data/cache/build-verification/20261107-data-architecture/live-static-migration-bc8ccd0e/`。

## 暂停与回退

出现对象或版本不一致时先停止新增发布，保留所有对象、指针收据和 dirty 标记。
可关闭两个自动 JSON 开关并平滑重启 scheduler/worker；已发布的 JSON 入口继续保留。
模板回退也必须保留兼容的 `_worker.js`、路由和 R2 binding，以便已经打开的页面读取
其固定版本。回退后端前先隔离新版 JSON 队列任务，不能让不识别 `data_line` 的旧 worker
将其当普通静态发布执行。不要通过批量确认标记或删除对象来消除监测告警。
