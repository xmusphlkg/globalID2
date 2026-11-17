# 美洲周/月数据源核对（2026-11-17）

本轮仅审计，未写数据库、修改来源准入或发布网站。墨西哥由主审计单独记录。
线上依据为 `data-20261116T154800Z-reviewed-countries` 的不可变清单、国家
`-summary.json` 默认曲线及 `-source-series.json`，下载均由主审计核对 SHA256。
数据库依据为 2026-11-17 09:07 左右的只读观测账本汇总。线上默认曲线、公开源详情、
内部入库是三个不同层次；内部存在 `weekly` 标签也不意味着可公开当周新增病例。

## 已确认的新增高频来源缺口

| 地域 | 当前线上默认粒度 | 本轮实际核验的官方高频证据 | 判断与约束 |
| --- | --- | --- | --- |
| CA 加拿大 | 年度 | [PHAC FluWatch+](https://health-infobase.canada.ca/respiratory-virus-surveillance/) 及其页面 JavaScript 指向的 [RVD_WeeklyData.csv](https://health-infobase.canada.ca/src/data/respiratory-virus-detections/RVD_WeeklyData.csv) 均 HTTP 200。CSV 49,680 字节、800 行，日期 2026-09-05—2026-11-07，含明确 `province=Canada` 全国行以及省区行，`week/year/date/virus/tests/detections/percentpositive` 字段。 | **官方周度实验室监测源尚未接入**。10 个病毒标签包含流感总类与 A/B 子类，不能相加；检出、检测数、阳性率分别保留，不能替代 CNDSS 全国全病种通知病例。地区汇总与 Canada 行不能重复相加，`N/A` 不能变零。 |
| PA 巴拿马 | 年度 WHO 基线 | [MINSA 2026 周报目录](https://www.minsa.gob.pa/publicacion/boletin-epidemiologico-semanal-3) HTTP 200，实际下载 [2026 第 1 周原始 PDF](https://minsa.b-cdn.net/sites/default/files/migration/publication//boletin_epidemiologico_semanal_no_1_2026_v1_pdf.pdf)，HTTP 200，1,154,728 字节、12 页。封面明确 2026-01-04—01-10。 | **官方全国周报尚未接入，原访问阻塞本轮已解除**。PDF 第 3—4 页明确区分当周 A 与累计 AC，病例与发病率分开，数据为初步值；例如当周细支气管炎 401、IRAG 493、流感综合征 1,052。必须分别解析当周/累计和 2025/2026 对比列。此次仅验一个完整原件，不宣称全档案已验证。 |
| GP 瓜德罗普 | 年度 SPF 基线 | [SPF ODISSÉ 流感区域周数据](https://odisse.santepubliquefrance.fr/explore/dataset/grippe-passages-urgences-et-actes-sos-medecin_reg/) 的数值 API HTTP 200，分组查询返回 GP、MQ、GF 各 1,830 行；最新样本 `2026-S45`、首日 2026-11-02。 | **官方周度综合征指标尚未接入**。急诊流感/流感综合征比例、相关住院比例和 SOS Médecins 比例，不是报告病例数；按源单位保存并明确网络覆盖。 |
| MQ 马提尼克 | 年度 SPF 基线 | 同上，独立地域 `reglib=Martinique`、区域码 `02`。 | 同上；不能并入 GP 或法国全国病例总量。 |
| GF 法属圭亚那 | 年度 SPF 基线 | 同上，独立地域 `reglib=Guyane`。 | 同上；按当地报告值和缺失状态保存。本轮 SPF 主站地域页 403，ODISSÉ 数值 API 实际成功，所以不能用主站访问失败推断没有周数据。 |

上述 CA、PA、GP/MQ/GF 都取得了实际官方数值字节，不只是机构主页或搜索结果。
精确 URL、大小、SHA256、字段及样本见 [结构化证据](evidence/frequency-audit-americas-20261117.json)。
SPF 的住院指标字段说明写 `Pour 100 000 passages`，长描述则以急诊后住院总数为
分母；这处元数据差异应在接入时保留并核实，不应套用统一人口或就诊分母。

## 已入库但线上未提供相应高频数据

| 地域 | 内部与线上事实 | 原因边界与处理方向 |
| --- | --- | --- |
| AR 阿根廷 | 内部 BEN 7,326 条 `weekly`，其中 7,198 有值，截止锚 2026-10-31；线上只有 2014—2023 年 HIV 年度通知 10 点。 | [BEN 合同](AR_BEN_INTEGRATION.md)明确这些是**按周发布的累计通知/临床/实验室快照**，不是当周新增。当前保留 BEN 发布限制是已知合同边界，不能直接把累计值搬入周病例图。可继续单独审核 [替代官方数据](AR_ALTERNATIVE_SOURCES_20261116.md)。 |
| CO 哥伦比亚 | 内部 SIVIGILA 历史/新版合计 2,922,383 条，另有 BES 159,393 条，其中 159,377 有值，最新锚 2026-11-07；线上源详情与默认曲线均为空。 | 不能沿用旧“2025/26 文件未下载”的结论。[BES 新合同](CO_BES_INTEGRATION.md)确认已捕获 97 份 2025—2026 公报并入库，但固定附录是**年内累计值**，八个印刷错误单元仍不可读，公开准入待审核；历史 SIVIGILA 周界/全国口径也不能凭标签猜测。属于已采集、未进入公开可比曲线。 |
| CR 哥斯达黎加 | 内部有 86 个 `daily` 精度记录（2025—2026），另外年度基线；线上仍只有 WHO 年度源。 | [卫生部合同](CR_MOH_NOTIFICATIONS.md)说明 `daily` 是封面发布日期精度，数值为登革热/基孔肯雅**累计快照**，不是逐日、逐周新增病例。其他病种表、旧版集体报表尚未解析，不宜把 86 条误计为已覆盖全国日/周监测。 |

## 其他负责地域逐项结论

| 地域 | 线上默认数据 | 核对结论 |
| --- | --- | --- |
| CA-ON 安大略省 | 月度 | 有独立省级月数据，**不属于无高频**。公开源详情为空不代表默认曲线没有数据。内部月度有值 486 条，覆盖 2026-01—09；保持与 CA 全国分开，不能用安省填加拿大全国。见 [省级合同](CA_HIGH_FREQUENCY_SURVEILLANCE_PLAN.md)。 |
| CL 智利 | 年度 | 本轮官方 CKAN 元数据 HTTP 200；检查已归档 68,280,694 字节原始 CSV 的全部 39 列，唯一可用时间字段是 `anho_notificacion`，**没有月、周或日期字段**。现有 ENO 接入按年不是丢弃已有月字段。另查 `epi.minsal.cl` 返回 403；其他官方专题周报仍需独立数值源核验，不能声称智利无周报。见 [ENO 合同](CL_ENO_HISTORICAL.md)。 |
| EC 厄瓜多尔 | 年度 WHO 基线 | [MSP 流行病学公报入口](https://www.salud.gob.ec/gacetas-epidemiologicas/) 本轮仍 TLS 失败。国家高频源是明确覆盖待办，但未取得完整原件，不能据此判定官方没有数据或宣称解析可用。 |
| PY 巴拉圭 | 年度 WHO 基线 | [DGVS 周报入口](https://dgvs.mspbs.gov.py/boletin-epidemiologico-semanal/) 本轮仍 TLS 失败。与 EC 一样保留源访问/合同核验待办，不能判定“无周报”。 |
| BQ 荷属加勒比地区 | 年度 | [RIVM 通知表](https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen) 本轮 HTTP 200；当前 BQ 仅登革热/基孔肯雅两类年度记录 20 点，2016—2025。没有核验到独立 BQ 连续周/月数值源，列为**待发现**，不能把 NL 实验室周源移用为 BQ。 |
| BR 巴西 | 月度 | 已有 SINAN 月数据，线上源详情 11 个系列、1,873 点，最新 2026-10。不是整国周/月缺失。内部同时有较多 `unknown` 粒度记录，需单独审时间合同；有月数据不等于所有病种/州/月份完整。官方 [SINAN](https://portalsinan.saude.gov.br/) 本轮 HTTP 200。 |
| PE 秘鲁 | 周度 | 有周数据，线上源详情最新 2026-07-25、1,923 点；内部最新锚 2026-08-08、26,521 条。存在**公开与内部覆盖差异、历史解析不完整**，不是完全未接周报；是否为更新滞后，需按同一病种／来源指标及公开选择逐序列核对，不能仅用两个最大日期确认。[官方周报入口](https://www.dge.gob.pe/portalnuevo/informacion-publica/boletines-epidemiologicos/) 本轮 HTTP 200。实际回填进度以运行状态为准，旧文档的 2,365 条不能当本轮现状。 |
| US 美国 | 周度 | 有 NNDSS 周数据，线上源详情最新 2026-11-07、10,561 点；内部还保留年度 NHSS 及独立呼吸道实验室/住院周源。官方 [NNDSS 当前 API](https://data.cdc.gov/resource/x9gk-5huc.json?$limit=1) 本轮 HTTP 200。属于有高频，不应列为整体遗漏。各州/属地完整性另按自身来源覆盖审计，不把全国存在数据等同每州每周完整。 |

美洲的优先顺序：先推进已拿到明确数值原件的 PA 全国当周 ENO、CA 实验室周源、
GP/MQ/GF 周度综合征指标；并行核对 PE 的覆盖／更新差异。AR/CO/CR 按累计指标和公开合同
单独处理，不能为填满“周报”标签改变数值含义；EC/PY/CL/BQ 继续来源可达性与
独立数据合同核验。
