# 非洲、东南亚与拉丁美洲数据接入计划

更新：2026-11-16。NG、KH、AR 首批已实际入库，PE 持久回填继续；按用户要求，继续由 subagent 并行推进其余可接入国家。

“全量”指官方公开可发现、可验证的历史与源原生类别；不是全部实际感染，也不保证官方从未发布或已撤回的报告可恢复。原始文件、组织后的事实、数据库入库和公开快照分别核验。不能解析的图表、失效链接和缺周必须进入审计，不能补零。

## 首批执行

| 国家 | 状态 | 范围与实施记录 |
| --- | --- | --- |
| 尼日利亚 NG | 首批数据库入库完成 | NCDC 周报及各病种情况报告；[接入记录](NG_NCDC_INTEGRATION.md) |
| 柬埔寨 KH | 首批数据库入库完成 | CamEWARN 全历史档案、全部发布类别；[接入记录](KH_CAMEWARN_INTEGRATION.md) |
| 阿根廷 AR | 首批数据库入库完成 | BEN 全公开历史、全部可验证数值表；[接入记录](AR_BEN_INTEGRATION.md) |
| 秘鲁 PE | 持久全量回填执行中 | CDC Perú 历史周报、全部可验证疾病监测表；[接入记录](PE_CDC_INTEGRATION.md) |

## 本轮已核验进度（2026-11-15）

原档抓取与数值提取分别统计；以下数字不表示已经公开发布。

| 来源 | 已持久保存的原档 | 已组织事实 | 尚未完成的范围 |
| --- | --- | --- | --- |
| NG NCDC | 1,645 / 1,690 个发现的 PDF；430 周报路由及 12 病种目录已逐项检查 | 15,128 条精确原生观测已入库，1,225 / 1,645 份报告已解析；177 条 holding 序列 | 45 个文件及 8 个目录入口重试后仍失败；420 份尚未完成数值提取，64 个冲突已隔离 |
| KH CamEWARN | 全部 39 个链接文件；展开后 401 个 PDF，含索引/压缩档共 412 个 artifacts | 2,775 条原生类别／周记录：1,775 个精确值，958 个仅图表未知值，42 个矩阵空单元 | 24 个文件未通过独立解析核验；2023/2024 未列于当前官方目录；2,775 条已入库（含 1,000 条明确未知），公开快照待校验 |
| AR BEN | 现行官方目录 199 个 PDF，含索引及计划共 205 个 artifacts | 7,326 条事实：7,198 个有值，128 个明确缺失，286 个按统计口径区分的原生身份 | 116 个公报尚无受支持的 ENO 全国汇总表；13 个复杂单元格拒绝提取；旧归档 TLS 不可用；7,326 条已入库（含 128 条明确缺失），公开快照待校验 |
| PE CDC | 完整目录 1,714 行、1,661 个独立 PDF URL；已处理 94 个 PDF，9 个不可变批次已应用 | 验收时 2,365 条精确全国指标已入库；持久任务持续下载并自动分批入库 | 当前仍有 1,567 个待归档文件；旧文档 OCR 与源站低速需持续审计；不得将已发现目录误称全档案已下载 |

最终入库状态以 `data/current/<国家代码>/database_import_receipt.json` 和数据库 `run_stages` 为准。源原生事实保留在 holding 序列；公开标准疾病快照通过现有映射与发布校验后，国家目录及地图才转为已支持。

## 第二批执行（2026-11-16）

三个区域 subagent 分别负责官方历史采集、源合同、离线解析和真实样本验证；主 agent 统一注册、实际入库与验收。目录覆盖不等于数值解析或数据库完成。

