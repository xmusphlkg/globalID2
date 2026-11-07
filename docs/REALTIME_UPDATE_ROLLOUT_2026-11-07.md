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
  PostgreSQL 场景。前端 356 文件检查无错误，16 个桌面/移动数据切换用例通过。

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

## 暂停与回退

出现对象或版本不一致时先停止新增发布，保留所有对象、指针收据和 dirty 标记。
可关闭两个自动 JSON 开关并平滑重启 scheduler/worker；已发布的 JSON 入口继续保留。
模板回退也必须保留兼容的 `_worker.js`、路由和 R2 binding，以便已经打开的页面读取
其固定版本。回退后端前先隔离新版 JSON 队列任务，不能让不识别 `data_line` 的旧 worker
将其当普通静态发布执行。不要通过批量确认标记或删除对象来消除监测告警。
