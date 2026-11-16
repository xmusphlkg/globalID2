# v0.20.0-rc.7：新增来源独立验收与修复

日期：2026-11-16（Asia/Shanghai）。本报告为 country-ingestion subagent 的新增批次，不改写 rc.1–rc.6 首次审查的历史结论。下述五项是新增来源的独立问题，与首次审查的十二项分开计数。主 agent 负责最终版本提交及发布决策；本 subagent 没有提交、推送或部署。

结论：本批 **离线合同、来源身份、解析和修订回归通过**。验收覆盖原十个固定渠道、十五个 WHO GHO 国家渠道、三个 SPF ODISSE 属地渠道及三个明确新增渠道，总计三十一个。该结论不等于三十一个渠道均已完成在线全量采集或公开发布。

## 新增问题与完成修复

| 编号 | 级别 | 具体触发与影响 | 修复与独立回归 |
| --- | --- | --- | --- |
| RC7-01 | P2 | 一月发布的 WPRO 报告写明上一年十二月周，原解析要求观测年等于发布年；跨年周还可能把起始日期放到错误年份并拒绝，合法周缺失 | 发布年改为上界；优先读取源明确的两端年月日，只有两端月份明确时允许跨年；仍要求恰好七天，禁止用周号补造日历。覆盖十二月周、一月周、双年明确、跨月及错误区间 |
| RC7-02 | P2 | WPRO 周叙述中的旧年累计比较数或一月含混的 `for this year` 可被赋到本次观测年 | 检查累计子句中的明确年和 `as of` 年；全部必须与源周结束年一致，否则保留合法周数并跳过累计、记录 mismatch。保留现有“发布日累计快照、截止日未验证”合同，不把发布日说成实际累计截止日 |
| RC7-03 | P2 | CR 年度 `VIH*` 初报变为 `VIH` 定稿时，星号进入 source code 且 provisional 状态进入 dimensions，导致新身份而非同年修订 | 星号仅保留在原始标签和 provisional 属性；source code、身份维度使用去星号的源标签。用真实 PDF 文本构造 `875→876`、`VIH*→VIH`，holding/mapped 各执行两次真实 `SeriesObservationStore.save_rows()`，断言只有一个身份且修订账本包含前后值 |
| RC7-04 | P2 | CR 年度报告可将尚未结束的年当作完整年度；组织阶段若依赖当前时钟，旧原档未来重放会改变准入结果；UTC 新年还早于 Costa Rica 当地新年 | 组织阶段固定使用捕获计划的带时区时间，并转换 `America/Costa_Rica`；年度必须小于捕获当地年，未来公报拒绝。测试 UTC 00:00 仍属当地旧年、UTC 06:00 当地新年、同刻负六时区表示，以及显式 as_of 和未来公报 |
| RC7-05 | P2 | WPRO 自建 PDFium 锁，CR/其他文档解析使用公共锁；跨来源线程不受同一把锁保护，存在 native 库并发风险 | WPRO 复用 `situation_notices.documents._PDFIUM_LOCK`；检查共享锁对象同一性，并保留真实 WPRO PDF 的双线程抽取回归。本轮未声称已复现 native 崩溃 |

对应实现：`src/data/crawlers/wpro_dengue.py`、`src/data/crawlers/cr_notifications.py`、`src/services/ingestion/countries/cr_notifications.py`。新增独立测试：`tests/unit/test_wpro_dengue_acceptance.py`、`tests/unit/test_cr_notifications_acceptance.py`。

CR/WPRO 文件在并行工作中持续更新。本批增量修改前后核对内容 SHA-256，补丁保存到证据目录；保留并行加入的 PDFium 加速、真实 PDF 测试、离线工件完整性和 WPRO 发布日累计快照语义，没有用早期文件快照覆盖新代码。

## 三十一个来源的固定合同

`test_priority_country_updates.py` 固定原十个国家的 country/scope/source-id 与计划任务身份，不能靠“至少十个”放宽来吞掉遗漏。`test_remaining_surveillance_onboarding.py` 再固定十五个 WHO 国家集合、三个 SPF 属地集合和以下三个新增身份，验证目录及计划任务的完整集合恰好三十一个：

| Country | Scope | Source ID |
| --- | --- | --- |
| CR | cr_moh_notifications | SRC_CR_MOH_NOTIFICATIONS |
| LA | who_wpro_dengue | SRC_LA_WHO_WPRO_DENGUE |
| VN | who_wpro_dengue | SRC_VN_WHO_WPRO_DENGUE |

