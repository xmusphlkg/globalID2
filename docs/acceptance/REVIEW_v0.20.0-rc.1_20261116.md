# v0.20.0-rc.1：多国接入与增量更新独立验收

验收日期：2026-11-16，Asia/Shanghai。审查基线：`development` 分支 `b86708d` 及当前未提交工作区。`v0.20.0-rc.1` 只是本轮候选验收编号，未修改应用版本、创建 Git tag、提交或部署。

**结论：暂不通过。** 首轮离线回归 530 通过、1 失败；工作区变更后复验为 **532 通过、1 失败**，另有两个经纯内存复现的增量更新问题。两轮测试范围重叠，不相加。未发现已证实的 P1；下列三个 P2 需要修复及针对性复验。复验期间 CO 历史导入脚本仍有变更，最新内容未完整认证；本结论不构成数据库、实时源覆盖或上线验收。

## 范围与实际检查

- AR、CL、CO、ID、KH、MW、MX、MY、NG、NL、PE、TH、ZA 新增 crawler、processor 和 staged ingestion connector，合计约 7,300 行 crawler/processor；按来源抽查国家/地区、病例/死亡/实验室检测/发生率、年/月/周/累计、缺失/零值、修订及原件链路。
- `country_library.py`、`source_scopes.py`、`source_channels.py`、`country_bootstrap.json`、`surveillance_channels.json`；10 个官方渠道配置与 TH/MY/NL 11 个通知源配置脚本。
- 查看相关导入、核验脚本及既有来源文档；运行其离线单元/合同测试。现有验收文档中的真实数据库条数、运行任务和网络结果未当作本轮新验证结果。
- 核对 CO 已改为完整历史版本门禁；新来源默认 `public_release_enabled=false`、禁映射通知并使用 source-native holding，这些边界符合当前代码合同。
- 70 个主要来源/配置文件的内容指纹保存在 `docs/acceptance/evidence/20261116_review/rc1-scope-manifest.json`。该清单用于识别验收时工作区内容，不表示逐行审计了全部 70 个文件。

## 验证证据

执行命令（仓库根目录）：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_argentina_ben.py \
  tests/unit/test_cl_eno.py \
  tests/unit/test_co_sivigila.py \
  tests/unit/test_co_update_gate.py \
  tests/unit/test_historical_country_onboarding.py \
  tests/unit/test_id_kemkes_profile.py \
  tests/unit/test_kh_camewarn.py \
  tests/unit/test_mw_phim_idsr.py \
  tests/unit/test_mx_dge_annual.py \
  tests/unit/test_my_idengue.py \
  tests/unit/test_my_nl_current_onboarding.py \
  tests/unit/test_my_who_sources.py \
  tests/unit/test_national_notification_schedules.py \
  tests/unit/test_ng_ncdc.py \
  tests/unit/test_nl_notifiable.py \
  tests/unit/test_nl_weekly.py \
  tests/unit/test_pe_cdc.py \
  tests/unit/test_priority_country_updates.py \
  tests/unit/test_priority_surveillance_onboarding.py \
  tests/unit/test_th_dds.py \
  tests/unit/test_th_doe506.py \
  tests/unit/test_th_id_th.py \
  tests/unit/test_th_tableau.py \
  tests/unit/test_za_nicd_nmc.py
