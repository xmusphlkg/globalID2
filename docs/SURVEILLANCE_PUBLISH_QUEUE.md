# 独立监测 JSON 发布队列

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