期望值使用独立字面量，不从被测 `EXTRA_SOURCE_CONTRACTS` 自动推导。新增三渠道验证 dispatch、updater 国家/来源、connector options、历史起始年、ontology、bootstrap、显示标签和 scheduler 目录一致。CR/LA/VN 的默认 `all` 仍解析到年度 GHO 基础渠道，显式 scope 才选择新来源。公开发布及映射通知均保持关闭。

WHO GHO 验收包括真实国家计数、病例/死亡分离、零/缺失/抑制、错误国家、估计值、分层、非法数值、未闭合年份、完整唯一分页链及捕获日期重放。SPF 验收包括属地/年龄/血清群边界、结核 2019 地域定义变化、计数与率分离、分页完整性、元数据前后校验、重复冲突及工件篡改。复用 MY WHO 公共分页模块，相关 MY 来源回归同时执行。

WPRO 真实 PDF 核对 LA 周病例 232、周死亡 1、累计快照 6,133；VN 周病例 5,390、累计快照 166,507、累计死亡 21。CR 真实年度 PDF 返回 64 条明确国家表格行，保留零值和 `VIH*` 初报标记；真实公报的九健康区加总必须与国家总数一致，保留 2,425 / 113 / 71 三条不同类别累计快照。

## 验证与证据

首轮新来源四文件测试：48 passed。修复共享锁并强化目录后，九文件完整集：205 passed。最终带前后指纹复验：**205 passed / 0 failed / 2 warnings，25.01 秒**，见 `rc7-frozen-final.log`。04:14:22–04:15:52 UTC 的三十一个关键文件内容 SHA-256 **零变化**，前后 HEAD 均为 `9d4d52bacb56081b6e061baad40d8884f8742a56`；见 `rc7-start-manifest.json` / `rc7-end-manifest.json`。HEAD 由主 agent 的其他批次提交推进，本 subagent 没有提交。

完整命令：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_priority_country_updates.py \
  tests/unit/test_remaining_surveillance_onboarding.py \
  tests/unit/test_remaining_who_annual.py \
  tests/unit/test_spf_overseas.py \
  tests/unit/test_my_who_sources.py \
  tests/unit/test_wpro_dengue.py \
  tests/unit/test_wpro_dengue_acceptance.py \
  tests/unit/test_cr_notifications.py \
  tests/unit/test_cr_notifications_acceptance.py
```

证据位于 `evidence/20261116_fixes/`：`rc7-extra-sources.log`、`rc7-complete.log`、`rc7-frozen-final.log`、`rc7-start-manifest.json`、`rc7-end-manifest.json`、`rc7-*.patch`。早期 WPRO 年界修复前/后日志也保留，避免把失败证据删成全绿。

## 验收边界

- 本批使用本地原档和离线重放，没有在线抓取、真实数据库写入、通知发信、调度启用、提交或部署。
- CR 覆盖已审查的国家年度 Table 1 和部分登革热/基孔肯雅累计公报表；其他表格、历史 collective workbooks、年龄性别及市级细分仍是覆盖缺口。
- WPRO 只发现当前官方落地页链接的报告；IRIS 历史档案及图表点未还原，缺周保持未知，不能宣称完整历史周序列。累计事实明确标为发布日快照且 cutoff 未验证。
- WHO 年度报告指标不等同于各国完整法定传染病系统；SPF 的源数据分层和定义边界不被自动相加。全部新渠道仍需要各自映射和公开快照门禁。
- CR 两次持久化测试执行生产 store 的 upsert/修订账本逻辑，数据库为 SQLite 内存库，并仅替代 PostgreSQL advisory lock 与 JSON 布尔文本适配。两条 DISTINCT ON 方言警告已记录；不将其称作 PostgreSQL 实库集成验收。
- 指纹只覆盖报告列出的三十一个关键代码/配置/测试文件；最终版本仍由主 agent 在全仓冻结候选上完成整体验收。


## rc.1 + rc.7 最终国家合并复验

后续将原二十六文件与本批九文件合并去重，一次运行三十三文件：**694 passed / 0 failed / 0 skipped，222.86 秒**。八条 SQLite 方言 warning 保留。436 文件双端指纹中 435 不变；唯一测试文件为纯追加一个回归，原内容哈希精确保留，追加后该文件再验 46 passed。生产代码、CO 历史脚本和来源配置在测试期间均未改变。新 fixture 的补充哈希及文件时间另行记录，不将其冒称双端指纹。完整证据及提交边界见 [rc.1 最终复验](FIX_v0.20.0-rc.1_20261116.md) 的末节与 `evidence/20261116_fixes/country-final-*`。本 subagent 至此停止代码变更，国家批次可交主 agent 提交；没有部署。