```

结果：`1 failed, 530 passed in 189.24s`，0 skipped。完整日志：`docs/acceptance/evidence/20261116_review/rc1-pytest.log`。

### 工作区变更复验与结论边界

首轮后总审查检测到 13 个文件发生外部变更，因此重新检查了 CO 按相关周期查询历史的优化及其在 `series_rows` 中的传递、MW 小节标题与计划元数据变更，并完整重跑上述 24 个测试文件。复验结果为 `1 failed, 532 passed in 174.22s (0:02:54)`，0 skipped；唯一失败仍为 RC1-03 的 CO 旧合同断言。日志：`docs/acceptance/evidence/20261116_review/rc1-recheck.log`。11:26:47 的纯内存对抗复验结果亦未变化，见 `rc1-adversarial-recheck.log`。

复验开始于 11:26:17，结束内容指纹采集于 11:29:33，均为 Asia/Shanghai；79 个相关文件的前后清单为 `rc1-recheck-start-manifest.json`、`rc1-recheck-end-manifest.json`。期间仅 `scripts/imports/co_sivigila_history.py` 再次改变；测试进程加载的是较早内容，因此不能将通过项扩大解释为最新脚本已完整验收。最后总工作区快照相对初始共有 15 个文件变化，新增的两项是 `docs/data-sources/MW_PHIM_IDSR_INTEGRATION.md` 和 `docs/data-sources/README.md`，不改变以下发现的代码位置。后续应冻结候选内容再复验，当前不继续追逐变更重跑。

额外编写工作区外的纯内存对抗复现，读取既有测试 fixtures，未运行配置/导入 CLI、未访问真实数据库或网络：

```bash
PYTHONPATH=. venv/bin/python docs/acceptance/evidence/20261116_review/rc1-adversarial.py
```

输出保存在同目录 `rc1-adversarial.log`，关键结果：

```text
NL numeric -> blank: incoming identity omitted; upsert projection retains stale value 65
MY FluNet numeric -> null: 1 previously numeric identity omitted
TH Tableau run after outage: offered weeks=1..45, automatic selected= [45]
TH PDF run after outage: offered weeks=43,44,45, automatic selected= [45]
```

## 发现

### RC1-01 · P2：明确变成缺失的历史单元格无法撤销此前数值

位置：

- `/home/likangguo/globalID/globalID2/src/data/crawlers/nl_weekly.py:122`（virological）及 `:196`（respiratory）。
- `/home/likangguo/globalID/globalID2/src/data/crawlers/my_who_flunet.py:72`。
- 存储调用：`/home/likangguo/globalID/globalID2/src/services/ingestion/series_rows.py:259`。

触发条件：某一仍在本轮上游响应中的明确年份/周/病原单元格，之前发布数值，随后被上游改为 blank/null。上述 parser 将它计入缺失统计后直接跳过，不输出原 identity 对应的 `source_missing` 行。例行保存按 identity upsert，且这些来源未传入权威替换窗口，因此没有任何写入能将此前的数字改为未知。

复现：RIVM fixture 中 `Influenza A virus` 的 `202645` 为 `65`；将同一明确单元格改空，原 identity 从新行列表消失。将第二快照按实际保存方式合并到第一快照，原值仍为 `65`。FluNet 对同一已发布周/来源的 `INF_A` 从数值改 `None` 也省略一条原 identity。

影响：回归只验证“初次缺失不伪造零”，没有覆盖“旧数值被撤回后变未知”。后续查询可能持续读到已撤回数字，修订任务仍可成功结束。

建议：对响应中明确存在的缺失单元格发出同 identity、`Cases=""`、`MissingValueStatus="source_missing"` 的修订；窗口外离开滚动发布范围的旧周继续保留。补充数值→缺失的两次 ingest 回归，验证 holding/已映射路径均真正更新状态。共享存储已支持显式缺失，避免用整窗删除替代这个精确修订。

### RC1-02 · P2：TH 自动化“完整历史修订”声明与最新一周快照实现冲突

位置：

- `/home/likangguo/globalID/globalID2/src/services/ingestion/countries/th_tableau.py:39`。
- `/home/likangguo/globalID/globalID2/src/data/crawlers/th_dds.py:159`。
- `/home/likangguo/globalID/globalID2/src/services/crawl_service.py:1102`、`:1137`。
- `/home/likangguo/globalID/globalID2/scripts/configure_national_notifications.py:66`。

触发条件：30 分钟计划使用默认输入；某周采集失败或暂停，下一次成功时来源已发布新周。PDF 连接器默认 `[max(published)]`，Tableau 默认 `recent_weeks=1`；任务分发未将调度的修订窗口或上次成功水位变成 `weeks`。二者均拒绝 `fill_missing`。

复现：官方选择器同时提供已结束的 43/44/45 周，两个默认选择函数均只返回 `[45]`。缺失的 43/44 周不会进入本次请求，后续默认计划也不再选中。对更早周的上游修订有同样问题。

影响：新来源接入后的短暂故障可变成永久周缺口；持续运行也只能修订当前最新周。历史 ID_TH、DOE506 与当前 DDS 的基准/日历/指标合同独立，不能认为其他历史源能自动填补同一 DDS 序列。

合同边界：`TH_DDS_TABLEAU.md` 和 `TH_DDS_WEEKLY.md` 确实明确了默认只采最新一周，当前实现符合这项较窄合同。本项因此属于自动化覆盖声明及恢复能力的验收缺口：`docs/data-sources/NATIONAL_NOTIFICATION_AUTOMATION.md` 首段及 `configure_national_notifications.py` 的说明宣称刷新完整来源窗口/历史修订，无法由当前两个默认计划支持。不能把仅保最新周的有意设计冒称全历史修订通过。

建议：按照已存水位与明确修订窗口选择多个已结束周，并在元旦覆盖尚未完成的上一来源年；保留每个来源显式日历与请求预算。若产品明确只要求最新周快照，应在任务和覆盖说明中记录该限制，不声称日常历史修订/自动补齐。

### RC1-03 · P2：CO 新历史门禁与 onboarding 回归合同不一致，测试套件失败

位置：`/home/likangguo/globalID/globalID2/tests/unit/test_priority_surveillance_onboarding.py:48`；对应实现 `/home/likangguo/globalID/globalID2/src/services/crawl_service.py:1211`。

失败 case：

```text
test_country_task_roundtrip_uses_canonical_archive_connector[
  CO-co_ins_sivigila_historical-ColombiaINSSivigilaUpdater-
  Colombia INS SIVIGILA Historical Public Health Surveillance]
```

该测试期待所有国家都转到 `countries.<country>` 且 `process=True`、传递 `start_year`，但 CO 已有意改为 `co_update_gate`、`process=False`，并在 force/不完整历史/版本变化时要求独立完整回填。这一门禁防止单页假成功，已有专门 `test_co_update_gate.py` 覆盖，不能为了旧断言恢复不安全的单页路径。

建议：对 CO 单独断言新门禁合同，同时保留其 force、未完成历史、版本变化拒绝成功的回归；其他国家继续核验原 staged connector 和入库选项。本轮未改测试或业务代码，保留真实失败证据。

## 本轮未覆盖

- 未复查真实数据库条数、已运行任务收据、在线目录完整性、实际计划启用/next_run_at；未写数据库、运行真实爬取、重启服务、提交、推送或部署。
- 未穷举真实 PDF 的全部历史版式，未对每份历史原件逐格人工对账。通过的 fixtures/合同测试只能支持其覆盖的版式与边界。
- 本报告未独立验收 ontology 大规模变更的全部疾病映射、全站前端与生产公开发布；这些需由其他批次覆盖。
- 修复后应在同一验收编号下记录复验，并重新检查工作区指纹。当前通过项不能抵消失败回归、上述修订缺口及候选内容未冻结的验收边界。
