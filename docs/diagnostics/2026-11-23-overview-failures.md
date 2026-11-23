# 2026-11-23 Overview 失败任务诊断与首批修复

本次核对的是运行中 PostgreSQL 的只读查询、现有任务输入和阶段记录。
统计快照：北京时间 **2026-11-23 12:54**，回看任务创建时间最近 48 小时。
首轮诊断未修改生产任务状态、采集事实、发布标记、密钥、模型额度或调度配置，未重启服务。
后续按用户要求暂停了韩国年度调度，修复了陕西入口和跨来源调度公平性，并重启 API 与
scheduler；具体执行结果和仍未加载的工作进程边界见文末“后续处理”。

## 主要结论

最近 48 小时共 193 个失败任务：采集 158、文献 AI 27、模型业务 8。
其中 154 次采集失败来自同一个配置问题：韩国两个年度接口缺少密钥，却仍每 30 分钟调度。
Overview 的 11 个失败阶段中，8 个已被同参数的新名称任务成功重跑覆盖。
历史失败是真实发生过的，但继续把这些记录当成当前未恢复问题，是告警判定的缺陷。

| 优先级 | 问题 | 当前证据 | 处理 |
| --- | --- | --- | --- |
| P1 | 韩国年度死亡、地区接口缺少凭证 | 154 次失败；两个计划都启用且间隔 30 分钟；月度接口支持门户备用路径 | 入队前检查依赖，缺少密钥时跳过并显示原因；配置密钥后恢复 |
| P1 | 文献模型账户额度耗尽 | 27 次失败中 19 次含额度耗尽，3 次其他限流、3 次空回答、1 次超时、1 次排队超过 6 小时 | 中文额度错误接入现有 15 分钟最低冷却；额度仍须在供应商侧恢复或配置可用模型路由 |
| P1 | 已恢复导入继续出现在 Overview | 8 个后续任务国家、来源、完整输入一致，状态 completed；任务名由 full history import 改成 verified high-frequency import | 读取时识别恢复，同时保留原始失败历史 |
| P2 | 陕西来源连接失败 | 11 月 16 日任务的 SSL EOF；本次旧入口的 HTTP、HTTPS 及首页均在 15 秒内超时 | 核实官方可用入口后验证报告月份、字段与解析；不能据此判定官方服务永久下线 |
| P2 | 泰国 DDS 传输失败 | 历史 SSL 失败 1 次；本次最新任务返回 HTTP 503，状态 retrying | 已进入现有 transient_transport 自动重试，需观察重试结果 |
| P2 | PostgreSQL 共享内存不足 | 摘要修复失败 1 次，错误为无法扩容 `/PostgreSQL.*` 共享内存段 | 后续摘要任务已完成；检查数据库宿主机 `/dev/shm` 和并行查询，不能仅据此判断数据盘满 |
| P3 | WHO GHO 的 EC、PA、CR 请求失败 | 3 次上游 502/504；三者后续同名任务均 completed | 保留失败历史，暂无需盲目重跑 |

其他 7 次模型业务失败来自 503 等服务错误。文献 AI 任务失败不代表整批产物都未生成：
`2cceea0e-eede-41dc-9c6b-7ebcd8c83b4a` 仍记录 generated=63、skipped=84、failed=3；
模型活动或审核子流程失败会使总任务失败。

## 已证实恢复的 8 个导入

| 国家 | 来源 | 成功任务 UUID |
| --- | --- | --- |
| BN | who_flunet_weekly | ad02a3b3-7021-4dfd-ac67-e76a1e003af7 |
| CL | who_flunet_weekly | e7099cb1-1aec-454b-a736-55404434ef9d |
| CR | who_flunet_weekly | 4675f8a0-80d5-49d7-acf4-191ed4acd960 |
| CY | ecdc_respiratory_weekly | 48696869-198e-46b1-a377-08281e1aafa1 |
| DE | ecdc_respiratory_weekly | ea0663cc-db7c-4303-a712-9457ffc04cde |
| DK | ecdc_respiratory_weekly | 3ff907b6-3ca1-4b38-9b70-9e2744d5206f |
| MX | mx_dge_weekly | 931b8b55-0b73-4cd1-ae0e-ebecfcf83883 |
| MX | mx_dge_monthly | b3ce41f0-beb6-48d1-b322-6cf2a0518a3e |