| 国家 | 状态 | 已确认官方范围 | 入库状态 |
| --- | --- | --- | --- |
| 南非 ZA | 正式入库及逐条只读核验完成 | 39 PDF + 1 DOCX 共 40 份原档；17,013 条事实（16,774 有值、239 明确未知），全国与 9 省分别保留，月报与季报分开 | 数据库逐条对账通过；公开发布待映射和快照核验 |
| 印度尼西亚 ID | 正式入库及逐条只读核验完成 | 2017–2025 全部 9 册、23 个 artifacts；881 条全国事实（879 有值、2 明确缺失），统计年份 2015–2025 | 数据库逐条对账通过；公开发布待映射和快照核验 |
| 墨西哥 MX | 224,601 条入库及逐条只读核验完成 | 176/176 PDF、179 个 artifacts；224,601 条事实（212,971 有值、459 明确缺失、11,171 不适用），43,682 个类别/序列；全国、性别、年龄、病例／发病率分开 | 已解析事实全部入库，对账差异为 0；数值提取仍部分覆盖，公开发布待映射和快照核验 |

后续 ET、KE、UG、GH、TZ、PH、VN、LA、MM、BN、TL、PY、EC、CR、PA 的 WHO 年度基线和 GP、MQ、GF 的 SPF 年度报告已实际入库，见下方第四批；CO、CL 的单独进度见第三批。

## 第三批执行（2026-11-16）

智利、哥伦比亚和马拉维的官方来源、国家专用解析器、数据库接入器与私有 holding 序列已接通；原档和数据库分别验收，公开发布仍关闭。智利和马拉维的每日检查任务已创建并启用。哥伦比亚 254 个批次、2,465,826 行历史数据已全部入库并通过最终逐行验证；每日元数据与逐年行数检查已启用，源版本变化会明确要求重新全量回填。

| 国家 | 官方历史范围 | 当前进度 | 待核验 |
| --- | --- | --- | --- |
| 智利 CL | MINSAL ENO CSV，2007–2024，333,300 病例行 | 5 份原档；442 条年度原生观测已入库并逐条复核，计数与原 CSV 一致；[接入记录](CL_ENO_HISTORICAL.md) | 官方目录说明与当前 CSV 范围不一致；疾病映射、权利与公开快照待审核 |
| 哥伦比亚 CO | INS SIVIGILA 官方 SODA 数据集，2007–2022，共 2,465,826 条市级事件／周记录 | 254 个批次、2,465,826 行全部入库；16 个年份逐行对账零差异，每日元数据检查已启用；[接入记录](CO_SIVIGILA_INTEGRATION.md) | 官方周界与全国分母未确认，不能发布为全国周病例 |
| 马拉维 MW | PHIM IDSR 官方帖子及媒体目录，2025–2026 存在缺周 | 91 份原档含 57 份 PDF；56 份范围内报告形成 1,241 条全国周观测、28 个原生 holding 序列，逐字段数据库对账零差异；[接入记录](MW_PHIM_IDSR_INTEGRATION.md) | 2025 年 W7–W50 间缺 22 周、2026 年 W2–W43 间缺 9 周；疟疾、地区附录和累计叙述未纳入本合同，疾病映射与公开快照待完成 |

## 第四批执行（2026-11-16）

18 个新增国家和地区均已完成真实原档采集、离线重放、正式入库及逐字段数据库对账；18 个年度渠道和哥斯达黎加卫生部、老挝及越南 WPRO 登革热三个独立专项源，均已完成真实调度器触发的 collect/organize/store 刷新；最终 21 个来源、11,024 条观测逐字段验收全部通过。定时运行与数据验收见[本轮验收](../acceptance/REMAINING_COUNTRIES_20261116.md)。

| 范围 | 已入库年度观测 | 数据合同 |
| --- | ---: | --- |
| ET、KE、UG、GH、TZ | 2,605 | WHO 分发的国家报告计数，保留原始年度与明确未知 |
| PH、VN、LA、MM、BN、TL | 2,823 | 同上；18 个已审核指标中，各国实际有报告的类别和年份不同 |
| PY、EC、CR、PA | 1,832 | 同上；国家原始公报与 WHO 年度基线独立 |
| GP、MQ、GF | 3,589 | SPF ODISSÉ 五套年度疾病报告，年龄／血清群及定义版本独立 |

