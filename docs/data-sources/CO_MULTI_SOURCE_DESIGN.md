# 哥伦比亚多数据源采集与指标口径

2026-11-17。本次直接复用了本地官方原件，不重新下载已归档的微观数据。

高频接入的后续设计见[网站高频采集与动态更新方案](CO_HIGH_FREQUENCY_UPDATE_PLAN.md)：
BES 正文明确定义的周通知小表与 WHO FluNet 承担周监测，微观聚合保留历史用途。

## 来源分工

| 来源 | 已核实覆盖 | 采集与用途 | 禁止的合并方式 |
| --- | --- | --- | --- |
| [4hyg-wa9d](https://www.datos.gov.co/d/4hyg-wa9d) | 2007–2022 | 市镇 × 事件 × 周历史汇总；监控元数据、行数及版本，变更后全量对账 | 未核验地域完整性、境外/未知发生地及周历前，不直接声称全国发病数 |
| [wns8-p38i](https://www.datos.gov.co/d/wns8-p38i) | 2022–2024 | 独立后续表；只入库 2023 年及以后，避开重复的 2022 年；继续检测新版本与新产品 | 不把“2023 年起”或元数据更新时间解释为持续覆盖至今 |
| [SIVIGILA Microdatos](https://portalsivigila.ins.gov.co/Paginas/Buscador.aspx) | 年度工作簿至 2025 | 独立来源 `co_ins_sivigila_microdatos`；原件校验、逐表分类、个案按原始年周及分类聚合 | 不把集体表按一行一例计数，不把个案衍生值冒充官方全国周表 |
| [BES](https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx) | 已归档 2025 W1–53、2026 W1–44 | 持续发现新报告；附录存累计快照；比较列存证据；正文周指标须另行核验 | 不把 Acumulado、Observado 或累计差分拼成周新增 |
| [事件报告](https://www.ins.gov.co/buscador-eventos/Paginas/Info-Evento.aspx) | 按事件发布的年报、周期报告 | 归档、病例定义及总量核对；作为公开来源说明中的独立参考来源 | 不作为完整的全病种全国周表 |

这是按来源、指标、分类、地域及统计期分别维护的架构。2025 年微观数据
可补充结束年度的周分布；2026 年现有持续更新材料是 BES，无法凭累计附录
补出一套完整、同口径的全国周新增数据。缺口保持缺失，不补零。

## 微观数据的实际处理结果

2025 年 72 份原件已逐份打开检查。69 份为同一类 `Datos` 个案表，3 份是
集体表：`349`（ETA Colectivo）、`995`（Morbilidad por IRA）、`998`
（Morbilidad por EDA）。集体表中存在病例总数字段，行数代表报告记录数，
本轮不对这些字段猜测求和。

69 份个案表共 **889,556 条记录**，生成 **11,484 条周/分类分层汇总**。
输出的指标名是 `derived_notification_record_count`，按以下维度分层：

- `ANO`、`SEMANA`、`COD_EVE`：源年、源周、事件；不从通知日期倒推周序号。
- `TIP_CAS`、`Estado_final_de_caso`、`AJUSTE`：原始分类、最终病例状态及调整值；
  疑似、确诊、排除记录不自动混成“确诊病例”。
- `COD_PAIS_O`：发生国家；境外及未知发生地独立保留，不默认境内发生。

这些记录是国家通知系统中的事件记录，可能包含哨点、死亡及非传染性事件。
事件代码保持独立；不跨事件去重为人数，不自动映射成普通发病病例或死亡数。
`CONSECUTIVE` 只用于工作簿内的重复记录检查，不进入汇总输出。每个原件保留
URL、修改时间、SHA-256、字段清单和审计结果；任何无效年周、事件不符或重复
记录都会使整个工作簿停止输出。Excel `="07"` 仅作为常量字符串解码，其他
公式不计算。表结构变化则单独列为待核验。

源周历尚未建立正式日期边界，`Date` 是供内部排序的年内序号锚点，不是实际
发病日或官方周末。真实统计标识在 `Dimensions.source_epi_year/week`。
没有出现的周不自动生成零值，2025 年的第 53 周不归入 2026 年。

本轮只生成可审阅的本地汇总，未将衍生口径自动替换已有数据库或公开曲线。
普通爬取任务已可单独选择微观数据来源，保持预览模式；默认关闭其自动调度。
后续入库须按事件确认病例定义、周历及映射，集体表还须确认报告单位、重复/调整
规则和应加总的指标。历史年度可用同一命令指定范围，未知结构不会套用 2025 模板。

## 可重复运行

```bash
# 已归档原件 → 周分层汇总，不需要网络或数据库。
venv/bin/python scripts/imports/co_microdatos_weekly.py --start-year 2025 --end-year 2025

# 用独立 ZIP/XML 读取器对照每份原件的事件/周记录数与 CSV 汇总。
venv/bin/python scripts/verify_co_microdatos_weekly.py

# 仅试跑指定事件，使用单独目录避免覆盖全部事件的汇总。
venv/bin/python scripts/imports/co_microdatos_weekly.py \
  --event 220 --event 780 --output data/current/co/microdatos-weekly-sample

# 正常任务入口；微观数据目前始终为采集/聚合预览。
venv/bin/python main.py crawl --country CO --source co_ins_sivigila_microdatos --save-raw
```

`--force` 的正常任务会先通过现有 INS 传输重新抓取官方目录和原件，然后聚合。
现有采集器按修改时间及原件 hash 复用文件，不因统计年度未结束而虚构新工作簿。
本地命令逐份校验目录快照、元数据及原件，按原件/解析版本复用聚合缓存。
旧原件继续归档，但已从当前官方目录撤回的条目不加入最新汇总。

本机产物：

- `data/current/co/microdatos-weekly/weekly_notification_records.csv`
- `data/current/co/microdatos-weekly/audit.json`：含全部 72 份工作簿的字段与对账。
- `data/current/co/microdatos-weekly/verification.json`：独立原件 XML 核验。
- `data/current/co/source-design-review.json`：五张来源卡片的生成预览。
- `data/current/co/bes-semantic-review.json`：2025 W19/W20 原始 PDF 的复核结果。

## BES 的精度边界

累计附录解析版本升级为 `co-ins-bes-dashboard-parser-v5`，源系列定义仍是累计。
`SourceObservationPeriod=year_to_date`，`WeeklyIncidenceEligible=false`，
`CumulativeDifferenceEligible=false`。识别到的 Esperado/Observado 列在
`SourceComparisonCells` 中保留原始值、列号及窗口未知标记，不作为 `Cases`。
AI 异常页转录仍仅负责累计列，未读到的比较列不声称完整。

原件复核：2025 W19 登革热重症全国累计为 **657**，W20 为 **682**，
W20 的 Observado 为 **124**。累计差为 25，不等于 Observado，也不能据此
确定当周发病数。微观数据的结束年度版本也可能与当时发布的 BES 快照不同，
不得强行配平。

`coverage_complete` 仅描述固定累计附录的提取范围；新增
`weekly_incidence_coverage_complete=false` 明确其不代表全国周曲线完整。
流行通道图、专题正文的单点和低频率小表需要独立的证据与提取契约，不通过
插值、图形估算、前后累计相减或 Observado 代替。本次没有把图中点估算为精确值。

## 公开来源说明

`configs/country_bootstrap.json` 的 `public_source_scopes/details` 现在分别列出
五种来源及其用途和覆盖，旧数据库中的单链接配置不会覆盖这些说明。国家页、
下载来源卡和 About 目录均能显示对应链接和中文说明。来源说明列出官方参考
材料，不代表所有材料都已有可公开的同口径曲线；既有数据发布门槛独立保留。
代码与本地结果准备完成，尚未重新发布线上网站或更新已安装的 BES 独立运行时。
