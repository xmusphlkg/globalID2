# 2026-11-13 生产部署记录

## 后续切换补充（2026-11-13）

本文件前面的版本表记录维护窗口中的早期兼容部署。随后独立主题和 Papers v3
已完成生产切换：主题收据为
`exports/theme-production-cutover-20261113/theme-receipt.json`，Papers v3 收据为
`exports/site-research-runs/papers-v3-cutover-20261113/published.json`。当前状态以
这两份收据、线上指针和三语 canary 为准；旧版本表中的关闭状态仅属于历史快照。

本次兼容版本已部署并完成线上验收。按用户最后要求，独立 JSON CDN 已撤销：新增镜像代码、配置、公开桶、自定义域名、缓存规则和复制记录均已删除。Bot Fight Mode 保持启用；网站继续使用原有同源 JSON、Pages 和研究 Worker。

## 实际生产版本

| 项目 | 版本 / 状态 |
| --- | --- |
| 主站 | `20261113T040327Z-d1d67eca1c36`，2026-11-13 12:04 上海时间部署 |
| Pages | `globalidv2` / `master`，部署 `dfc0610b-5b52-48fa-9452-282e97f5fa2c` |
| 生产地址 | https://globalinfectiousdisease.com |
| Pages 部署地址 | https://dfc0610b.globalidv2.pages.dev |
| Papers 兼容 Worker | `41238cbf-5dc7-49b6-9897-99b9c2081643`，v3 开关为 `0` |
| 研究内容身份 | 保留 `20261112T152513Z-d1d67eca1c36`，仍为 `template` 模式 |
| 控制台 | Next BUILD_ID `7hJ8-RKeA8TFsHpLKB-75` |
| 后台 | API、普通 worker、AI worker、scheduler 和 web 均已加载最终代码并健康运行 |

发布后台增加发布计划、幂等执行、范围与基线校验、阶段收据、完整候选冻结恢复、主题私有预览及回滚能力，以及文献监控/控制合同。候选准备后恢复仍使用原内容与原目标，凭据运行时重新读取；损坏候选或较新远端版本会阻断恢复。

主站仅发布兼容脚本修复，解决图表销毁后仍执行渐进绘图回调的问题。6,749 个 HTML 的正文、结构、CSS、SSR 参数和 SEO 保留；其中 1,767 页替换 4,086 个 JS 引用。另更新三语首页不可见的发布身份标记。保留全部原资源及原 Pages Worker、路由和响应头。数据读取模块与原生产字节完全一致。

原 `built_at` 保留，页脚展示日期不变；新客户端发布时间另记为 `published_at`。本段描述维护窗口中的历史快照；后续主题、Papers v3 和 Literature JSON 切换见上方补充及对应收据。

## 验证

- 删除 CDN 后的后端集中回归：424 passed；编译及 `git diff --check` 通过。
- 控制台构建：83 页；265 个输入摘要与实际部署构建一致。
- 真实认证 API：直连与代理合计 10/10 通过。
- 控制台浏览器：中英文 × 桌面/手机 × 两个发布页面，8/8 通过。
- 主站同条件候选对照：三语首页/国家/Situation × 双视口，18/18 通过；下载交互 6/6 通过。
- 实际生产主站：18/18 通过；Research 三语目录与英文文章 4/4 通过。
- 线上正文、链接、SEO、计算样式和图表尺寸与候选一致；初始加载和最终语言导航全部 200，无新增 JS 错误、缺失核心资源或独立 CDN 请求。
- Papers Worker 更新前后九个 URL 的正文、链接、元数据与资源合同一致。
- 另对工作区 AI runtime/transport 运行 108 项回归，全部通过；不与上述分组相加。

发布前已有的部分语言预取请求 503 仍存在，最终导航成功；没有将其归为本次新增故障。控制台中文手机表格的长原因文本可能形成较高行，未出现页面横向溢出。

主要证据：

- `exports/publication-deploy-20261113/main-production-verification.json`
- `exports/production-compatible-after-20261113.browser/production-verification.json`
- `exports/publication-audits/dashboard-postdeployment-summary-20261113.json`
- `exports/compat-client-candidate-20261113.receipt.json`
- `exports/papers-v2-production-after-20261113/`

## CDN 撤销与运行恢复

新增 `globalid-public-data` 桶及 3,384 个对象已删除，关联 CORS 随桶移除；`static.globalinfectiousdisease.com` 自定义域名已删除，公开 DNS 返回 NXDOMAIN。两条本次创建的缓存规则及其独立 ruleset 已移除；原权威桶内四条本次复制记账对象已删除，两个原权威指针字节未改变。

后端镜像模块、准备脚本、配置字段、专用测试及环境变量已删除；前端 CDN reader 改动恢复到原实现。旧 CDN 源码副本、候选和执行脚本已清理，只保留撤销审计证据。原始设计文档作为用户提供的历史输入保留，不再代表当前 CDN 实施范围。

三个自动发布暂停控制已于 2026-11-13 12:11:49 上海时间恢复为维护前的 `paused=false`；文献功能本身仍关闭。工作进程均自然排空或按既有机制恢复，没有强杀采集任务。临时排空超时 override 已移除，原 15 分钟 systemd 停止超时恢复。用量集中采集已启用，每五分钟只读刷新；无法确认的套餐和额度仍显示未知。

维护前跟踪的 37 个任务全部保留，验证时 36 个完成、1 个仍运行；数据库 schema 仍为 `0031_ai_success_log_index`。本次未执行数据库迁移、订阅发送或付费套餐升级。

## 保留与回滚资料

`backups/publication-deploy-20261113/` 保留原主站完整产物、原生产部署 ID、原成功发布标记、原运行配置和旧代码，以及完整 PostgreSQL 备份。数据库备份约 65.3 GB，已通过全量 `pg_restore` SQL 流读取校验；没有向数据库执行还原。

主站旧部署为 `1c458c16-fd70-41c1-bf8f-412f103d968d`；旧 Papers Worker 版本为 `145915b0-c375-45ec-ae87-8e638ada000e`；旧控制台独立 release 为 `NesDjgxoAnmcnn6yI6jdI`。如需回滚，应使用对应完整产物并复验，不能仅覆盖 BUILD_ID 或盲目还原数据库。恢复旧发布后端文件时应保留用户其他工作区修改。

新的本地 `dist`、验证快照、成功标记和公开验证标记已经同步到本次实际发布。数据输入快照沿用原生产记录，没有将当前变化中的 `src/data` 冒充已发布数据；后续模板操作若数据前提不满足，会按原约束拒绝。

代码仍在 `development` 工作区，公开发布身份如实标记 `commit_dirty=true`，未宣称已合并或推送 Git 提交。部署源码另保存于本地 `implementation-source.tar`，摘要见 `exports/publication-deploy-20261113/source-snapshot.json`。