年度合计 10,849 条，不含三个专项源。详见[WHO 年度合同](WHO_REMAINING_COUNTRIES.md)、[SPF 海外省合同](SPF_OVERSEAS_INTEGRATION.md)、[CR 卫生部合同](CR_MOH_NOTIFICATIONS.md)及[WPRO 登革热合同](WHO_WPRO_DENGUE.md)。所有渠道均保留原档并更新历史修订，不删除仅因本次抓取遗漏的已存事实，不补零；公开发布仍关闭。国家原始周报未能验证的来源及访问阻塞见[国家来源审计](REMAINING_NATIONAL_SOURCE_AUDIT.md)，不能将年度基线解释为全部疾病或最新一周已覆盖。

## 全部候选与扩展计划

| 代码 | 国家或地区 | 计划 | 官方入口 |
| --- | --- | --- | --- |
| ZA | 南非 | 内部事实已入库，官方渠道和每日更新计划已接入 | [NICD NMC Monthly Surveillance Report](https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/) |
| NG | 尼日利亚 | 首批事实已入库，剩余解析缺口见接入记录 | [NCDC Weekly Epidemiological Reports and Situation Reports](https://ncdc.gov.ng/reports/weekly) |
| ET | 埃塞俄比亚 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [Ethiopian Public Health Institute](https://ephi.gov.et/national-diseases-surveillance-and-emergency-response/) |
| KE | 肯尼亚 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [Ministry of Health Kenya](https://www.health.go.ke/) |
| UG | 乌干达 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [Uganda National Institute of Public Health](https://uniph.go.ug/) |
| GH | 加纳 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [Ghana Health Service](https://ghs.gov.gh/) |
| KH | 柬埔寨 | 首批事实已入库，剩余解析缺口见接入记录 | [Ministry of Health CamEWARN](https://cdcmoh.gov.kh/surveillance/camewarn) |
| ID | 印度尼西亚 | 内部事实已入库，官方渠道和每日更新计划已接入 | [Ministry of Health Indonesia Health Profile](https://kemkes.go.id/id/category-download/profil-kesehatan) |
| PH | 菲律宾 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [Department of Health Philippines; WHO WPRO dengue updates](https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue) |
| VN | 越南 | WHO 年度基线和 WPRO 登革热专项已入库；分别每日刷新 | [Ministry of Health Viet Nam; WHO WPRO dengue updates](https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue) |
| LA | 老挝 | WHO 年度基线和 WPRO 登革热专项已入库；分别每日刷新 | [WHO WPRO dengue updates](https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue) |
| MY | 马来西亚 | 扩展已有管线覆盖 | [MOH annual STD notifications; broader disease coverage planned](https://data.gov.my/data-catalogue/std_state) |
| MX | 墨西哥 | 已解析事实全部入库并逐条核验；官方渠道和每日计划已接入 | [DGE Anuario de Morbilidad](https://epidemiologia.salud.gob.mx/anuario/html/index.html) |
| AR | 阿根廷 | 首批事实已入库，剩余解析缺口见接入记录 | [Ministerio de Salud Boletín Epidemiológico Nacional](https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional) |
| PE | 秘鲁 | 持久全量回填执行中 | [CDC Perú Boletines Epidemiológicos](https://www.dge.gob.pe/portalnuevo/informacion-publica/boletines-epidemiologicos/) |
| CO | 哥伦比亚 | 2,465,826 行历史数据已入库并逐行验收；每日元数据检查已启用 | [INS SIVIGILA government open data](https://www.datos.gov.co/d/4hyg-wa9d) |
| CL | 智利 | 442 条历史观测已入库并逐条验收；每日检查已配置 | [MINSAL ENO open data](https://datos.gob.cl/dataset/notificaciones-eno) |
| MW | 马拉维 | 1,241 条全国周观测已入库并逐条验收；每日检查已配置 | [PHIM IDSR bulletin archive](https://phim.health.gov.mw/wp-json/wp/v2/search?search=IDSR&per_page=100&page=1) |
| PY | 巴拉圭 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [DGVS Boletín Epidemiológico Semanal](https://dgvs.mspbs.gov.py/boletin-epidemiologico-semanal/) |
| EC | 厄瓜多尔 | WHO 年度基线已入库、每日全历史刷新；国家原始周报仍有覆盖缺口 | [MSP Gacetas Epidemiológicas](https://www.salud.gob.ec/gacetas-epidemiologicas/) |
| TZ | 坦桑尼亚 | WHO 年度基线已入库；每日全历史刷新 | [WHO GHO](https://www.who.int/data/gho) |
| MM | 缅甸 | WHO 年度基线已入库；每日全历史刷新 | [WHO GHO](https://www.who.int/data/gho) |
| BN | 文莱 | WHO 年度基线已入库；每日全历史刷新 | [WHO GHO](https://www.who.int/data/gho) |
| TL | 东帝汶 | WHO 年度基线已入库；每日全历史刷新 | [WHO GHO](https://www.who.int/data/gho) |
| CR | 哥斯达黎加 | WHO 年度基线及卫生部专项已入库；分别每日刷新 | [卫生部](https://www.ministeriodesalud.go.cr/) |
| PA | 巴拿马 | WHO 年度基线已入库；国家公报 CDN 自重定向阻塞另见审计 | [WHO GHO](https://www.who.int/data/gho) |
| US-PR | 波多黎各 | 已核验保留地区原生观测；沿用 US NNDSS 每 30 分钟刷新 | [US CDC NNDSS territory observations](https://data.cdc.gov/browse?category=NNDSS) |
| US-VI | 美属维尔京群岛 | 已核验保留地区原生观测；沿用 US NNDSS 每 30 分钟刷新 | [US CDC NNDSS territory observations](https://data.cdc.gov/browse?category=NNDSS) |
| BQ | 荷属加勒比地区 | 20 条、2 个原生系列已核验；地域登记已补齐，沿用 NL RIVM 父级刷新 | [RIVM Caribbean Netherlands notifications](https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen) |
| GP | 瓜德罗普 | SPF ODISSÉ 五套年度数据已入库；每日全历史刷新 | [Santé publique France Antilles](https://www.santepubliquefrance.fr/regions/antilles) |
| MQ | 马提尼克 | SPF ODISSÉ 五套年度数据已入库；每日全历史刷新 | [Santé publique France Antilles](https://www.santepubliquefrance.fr/regions/antilles) |
| GF | 法属圭亚那 | SPF ODISSÉ 五套年度数据已入库；每日全历史刷新 | [Santé publique France Guyane](https://www.santepubliquefrance.fr/regions/guyane) |
| TH | 泰国 | 扩展现有 Report 506 历史，并独立验证 DDS 当前序列 | [接入记录](TH_DDS_INTEGRATION.md) |
| BR | 巴西 | 已有 SINAN 管线，继续历史修订与地区覆盖 | [增量策略](BR_SINAN_INCREMENTAL.md) |
| SG | 新加坡 | 已有周报管线，继续历史补齐与修订 | [来源清单](README.md) |


## 执行与发布约定

- 未实现的候选仅加入计划及网站 Scheduled 清单，不启用空壳采集任务。
- 每个源保留全部原生类别；未审核类别进入独立 holding series，不强行映射标准疾病。
- 全国与地区、疑似与确诊、病例与死亡、累计与本期、综合征与实验室检测分别保留。
- 当前轮以真实采集、可离线重放组织和数据库事实入库为目标；公开支持状态以已核验快照为准。
- 原始文件批次及 source_audit 记录发现数量、成功下载、解析/入库数量、失败与未覆盖的范围。

## 七国持续更新

七个官方更新渠道及实际 discovery URL 持久保存在 `configs/surveillance_channels.json`，控制面板数据源、手动采集及自动化表单共用同一渠道目录。每日检查新发布及近期修订；NG/KH/AR/PE/ZA 默认回查最近 2 个日历年，ID/MX 默认 3 年，保留来源原本的发布周期。历史全量回填独立持续，不用日更任务替代。部署和运行验收见[七国动态更新记录](../acceptance/SEVEN_COUNTRY_DYNAMIC_UPDATES_20261116.md)。
