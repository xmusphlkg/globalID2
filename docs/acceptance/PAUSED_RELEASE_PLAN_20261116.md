# v0.20.0 暂停现场与恢复计划

状态：按用户要求暂停；等待其他开发进程完成后恢复。本轮不再执行代码修复、测试、
commit、push、tag 或部署。已有提交保留，工作区其他进程的修改保留。

## 已完成与暂停清理

- 当前 HEAD：`1008e3f2cec1cd7b546723a7e3f927429e9b8b53`，branch `development`。
- 六笔已验收提交：`19e323ea`（rc.2）、`7396b1e4`（rc.3）、`e3e160c9`（rc.4）、
  `9d4d52ba`（rc.5）、`46543337`（rc.1/rc.7）、`1008e3f2`（rc.8）。尚未 push/tag。
- 国家合并回归 694 passed；后来纯追加测试所在文件另验 46 passed。CR 维护脚本
  补充回归 36 passed，没有执行生产迁移。
- 用户暂停时三个 subagent 已因工具服务 503 结束；没有仍在运行的本轮测试或
  IE 生成子进程。不重启 subagent 继续业务工作。
- 随机 PostgreSQL 主验收库与 marker 实验库均有 `dropped: true` 收据。
- 临时暂停的 surveillance/situation/literature 自动发布均恢复原来的未暂停状态，
  三次正式 control API 回执 `paused=false/effective_enabled=true`。未暂停其他采集进程。
- 版本历史定时器恢复 active。工作区版本说明恢复已发布的 0.19.5；0.20.0 候选
  保存在 `/tmp/globalid-fixes-20261116/changelog-0.20.0-candidate.ts`，避免提前公开。
- 没有执行本轮 backend restart、Worker 部署或 Pages 发布。

## 尚未签收的事实

1. 非 unit 隔离实库首轮最终 **464 passed、6 failed、1 skipped**，耗时 1836.03s。
   不能报告为整合验收通过。两项 US 夹具失败已用整文件 38 passed 复验；模型索引
   测试已用独立 PostgreSQL 整文件 10 passed 复验，修改仅在测试文件中，尚未提交。
2. 100,000 条 publication marker 场景超时。隔离无统计实验已证明：混合 line/pending
   与 ID 谓词的 generic plan 可退回 line 索引及巨大过滤；仅按 ID 锁读走主键且快。
   typed ANY 单独替换不能修复。尚未完成生产补丁与回归，不能按已解决记录。
3. CI fixture 构建失败的实际错误为三语 RSS 动态 Worker 路径被 SEO 审计误判为
   不存在的静态链接。需核对动态路由合同并补回归，不能全局放宽内部链接审计。
4. Situation static build 达到 300s 超时。该旧测试会直接改共享 Situation 文件、清理
   dist/.astro。恢复后必须先隔离构建输入，检查真实合同再复验，不能直接重复运行。
5. 独立全量 surveillance 导出曾被源码漂移 guard 拒绝，没有完成收据或可发布候选。
   新增的提前 guard 17 passed、冻结阶段独立补验 1 passed，仍未提交整合批。
6. IE 定向正规 CLI 的源指纹一致，但最终 **exit 1**：未选国家 AU 的下载基线缺失
   或无效，禁止复用。它已写过部分本地 JSON，不能签成完整生成，也不能直接发布。
   原始本地数据和下载完整备份 48G 位于
   `/home/likangguo/globalID/acceptance-backups/ie-baseline-20261116`。
7. 新国家来源仍有 holding/public gate；不以消除告警为由放开。外部进程继续导入、
   修订文档或维护数据，之前的 scope 数量和 BQ 状态不能直接当作恢复后的事实。

主要原始证据位于 `/tmp/globalid-fixes-20261116/`：
`nonunit-postgres.log`、`nonunit-postgres-result.json`、`marker-isolated-plans.json`、
`ie-local-export.log`、`ie-local-export-result.json`、`ie-baseline-backup-receipt.json`。
已归档的分批证据见 `docs/acceptance/evidence/20261116_fixes/`。

## 其他进程完成后的顺序

1. **重新冻结范围**：确认其他开发任务已结束写入，读取最新 HEAD、完整 dirty paths、
   来源配置和任务状态。与暂停现场对比，保留外部成果，列出需要追加审查的差异。
   为最终代码及配置建立起止 SHA 清单；若再漂移，停止该次签收。
2. **恢复分工**：国家 subagent 处理来源/迁移增量及 marker 查询；前端 subagent
   处理 RSS SEO 与隔离构建；数据 subagent 处理 IE/AU 下载和全量产物一致性。
   分配互不重叠的编辑文件，主 agent 负责整合与正式发布。
3. **修复剩余问题**：marker 查询必须保留精确 ID、来源通道、pending/own-retry、
   同事务行锁与后批失败全回滚；以 PostgreSQL custom/generic 两种计划验证，覆盖
   32,768/100,000 条及竞争场景，不靠提高超时或 ANALYZE 掩盖问题。
4. **重建完整本地候选**：先核实 AU 失效下载的原因、受影响 scope 及现有备份，
   用正式生成命令修复/重建完整匹配的 JSON 与下载，不手改 manifest 或完成收据。
   运行 country/disease 身份全等检查，验证 IE 原 73 条差异、未选 scope 的不变性、
   来源与映射门禁、行政区地图及对象 SHA/bytes。
5. **整合签收**：复验全部实际失败范围，完成必要构建、OpenAPI、仓库边界/大小和
   clean-tree/readiness 检查。报告保留首轮失败与最终复验的对应关系，不重复累加通过数。
   将剩余修复按可独立审阅的批次 commit，最后确认 v0.20.0 的最终 source commit。
6. **受控发布**：用户之前的部署授权仍有效，但只有所有门槛通过才进入此步。
   维护窗口内安全 drain/restart 后端、更新 dashboard 和兼容的独立 surveillance Worker。
   独立 `content/surveillance` 先发布并验证；在当前配置下 `full` 是 content-only，
   不会刷新独立监测曲线。随后创建全新的正式 full 计划并执行，不复用过期计划。
   所有维护发布设置 `suppress_subscription_dispatch=true`。
7. **公开验证再宣布**：必须同时具备 public_verified 与数据库确认，检查主域
   release 身份、不可变对象摘要、下载、真实 RSS、三语手机/桌面交互。受控发布
   0.20.0 版本历史，再恢复维护临时设置；确认部署 source commit 后 push/tag 并
   归档真实收据。没有线上证据前，不称“已部署”或“全部完成”。
