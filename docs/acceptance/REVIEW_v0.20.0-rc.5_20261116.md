# v0.20.0-rc.5：自动化、共享入库、模型路由与后台独立验收

日期：2026-11-16。基线：`b86708d`，范围为当前工作区。此编号仅用于分批验收，不表示正式版本或 Git tag。本轮不改业务代码，未执行部署、生产任务、数据库写入或发送邮件。

结论：**不通过，存在 1 个 P1 和 2 个 P2**。基础回归、类型检查与 OpenAPI 同步通过，但运行时状态值不满足 API schema，且两个并发边界尚有缺口。

## 范围与证据

独立审查了 `schedule_state.py`、自动化调度刷新与结果 CAS、前台任务租约、`series_observation_store.py` 的 N.A. 及分批查询、迁移 0033、模型中心原生文件能力与专家去重路由、疾病映射通知策略、来源 API/schema/生成类型，以及本次后台来源与作业表单变更。

主 agent 已执行的共享验证结果（本子代理阅读日志核对，未重复执行）：

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| Python 相关组合回归，13 个文件 | 172 passed，0 failed，6 条现有 SQLAlchemy 方言警告 | [rc5-python.log](evidence/20261116_review/rc5-python.log) |
| 全 dashboard Vitest | 40 files / 170 tests passed | [rc5-dashboard-tests.log](evidence/20261116_review/rc5-dashboard-tests.log) |
| dashboard `npm run lint`（`tsc --noEmit`） | 通过 | [rc5-dashboard-types.log](evidence/20261116_review/rc5-dashboard-types.log) |
| OpenAPI JSON / TypeScript 同步检查 | 两者 status=ok，ready=true | [rc5-openapi.log](evidence/20261116_review/rc5-openapi.log) |

Python 组合命令：

```bash
venv/bin/python -m pytest -q tests/unit/test_schedule_state_cas.py tests/unit/test_automation_state_refresh.py tests/unit/test_national_notification_schedules.py tests/unit/test_ingestion_mapping_notifications.py tests/unit/test_foreground_ingestion_lease.py tests/unit/test_series_observation_store.py tests/unit/test_peer_review_routing.py tests/unit/test_model_center_catalogue_policy.py tests/unit/test_disease_mapping_registry_v3.py tests/unit/test_disease_ontology_export.py tests/unit/test_ontology_sync_validation.py tests/unit/test_dashboard_release_schema.py tests/unit/test_surveillance_data_export.py
```

本批审查期间主 agent 发现外部进程仍在修改部分共享入库文件，随后以同一命令复跑，结果仍为 **172 passed，6 warnings（23.60s）**，见 [rc5-recheck.log](evidence/20261116_review/rc5-recheck.log)；本表保留首次运行结果。下述三个问题所在的 automation/mapping_notification 文件未发现该次漂移。

补充了无数据库、无网络、无实际邮件发送的运行时反例：[rc5-adversarial.py](evidence/20261116_review/rc5-adversarial.py)，输出见 [rc5-adversarial.log](evidence/20261116_review/rc5-adversarial.log)。从仓库根目录执行：

```bash
venv/bin/python docs/acceptance/evidence/20261116_review/rc5-adversarial.py
```

## 发现

### P1：新增 `stale_source` 状态不在 API 枚举中，可能使自动化接口整体失败

位置：[automation_service.py:138](/home/likangguo/globalID/globalID2/src/services/automation_service.py:138)、[sources.py:141](/home/likangguo/globalID/globalID2/dashboard/api/schemas/sources.py:141)、[sources.py:1379](/home/likangguo/globalID/globalID2/dashboard/api/routers/sources.py:1379)。

省级月报源在最近任务已完成、未处于 needs_review、明确病例月份落后最近闭合月份超过 3 个月时，健康函数返回 `health_status='stale_source'`。但 `AutomationJobOut.health_status` 的 Literal、当前 OpenAPI 和生成 TypeScript 仅接受 healthy/needs_review/partial/active/recovering/failed/stale/never_run/disabled/unknown。

反例使用 CN-BJ 作业、2026-11-16 的成功任务与 `latest_report_month='2026-05'`。正式健康函数生成 stale_source，按正式 Pydantic 字段校验立即抛 `literal_error`。`_automation_job_out` 未转换或隔离此值，因此一条这样的作业便可使整份自动化作业列表/配置响应构造失败。OpenAPI 同步检查只能证明 schema 与生成类型一致，不能证明实际业务返回值符合 schema。

