# v0.20.0-rc.1 修复复验：多国接入与增量更新

> 最终复验更新（2026-11-16 12:21，Asia/Shanghai）：**rc.1 与 rc.7 合并去重 33 文件，694 passed / 0 failed / 0 skipped，222.86 秒；可进入国家批次提交。** 四个旧目录计数失败已由明确的原十渠道及三十一个完整身份守卫解决。运行期间生产代码、CO 脚本与配置指纹不变；唯一测试追加已单独核对并复验 46 passed。下文此前“不通过”及 555/4 结果保留为历史记录，不代表当前结论。

日期：2026-11-16，Asia/Shanghai。**本批三个修复已通过针对性复验；最新共享工作区的 26 文件整批验收暂不通过。** 最后一轮为 **555 passed、4 failed、0 skipped，193.06s**：外部 WHO/SPF 接入把渠道/任务由 10 个扩为 28 个，四个既有固定数量断言失败；复验期间 `crawl_service.py` 也再次改变，不能签为冻结版本通过。 原 24 个测试文件加两个新增回归文件的整批测试为 **548 passed，0 failed，0 skipped，168.88s**；交叉审查补修永久缺周饥饿及空年度失败隔离后，最终针对性测试为 **149 passed，0 failed，0 skipped，16.93s**。测试范围重叠，不相加。验收编号沿用 `v0.20.0-rc.1`，应用版本、提交、tag 和部署由主执行者统一处理，本子任务没有执行这些操作。

## RC1-01：明确缺失修订实际覆盖此前数字

`nl_weekly.py` 的 virological 和 respiratory parser、`my_who_flunet.py` 的字典检测字段现在对响应中明确存在的 blank/null 单元格输出原 identity，值为 `Cases=""`、状态为 `MissingValueStatus="source_missing"`。数值零仍为 reported；FluNet 抑制值仍为 source_suppressed。明确单元格的未知状态可以修订此前数字；不在新响应中的历史周期继续保留，不执行全窗删除。

新增 `tests/unit/test_nl_my_missing_revisions.py`，对 NL 两种周源及 MY FluNet 分别执行 holding 和已映射两条路径的两次实际 `SeriesObservationStore.save_rows()`，共六个回归。SQLite 内存数据库执行真实观察表/修订表 INSERT、ON CONFLICT、读取和事务提交，验证同 identity 数值变 NULL、缺失状态保留、零值不丢失、范围外旧周期/来源维度仍存在，以及修订账本保留前后状态。测试仅屏蔽 PostgreSQL advisory lock，并将 JSON 布尔文本序列化为 PostgreSQL `->>` 的等价文本；生产存储代码未修改。已映射路径使用受控的测试 Registry 定义，不宣称这些来源已经完成生产疾病映射。

既有 parser 回归同步更新，避免继续要求明确缺失单元格消失。

## RC1-02：DDS 有界修订与跨年故障追赶

新增共享规划器 `src/services/ingestion/countries/th_refresh.py`。`CrawlService` 的两个 DDS 任务入口在自动模式只读实际已存观察的 source code、date/place basis、日期与 updated_at，按来源及 national geography 隔离，不读整份 raw JSON，也不使用全局 MAX 日期推进完成状态。

自动计划的规则：

- 每病种、每任务最多四个 source weeks，预算跨年度共用。所有实时源类别仍参与计划，没有疾病 offset。
- 每轮保留最新闭合周期，并在确有到期修订时预留一个修订名额；剩余名额用于缺口追赶。一个名额持续重试最早未入库周期，其余按 UTC 日期 ordinal + hour + minute//30 轮转仍明确提供的缺周。日期偏移避免每天固定时刻重试与 modulo-48 槽共振，三个永久失败的旧周不能锁死后续补采或修订。预算下限四周，保证这三类工作同时有空间。
- 修订名额优先选 `revision_window_months` 内按 updated_at 最久未复验的旧周，24 小时内已复验的旧周暂不重取；缺口用不满预算时增加修订名额。默认三个月，继续使用现有任务/调度参数；最新周按正常计划频率复查。
- 追赶起点取首次已存周期与修订窗口起点的较早者，因此长期故障即使超过修订窗口也可逐轮恢复。无既有覆盖的新安装从修订窗口开始，完整历史初始化仍是独立工作。
- 跨年采集各年度官方日历，并保留短首周/末周；PDF 只使用捕获的正式 `N-N` 选择器，不将累计 `1-N` 当作周病例。Tableau 保存每病种稀疏请求计划，避免类别缺口差异扩大成所有类别的笛卡尔积请求。
- 原件按年度分别重放，合并成一次 runner store transaction。自动模式某年全部报告失败时返回空行和 partial 审计，保留 failed_reports/year，不阻断另一年有效结果入库；所有年份都无有效行时任务失败。手动明确请求维持原 fail-closed 行为。重复年度计划拒绝进入存储。
- 手动显式 `year`、`weeks` 或 Tableau `recent_weeks` 优先，绕过自动覆盖查询；手动年未指定周时保持原最新闭合周语义。

