# rc.1 / rc.4 / rc.5 修复交叉复核

日期：2026-11-16。由负责 rc.2 的子代理独立检查主 agent 的 rc.4 / rc.5 修复。本轮只读审查和本地无网络反例，没有修改被审业务代码，没有重复运行完整测试，没有生产写入、发送邮件或部署。

## rc.5：三项修复通过交叉审查

- `stale_source` 已补入 Pydantic `AutomationJobOut.health_status`、OpenAPI 和生成 TypeScript。新增测试将正式健康投影同时送入 `AutomationConfigOut` 与 `_automation_job_out`，能捕获此前“schema生成一致但运行时枚举不一致”的问题。
- 自动触发在最终刷新读到 `next_run_at=NULL` 时返回 not_due，不再错误入队；显式手动触发仍执行一次。手动触发若最初观察到 NULL，最终结果保持 NULL，防止使用过时的 enabled 快照恢复暂停作业。并发恢复产生新的非空日程时，SQL CAS 因当前值与 expected NULL 不相等而保留恢复后的日程；并发暂停也保持 NULL。正常首次初始化经过 `start` / `_sync_state_schedule` 计算未来日程后只插入缺失状态，正常创建及启用 API 会显式 reschedule，本次拦截没有破坏这些路径。
- 通知混合批次在同一 claim 事务中完成 opted-out→skipped 与 retained→sending，第一次提交时不存在仍 pending 的 retained 事件。全量 opt-out 分支单独提交 skipped 并直接返回，发送器不被调用；空批次无需额外领取提交。原先的提前释放领取锁窗口已移除。

阅读并核对已有日志：`rc45-targeted.log` 为 36 passed，`rc5-python.log` 为 179 passed；未将它们重复计入本子代理的新测试数量。通知测试使用发送器替身；本次不声称做了真实 PostgreSQL 并发发信。

## rc.4：宿主分组已覆盖原问题，额外发现规范化边界

新增回归验证了 `Wild  birds` / `wild birds` 合并，并让禽类、另一动物物种、人类、未知宿主分开；首页 key 也已包含 host_type 和 host_species，避免不同宿主在相同日期/地区被折叠。已有 `rc4-python.log` 为 165 passed。

交叉检查同时发现后端 `latest_per_place` 使用 AI `normalise`，该函数除了 NFKC、大小写和空白，还删除字母间连字符、零宽字符并使用 casefold；前端 `model.ts` 和首页则使用 NFKC、空白折叠及 lower。直接调用正式 Python 和 Node 代码，以同一 outbreak/place/date 的 `Black-headed gulls` 与 `Blackheaded gulls` 为输入，后端输出 1 个 place，前端 `latestMatchingPlaces` 输出 2 个。实际首页读取后端压缩后的 points，因此可出现首页 1、筛选历史视图 2。

该 P2 边界已修复并完成复核：后端改为与前端及首页一致的 NFKC、空白折叠和 lower；回归数据同时保留 `Black-headed gulls` / `Blackheaded gulls` 两组，原有空白/大小写等价仍合并。`test_species_normalization_keeps_latest_place_and_homepage_count_consistent` 验证六个独立 place 及首页六个地区，已有 `rc46-targeted.log` 为 38 passed。本子代理独立重跑该单个回归为 **1 passed, 14 deselected**，没有重复整套测试。原始对照证据：`/tmp/globalid-fixes-20261116/rc45-cross-species.log`。此项已解决。

## 泰国 DDS / Tableau：有界自动刷新交叉审查

已检查 `th_refresh.py`、DDS 和 Tableau 多年 collect/organize、`crawl_service.py` 参数透传，以及新增有界刷新测试。自动请求从实际 source/basis 隔离的已存事实读取覆盖；显式 year/weeks/recent_weeks 请求绕过自动扩张；使用官方逐年日历，跨年总预算按类别共享，未把每年各四周误作总四周。稀疏 Tableau 请求计划也由重放端按实际 label/week 检验，不虚报未请求组合缺失。