应统一业务返回值、Pydantic 枚举和消费端显示，并补健康函数到 API response 的契约回归。

### P2：最终状态刷新读到暂停的 NULL，仍可能自动入队一次

位置：[automation_service.py:765](/home/likangguo/globalID/globalID2/src/services/automation_service.py:765)、[automation_service.py:775](/home/likangguo/globalID/globalID2/src/services/automation_service.py:775)、[automation_service.py:782](/home/likangguo/globalID/globalID2/src/services/automation_service.py:782)。

触发顺序：调度进程的 `load_jobs` 先得到 enabled=True 快照；另一个 API 进程暂停作业并持久化 `next_run_at=NULL`；调度进程最终 `_refresh_persisted_states` 已读到这个 NULL。由于 enabled 判断仍使用旧作业快照，且 not_due 条件仅处理非空未来时间，NULL 没有阻止自动入队。

离线 mock 精确放置上述进程切换，正式 `trigger_job(..., manual=False)` 仍返回 queued，`enqueue_calls=1`。后面的结果 CAS 可保护暂停后的日程不被覆盖，却不能撤销这次误触发。现有测试覆盖了 trigger 之前暂停和 enqueue 已开始后的编辑，漏掉作业读取与最终状态刷新之间的窗口。

应把最终自动触发资格与持久化暂停状态一起判定；至少不能将已刷新得到的 NULL 当成到期。手动执行暂停作业的既有语义应单独保留。

### P2：部分通知 opt-out 的中途提交释放了其余 pending 事件的领取锁

位置：[mapping_notification_service.py:348](/home/likangguo/globalID/globalID2/src/services/mapping_notification_service.py:348)、[mapping_notification_service.py:368](/home/likangguo/globalID/globalID2/src/services/mapping_notification_service.py:368)。

领取批次同时包含已关闭通知类别与正常类别时，新代码把前者设为 skipped 后立即 `commit`；正常事件此时仍是 pending。PostgreSQL 在该提交时释放此前 `FOR UPDATE SKIP LOCKED` 的锁，另一个消费者可领取这些正常事件。原消费者随后无条件把内存里的同一事件改为 sending 并发送，形成重复领取/发送窗口。

已与 HEAD 对比：旧领取路径在第一次 claim 提交前便把整个批次设为 sending，没有“保留事件仍 pending 时提交”的分支，因此这是本次 opt-out 新路径带来的问题。离线复现捕获的正式代码提交顺序为：

```text
commit 1: skipped=skipped, retained=pending
commit 2: skipped=skipped, retained=sending
commit 3: skipped=skipped, retained=sent
```

复现只证明正式实现的提交边界，发送器完全 mock，未做真实 PostgreSQL 并发发信。并发后果来自 PostgreSQL 提交释放行锁及另一消费者的 pending 选择条件。应在同一领取事务中完成 skipped 过滤和 retained→sending，再统一提交。

## 已确认行为与限制

CAS 使用 SQL `IS NOT DISTINCT FROM` 比较 next_run_at，能够保留并发编辑后的未来日程和 NULL，并记录实际 dispatch 结果；初始化使用 only_if_missing，不应覆盖已有状态。前台租约将所有权校验与最终任务状态写入放入同一锁定事务；失去所有权会中断执行，不能以普通失败覆写其他 owner 的任务。

N.A. 新状态保留为带 `missing_value_status='not_applicable'` 的 NULL，区别于零和 source_missing；模型定义及迁移 0033 的允许集合一致。升级为扩展旧约束并使用 NOT VALID，降级在仍有 N.A. NULL 时应失败而不删除事实。未执行 PostgreSQL 迁移，未验证生产表约束安装状态或锁耗时；必须先完成兼容迁移才能让旧约束数据库接收该类事实。

分批查询覆盖 registry 代码检查、历史读取和权威窗口范围，未发现新的无界代码 IN 参数回归。原生文件能力由正式 transport 能力判定提供，专家路由要求不同模型身份并屏蔽已投票模型；未使用真实模型验证可用容量。

后台表单和来源链接已通过现有组件测试与 TypeScript 检查，但没有浏览器交互或真实后端数据库验收。本批未授权部署。