没有修改 `automation_service`、`schedule_state` 或 `mapping_notification`，也没有新增持久水位字段。覆盖只由实际已提交的观察推进。

新增 `tests/unit/test_th_bounded_refresh.py` 验证：跨年末周和新年首周、同一 MAX 日期下不同病种缺口、窗口外长期故障逐轮推进及失败周期重试、新类别预算、修订轮转、防止未来周入库、PDF/Tableau 跨年度实际请求数、Tableau 稀疏原件重放、年度结果合并、真实 SQLite 覆盖查询的来源/basis 隔离，以及手动选择优先。交叉审查后额外覆盖连续 16 轮的永久缺周故障（半小时计划与每日固定时刻）、到期修订预算保留、预算 2/3 拒绝、两来源某年真实原件全部缺失时保留空年审计、有效年度继续合并及全部失败拒绝。现有两来源原件/日历真实性检查继续通过。

## RC1-03：CO onboarding 测试跟随完整历史门禁合同

仅更新 `tests/unit/test_priority_surveillance_onboarding.py` 的 CO 分支：断言使用 `co_update_gate`、`process=False` 和 force 选项；其他国家继续断言各自 staged connector、start_year 和 process=True。生产门禁没有回退到单页导入。专门的 `test_co_update_gate.py` 与原国家合同测试均通过。

## 验证证据与文件边界

整批运行了原 [rc1 审查报告](REVIEW_v0.20.0-rc.1_20261116.md) 列出的 24 个 pytest 文件，并添加 `tests/unit/test_th_bounded_refresh.py`、`tests/unit/test_nl_my_missing_revisions.py`。日志：[rc1-full.log](evidence/20261116_fixes/rc1-full.log)。该广回归在交叉审查修复之前完成；之后增加永久失败轮转、修订名额保留、空年度部分失败隔离及年度错误标识。最终代码由以下针对性命令复验，不把先前通过计数冒称最后修改后的全套重跑：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_th_bounded_refresh.py \
  tests/unit/test_th_dds.py \
  tests/unit/test_th_tableau.py \
  tests/unit/test_nl_my_missing_revisions.py \
  tests/unit/test_nl_weekly.py \
  tests/unit/test_my_who_sources.py \
  tests/unit/test_priority_surveillance_onboarding.py \
  tests/unit/test_co_update_gate.py \
  tests/unit/test_national_notification_schedules.py