这些成功任务都创建于 11 月 17 日，晚于对应失败任务。抽查 store 记录：
BN 周度 13,600 行、MX 周度 761 行、MX 月度 6,648 行。
因此此前的 7 个 availability assertion 错误和 MX 周度 registry coverage 错误，
当前有恢复证据；本轮没有放宽它们原有的数据质量门禁。

两个 CO BES 模型抽取历史失败、一个 CR 全历史验收失败仍留在行动项中。
虽然另有 CO 历史入库和 CR 定期采集成功记录，输入或任务类型不同，
不足以证明原批次/完整历史已经恢复，不能合并清除。

## 本轮代码变更

- `src/core/source_credentials.py`：共享 KDCA 密钥解析，支持配置指定的变量及
  `KDCA_EID_SERVICE_KEY`，与 crawler 使用同一规则；月度门户采集保持可用。
- `src/services/automation_service.py`：缺少年度接口密钥时记录 skipped 和明确原因，
  按原周期再次检查；源健康显示 needs_review。保留既有任务历史和手动暂停语义。
- `src/ai/catalogue/errors.py`：识别“额度已用尽”和“额度已耗尽”。已有模型级冷却
  策略据此至少等待 900 秒，保留其他模型路由；普通每分钟限流仍按短期退避。
  这会减少无效重试，不会增加或恢复供应商账户额度。
- `src/control_plane/task_recovery.py`、Overview 和 collection API：跨名称识别
  同国家、相同完整输入的后续成功采集；参数不同、另一来源、另一国家、运行中、
  取消、失败、旧成功任务以及测试任务不能作为恢复证据。历史任务状态不变。

修复后的查询在生产库的只读试算中返回 **3 个失败阶段**：韩国两个年度来源及陕西。
试算不是已上线的页面结果，也不包括所有模型业务失败。

## 验证与上线边界

首轮以下测试共 **128 passed**：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_source_credentials.py \
  tests/unit/test_automation_state_refresh.py \
  tests/unit/test_collection_failed_stages.py \
  tests/unit/test_control_plane_overview.py \
  tests/unit/test_model_center_runtime_health.py \
  tests/unit/test_model_center_catalogue_policy.py \
  tests/test_kr_kdca.py