本轮发现并立即反馈国家采集子代理两项边界，现均已修复并复核：

- `RefreshPlan.select` 固定选择 latest + 最早三个 missing，仅在缺失不足三个时刷新已存修订。直接使用官方 2026 日历、除第 5/6/7 周外全部已覆盖且三天前更新的状态，连续三轮均选择 `[(2026,5),(2026,6),(2026,7),(2026,46)]`。若三个旧周持续失败，近期非最新周修订与排在后面的缺失周会无限饥饿；已有回归只覆盖单个失败周及没有缺失的修订轮转，未覆盖此反例。
- 两个 connector 的多年 organize 在任一年无有效行时直接抛错，runner 只有 organize 成功后才进入 store。例如上一年三个失效补采、当前年 latest 有效，当前年的有效结果也不能保存。需保留失败年份的 partial 审计并允许合并有效年份；所有年份均无有效事实时仍应明确失败，不能静默跳过失败。

最终实现将每类预算下限设为四周，分别保留 latest、一个到期 revision、最早 missing 和轮转 missing；没有对应任务时将名额分配给其他待办。missing 轮转加入日期偏移，避免每日固定时刻对三项失败取模始终落在同一项。新增连续 16 轮半小时 / 每日固定时刻反例，确认永久失败仍不阻止其他缺周尝试及近期修订，低于四周的预算明确拒绝。

自动模式下单年没有有效观察返回 `rows=[]`、`source_latest_date=None` 和完整 partial 失败审计，merge 保留年份与错误并汇总有效年份；所有年份均空时仍明确抛错，手动显式请求仍 fail-closed。日历原件、请求清单和会话状态校验未放宽。

另按主 agent 要求追查 revision 轮转水位是否依赖测试手动更新时间：正式两来源都输出 `AuthoritativeRevision=true`，观察构建将其写入 `metadata.authoritative_revision`；TH 正常保存没有 `authoritative_window_start`，不会走该模式的 unchanged 剔除。普通 upsert 的 WHERE 明确允许 authoritative_revision，SET 每次更新 `updated_at`，不要求 value 有差异；`CrawlService._import_rows_with_series` 即使 legacy import 返回零也继续保存 source-series。故同值成功刷新会推进真实水位；失败和被质量保护拒绝的事务则不会伪造成功水位。本次为源码路径检查，未声称执行生产数据库写入。

本子代理只独立重跑新增边界子集：**10 passed, 9 deselected, 6.16s**，不重复完整泰国测试。另阅读负责修复代理的 `rc1-reviewer-regressions.log`（61 passed）和最终 `rc1-final.log`（149 passed；测试范围重叠，不相加）。跨审提出的两项均已解决，本轮范围内没有剩余阻断。

## 发布源码清单：读取引发 atime 变化的偶发误报

完整 unit 偶发的 theme→full 计划降级已独立复现：`publication_planning.inspect_source` 原先比较整个 `os.stat_result`，哈希读取自身产生的 atime 变化也会触发 `source_inventory_unstable`。在正式文件哈希调用期间，仅模拟 atime 改变，原实现稳定出现 `theme != full` 回归失败；文件内容没有变化。

现将前后比较限定为 dev、inode、size、mtime_ns、ctime_ns，保留原有 SHA-256 内容读取。新增六项回归从实际临时 Git 仓库生成发布计划：仅 atime 变化仍保持 theme、内容指纹不变；另外五种字段变化均保留 unstable 警告并降为 full，其中 mtime / ctime 包含纳秒变化。没有降低真实替换、写入或内容漂移保护。

`test_publication_planning.py`、`test_release_artifact_checkpoint.py`、`test_build_fingerprints.py`、`test_country_template_release.py` 合计 **69 passed, 20.36s**。`git diff --check` 通过。本项只修改规划器及其回归文件，没有提交、部署或写生产数据。