```

末轮日志：[rc1-final.log](evidence/20261116_fixes/rc1-final.log)。六条 warning 来自 SQLite 不支持 PostgreSQL DISTINCT ON 的兼容性告警；不影响本次 value/identity/ledger 断言。未将内存 SQLite 回归扩大表述为真实 PostgreSQL 或在线来源验收。原始日志亦保留在 `/tmp/globalid-fixes-20261116/rc1-*`。

16 个相关文件的末轮 SHA-256 快照：[rc1-final-manifest.json](evidence/20261116_fixes/rc1-final-manifest.json)，采集于 11:52:47。共享工作区仍可能由其他工作更新；主执行者应按实际提交内容复核快照。本子任务增量编辑如下：

- `src/data/crawlers/nl_weekly.py`
- `src/data/crawlers/my_who_flunet.py`
- `src/data/processors/th_dds.py`
- `src/services/crawl_service.py`（仅两个 TH DDS handler）
- `src/services/ingestion/countries/th_dds.py`
- `src/services/ingestion/countries/th_tableau.py`
- `src/services/ingestion/countries/th_refresh.py`（新增）
- `scripts/configure_national_notifications.py`（说明/notes）
- `docs/data-sources/TH_DDS_TABLEAU.md`
- `docs/data-sources/TH_DDS_WEEKLY.md`
- `docs/data-sources/NATIONAL_NOTIFICATION_AUTOMATION.md`
- `tests/unit/test_nl_weekly.py`
- `tests/unit/test_my_who_sources.py`
- `tests/unit/test_priority_surveillance_onboarding.py`
- `tests/unit/test_nl_my_missing_revisions.py`（新增）
- `tests/unit/test_th_bounded_refresh.py`（新增）

本报告及上述持久证据另计。保留了工作区既有变更，没有改共享 `series_rows`/store、CN、地图或 PE fixtures。

## 最后 26 文件复验：外部接入扩容阻断冻结签收

主执行者要求再次运行全部 26 文件，运行结果为 `4 failed, 555 passed, 6 warnings in 193.06s (0:03:13)`，0 skipped。四个失败全部位于 `tests/unit/test_priority_country_updates.py`：`test_official_channels_match_source_registration`、`test_jobs_keep_native_cadence_and_raw_evidence`、`test_preview_does_not_change_jobs`、`test_apply_preserves_pauses_and_is_idempotent`。它们仍要求原 10 个国家/任务和 10 次重排，而外部并行 WHO/SPF 接入已使渠道与任务变为 28。失败属于新增范围与旧合同冲突，未显示 rc1 的缺失修订或 TH 追赶回归失败；本子任务没有自行改新接入或将断言放宽。

日志：[rc1-frozen-full.log](evidence/20261116_fixes/rc1-frozen-full.log)。20 个相关文件的 [开始指纹](evidence/20261116_fixes/rc1-frozen-start-manifest.json) 采于 11:53:35，[结束指纹](evidence/20261116_fixes/rc1-frozen-end-manifest.json) 采于 11:57:35，HEAD 均为 `b86708d`。期间列表中仅 `src/services/crawl_service.py` 内容改变；其他 19 个指纹不变。外部渠道配置不在这 20 项初始指纹中，因此不能据此声称整个共享工作区已冻结。保留 [当前 CrawlService 对 HEAD 的 diff](evidence/20261116_fixes/rc1-frozen-crawl.diff)，用于核对当前实现；它包含此前未提交内容，不冒称为开始/结束间的精确 diff。

等待主执行者隔离或纳入新增接入并完成对应合同验收，本子任务不继续追逐变更重跑，也未提交或部署。

## AU 旧断言的独立交叉复核

主执行者修正 `test_crawl_service_series_dual_write.py` 的 AU kwargs 断言后，本子任务独立核对实现：只有 CO updater 显式启用 `series_history_relevant_periods_only` 和 `series_reuse_upsert_rows_for_revisions`；AU/AU-NSW 实际工厂实例的两属性均采用 False。共享调用显式传入 False 与 store 默认策略等价，AU 继续使用完整质量历史及原 revision 读取路径，`series_country_code="AU"`、`SRC_AU_NINDSS`、州级 geography 的原断言全部保留。这次断言更新没有掩盖 AU 行为改变。

独立运行整个 `tests/unit/test_crawl_service_series_dual_write.py`：**16 passed，0 failed，3.39s**；日志：[rc1-au-contract.log](evidence/20261116_fixes/rc1-au-contract.log)，原件保留在 `/tmp/globalid-fixes-20261116/rc1-au-contract.log`。该文件由主执行者编辑，本子任务只读复核，不计入上面的 16 个增量编辑文件。

## 剩余验收边界

没有抓取线上来源、写真实数据库、发送邮件或部署。来源不再提供的旧周不能重建；新年度官方日历尚未公布时仍 fail closed。自动初始化只覆盖修订窗口，窗口之前的原始历史需要独立回填。最近修订窗口之外、已完整入库的旧数值不会每轮重新下载，需显式历史复验才能发现更早的上游修订。覆盖投影随既有存储周期增加，当前没有对真实生产规模做性能测量；每轮实际报告请求仍按类别四周上限约束。


## 最终国家合并复验（rc.1 + rc.7）

原 26 文件与 rc.7 九文件合并去重为 **33 文件**，完整命令见 [country-final-command.json](evidence/20261116_fixes/country-final-command.json)。本轮结果：**694 passed，0 failed，0 skipped，8 warnings，222.86s**。八条 warning 仍是 SQLite DISTINCT ON 方言兼容告警；没有 PostgreSQL 实库写入。日志：[country-final-full.log](evidence/20261116_fixes/country-final-full.log)。

开始与结束 HEAD 均为 `9d4d52bacb56081b6e061baad40d8884f8742a56`。436 个文件的 SHA-256 覆盖国家 crawler/processor/connector、CO 历史导入与验证脚本、国家导入脚本、来源配置、共享入库、测试及原国家批 fixtures。**435 个文件在测试期间内容不变**，所有生产代码、CO 脚本及配置均包含其中。唯一改变是外部在 `test_remaining_surveillance_onboarding.py` 末尾追加 retained-history 验证测试；将该函数移除并恢复原文件尾换行后，SHA-256 精确等于启动指纹，证明原测试未被放宽。最新该文件独立复验 **46 passed，13.92s**；这是重叠覆盖，不与 694 相加。

证据：[开始指纹](evidence/20261116_fixes/country-final-start-manifest.json)、[结束指纹](evidence/20261116_fixes/country-final-end-manifest.json)、[结果摘要](evidence/20261116_fixes/country-final-result.json)、[测试追加证明](evidence/20261116_fixes/country-final-test-drift.json)、[精确追加补丁](evidence/20261116_fixes/country-final-test-drift.patch)、[追加后复验](evidence/20261116_fixes/country-final-drift-recheck.log)。

原 436 清单未包含 rc.7 的 WHO/SPF/CR/WPRO 新 fixture 目录；另存十九个 fixture 的当前 SHA-256 及 mtime/ctime，均早于本轮测试启动，见 [补充 fixture 指纹](evidence/20261116_fixes/country-final-rc7-fixtures.json)。这是补充当前指纹和文件时间证据，不冒称预先采集的双端哈希。测试结束后的复核只有 `docs/data-sources/NL_RIVM_NOTIFIABLE_ANNUAL.md` 文档继续变化，见 `country-final-post-recheck-manifest.json`，没有代码变化；本报告随后更新也属于验收文档变更。

新增三来源的五项修复及覆盖边界见 [rc.7 报告](REVIEW_v0.20.0-rc.7_20261116.md)。本次完成已知国家范围的合并验收，不继续扩大审查或反复重跑；停止代码变更，由主 agent 按实际 index 进行国家批次提交。未部署。