```

补充旧成功和测试任务不构成恢复的边界用例后，失败阶段、overview 与任务进度相关
**35 项测试通过**；新增模块的 Ruff 检查和 `git diff --check` 通过。
两轮用例有重叠，不应把它们加总成独立测试数量。

生产库只读查询使用 `default_transaction_read_only=on`；恢复判断读取原始完整输入，
没有将失败状态改写成成功，也没有触发采集或发布。

上线需要 API 加载恢复判定，scheduler 加载凭证检查，模型执行进程加载额度分类。
模型工作进程需按既有 drain/restart 流程更新，避免在途任务被直接中断。
随后检查新韩国失败是否停止增长、来源健康提示是否可见，以及模型路由冷却是否生效。
韩国年度数据仍需真实密钥；首轮诊断时陕西尚需来源访问修复。

## 后续处理：韩国暂停、陕西新入口、泰国恢复与排队问题

2026-11-23 北京时间 13:10 复核：

### 韩国年度任务已暂停

通过控制面板的调度 API 将 `kr-kdca-region-annual-daily` 和
`kr-kdca-death-annual-daily` 的 `enabled` 改为 `false`。
API 与数据库均确认 `next_run_at=null`、健康状态 `disabled`。
两个 `kdca_open_api` 月度计划仍启用。历史年度失败保留，不再继续自动产生新任务。

### 陕西更换为省卫健委官方栏目

新入口：<https://sxwjw.shaanxi.gov.cn/zfxxgk/fdzdgknr/zdgz/ggwsxx/>。
原疾控站入口超时；省卫健委栏目和文章返回 HTTP 200，继续发布省级法定传染病月报。
适配器已改用该栏目，配置其实际 `index_{page}.html` 分页，并要求标题含“陕西省”。
实测读取 10 页发现 **116 篇月报**，日期范围 **2016-12 至 2026-10**；
这是发现的文章范围，不表示期间每个月均有完整、可用的数据。

最近三个月正文及全部明细图成功下载：

| 月份 | 官方文章 | 正文可解析病种行 | 明细原图 |
| --- | --- | ---: | ---: |
| 2026-08 | [8 月月报](https://sxwjw.shaanxi.gov.cn/zfxxgk/fdzdgknr/zdgz/ggwsxx/202609/t20260908_3675423.html) | 8 | 1 |
| 2026-09 | [9 月月报](https://sxwjw.shaanxi.gov.cn/zfxxgk/fdzdgknr/zdgz/ggwsxx/202610/t20261010_3702041.html) | 10 | 1 |
| 2026-10 | [10 月月报](https://sxwjw.shaanxi.gov.cn/zfxxgk/fdzdgknr/zdgz/ggwsxx/202611/t20261104_3711803.html) | 10 | 2 |

正文行属于 `partial_narrative`，完整病种数值在图片中，仍应进入原有
`image_pending_review` / 专家审核流程。新入口验证没有把这些正文行当作完整月报，
没有导入未经审核的图片数值，也没有改写旧失败任务。

### 泰国同时存在上游暂时错误和本地调度饥饿

`th_dds_tableau` 近 24 小时（13:06 快照）为 42 个 completed、1 个 failed、1 个 running。
completed 不能直接等同于覆盖完整：12:38 完成的批次存储了 228 行，来源审计为
`partial`，54 条错误记录包含请求失败及对应缺项，两者不能简单相加为失败病种数。
11:35 完成的批次有 64 行、审计 `validated`、无失败报告。

任务 `f9f08103-f530-4c0a-b1b0-d9688fc38a69` 首次在官方 Tableau 筛选 POST 收到 503，
属于 `transient_transport`，现有策略等待 300 秒后重试。
该任务已于 **13:07:48** 在第 2 次尝试完成，整理出 **92 行**，覆盖所请求的
第 43、44、47 周部分/全部病种，审计为 `validated`、失败报告数为 0。
这不表示所有历史周均已补齐，也不等同于全部数值已公开发表。

本地调度的另一个根因已经由 API 状态和代码共同证实：
同一国家只允许一个活动采集任务，而到期任务按固定 job_id 顺序执行；
被另一来源占用而跳过的任务也被推迟完整周期。Tableau 常先入队，导致
`th_dds_weekly` 和 `th_id_th_history` 一直 `never_run` / `already_running`，
`th_doe506_monthly` 最近成功停留在 11 月 22 日 01:16，13:06 时已过期 2,150 分钟。

修复后的调度按原定到期时间排序；被不同来源阻塞的自动计划保持原到期时间，
在下一轮空闲时优先获得机会。同来源去重仍按正常周期运行，手动触发语义保留，
暂停/改期仍通过原有 CAS 持久化保护。不同来源的活动任务不再冒充本计划的任务 UUID。
现有按病种与周的补采策略保留；未增加对 Tableau 状态型 POST 的盲目重试。

### 本轮验证与加载状态

- 陕西相关现有测试：**61 passed**。
- 调度、状态 CAS、采集重试等相关测试：**67 passed**，包括新增的来源轮转与
  自动/手动、同源/异源阻塞用例。
- 推送合并前，对 development 中同批待发布的凭证、失败恢复、模型额度、韩国及映射
  改动补跑相关 Python 测试：**131 passed**；数据 Worker 的 **35 项测试**和 TypeScript
  类型检查通过。仓库边界检查通过，未将生成的数据或原图加入代码仓库。
- 陕西适配器 Ruff 通过；调度模块和对应测试的全量 Ruff 仍分别有 43、2 个既有问题，
  与 HEAD 基线比较未新增问题；`git diff --check` 通过。
- scheduler 已重启并加载公平性及年度凭证检查，API 已重启并加载失败恢复判定。
  重启后的 `/health/ready` 返回 HTTP 200 / `ok`，韩国两个年度计划仍为 disabled，
  泰国 Tableau 健康状态已恢复 healthy。
- 采集 worker 当时仍在执行两个发布任务；未强制中断。陕西适配器更新已落盘并通过
  新进程实测，常驻采集 worker 仍需在发布任务完成后安全重载，随后才能用新入口执行
  正式采集和图片审核。没有宣称陕西已完成正式补采，也未宣称所有模型进程已加载额度修复。
