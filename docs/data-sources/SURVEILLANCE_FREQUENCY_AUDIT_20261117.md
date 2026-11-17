# 全站周报／月报来源核对（2026-11-17）

本轮是只读核对：检查当前线上不可变数据发布、数据库原生事实和官方来源；未改数据库、采集配置或线上发布。按用户要求，欧洲、非洲／亚洲、美洲分别由 subagent 核对，主 agent 完成墨西哥和全站逐主体对账。

## 结论

- 全部 **326 个主体**已核对：**74 个国家／地区级主体、252 个下属行政区**。这里不是全球全部主权国家，也不把下属地区重复算作国家。
- 74 个国家／地区中，**50 个线上只有年度观测、1 个（CO）无公开观测、23 个至少有一类周／月观测**。因此有 **51 个主体缺少已确认的公开周／月数据**，并非官方都没有。
- 23 个已有周／月观测的主体也不能称为“全部疾病完整覆盖”。各病种、年份、指标和缺周仍需独立核对；没有周报或没有月报单独一项，不能自动判错，更不能把年数拆成周／月数。
- 墨西哥是确定的接入遗漏：官方既有月度新增病例表，也有含当周病例的流行病学周报；现在仅接入 DGE 年度年龄／发病率表。
- AR、AT、CO、LA、MY、NL、VN 已有原生高频事实入库，而线上仍无相应周／月来源。部分涉及累计快照、实验室监测或未确认周界，不能仅打开发布开关。CR 的日频是累计发布快照，不能视为日新增病例。
- 61 个下属地区也没有已确认周／月粒度：CH 26 个、DE 16 个、NZ 19 个。DE/CH 的未知粒度需单独修复来源语义，NZ 当前下属地区为年度来源。不能因为父级有周／月数据就声称下属地区也有。

## 核对方法与可重现证据

线上版本为 `data-20261116T154800Z-reviewed-countries`。从线上 `site-data/bootstrap.json` 固定到其 SHA256 指向的 manifest；逐一核验 **326 个来源明细 JSON**和 **326 个国家摘要 JSON**的 SHA256 与字节数，无下载或完整性错误。不能用本地旧导出、数据库 `public_disease_series` 缓存或配置中的 cadence 替代线上真实结果。

同一主体同时检查默认疾病曲线声明的 `period_granularity` 和来源明细的 `temporal_granularity`；有已声明周／月数据即计为“有”，并保留 `unknown`。瑞士是重要例子：来源明细主要为 unknown/annual，但默认曲线仍有 2 类周、1 类月的旧回退曲线，不能误判整个国家没有高频数据。安大略省也有默认月曲线，不能仅凭来源明细为空判缺失。

数据库使用只读事务，按来源与原生粒度统计实际观测及非空、非抑制数值。NL、CO 等来源有年初存储锚点，`max(time)`不等于实际最新报告周；下属地区事实也常存放在父国家系列的 geography 下，不能把缺少独立 country_code 系列解释为没有采集。

- [全量 326 主体矩阵 CSV](evidence/surveillance-frequency-matrix-20261117.csv)：默认曲线、来源明细、原生事实粒度逐项对照。
- [全站机器证据 JSON](evidence/surveillance-frequency-audit-20261117.json)：发布 ID、manifest 哈希、逐文件哈希、粒度／数量、数据库事实和墨西哥原档收据。
- [欧洲逐国核对](FREQUENCY_AUDIT_EUROPE_20261117.md)。
- [非洲／亚洲逐国核对](FREQUENCY_AUDIT_AFRICA_ASIA_20261117.md)。
- [美洲逐国核对](FREQUENCY_AUDIT_AMERICAS_20261117.md)。

## 墨西哥：周表、月表均有，当前未采集

### 月度原表

[官方全国年鉴菜单](https://epidemiologia.salud.gob.mx/anuario/html/morbilidad_nacional.html)明确提供 `Distribución de casos nuevos de enfermedad por mes`。菜单列出 **2003–2025 年、69 个年份／性别版本**的月表选项；这是可发现的菜单范围，尚未逐一下载验证全部 69 份。

本次实际下载并解析了 [2025 全国全年龄、全性别月表](https://epidemiologia.salud.gob.mx/anuario/2025/morbilidad/nacional/distribucion_casos_nuevos_enfermedad_mes.pdf)，HTTP 200，959,780 bytes，4 页。列为 Total 与 Ene–Dic。例：阿米巴肝脓肿 A06.4 年总数 598，12 月值为 `41,43,85,57,33,40,54,51,39,57,50,48`，合计 598。

现有 `src/data/crawlers/mx.py::discover_national_tables` 只选年龄分布病例表及年度发病率表（2002 年例外使用机构表全国合计），没有选择月表。这属于发现／连接器范围缺口，不是前端把月报藏起来。当前数据库 224,601 条 MX 原生事实均为 annual；线上公开 127 条源系列、667 个源系列点，也全部 annual。公开点数与入库原生单元格数不同，涉及来源维度与映射选择，不可直接当成丢失总数。

月表保留来源 ICD、性别、疾病定义、缺失符号和月边界。年总列不与 12 月份重复相加。登革热非重症类别脚注明确含按源公式估算病例，不能把所有月表类别都标成实验室确诊病例。

### 官方周报

[官方周报入口](https://www.gob.mx/salud/acciones-y-programas/direccion-general-de-epidemiologia-boletin-epidemiologico)链接到[2026 年实际目录](https://www.gob.mx/salud/documentos/boletinepidemiologico-sistema-nacional-de-vigilancia-epidemiologica-sistema-unico-de-informacion-417103)。目录列 W01–W43；本次实际下载 [W43 原始 PDF](https://www.gob.mx/cms/uploads/attachment/file/1115232/sem43.pdf)，HTTP 200，2,350,631 bytes，90 页。封面为 2026-10-25 至 10-31。

第 10 页表 2 同时有“当周”“累计”“历史周中位数”“去年当周”等列。**这一期常规监测表实际报告至 W42；部分专项表报告 W43；第 11 页同一张表也可能按病种分别为 W42/W43。** 所以不能把整本公报封面的 W43 当作所有单元格的观测周，更不能把累计值当单周新增或用累计差分猜值。第 10 页另有“初步信息、包括可能病例”的来源限定。

建议分别建立 `mx_dge_monthly` 与 `mx_dge_weekly` 来源身份，先保留原生类别及维度，验证实际表头周、当周/累计口径、全国/州和原文明确缺失，再走现有映射、快照与发布流程；不覆盖年度年鉴来源。

本次原始 PDF/HTML 保存在 `data/raw/mx/frequency-audit-20261117/`，哈希见全站机器证据。

## 已入库而线上缺少高频的重点主体

下面是实际数值数量，不代表全国病例总数；同一来源可能含多个维度或累计快照。

| 主体 | 数据库已有高频事实 | 线上情况 | 需要解决的问题 |
| --- | --- | --- | --- |
| AR 阿根廷 | BEN 7,198 个按周发布的非空累计数值 | 仅 10 个年度 HIV 来源点 | 累计通知／临床／实验室快照不是单周新增，不能直接搬进周病例图；不能把 HIV 年报称为全国 BEN 已覆盖 |
| AT 奥地利 | AGES Radar 441 个月频数值，另 189 个 unknown | 仅 ECDC 年度 | 逐类审核月期与 unknown、映射和来源准入 |
| CO 哥伦比亚 | BES 159,377 个周频非空数值，另 SIVIGILA 历史/新版 2,922,383 个事件／周行 | 来源与默认曲线均无观测 | BES 部分是累计；SIVIGILA 含地区／事件粒度和周界限制，需独立全国与日历合同 |
| LA 老挝 | WPRO 登革热 18 个周频数值 | 仅 WHO 年度 | 专项与全国总疾病分开；新一期六天区间未满足现有七天合同 |
| MY 马来西亚 | FluNet 19,722 个周频数值，iDengue 32 个日频数值 | 仅年度 | 实验室检测与登革热当日／累计指标分别建公开合同 |
| NL 荷兰 | RIVM 病毒学 1,323、呼吸道 3,033 个周频数值 | 仅年度 | 当前周标签缺已核实的实际周界，存储时间是年锚；不能直接画成日期周曲线 |
| VN 越南 | WPRO 登革热 7 个周频数值 | 仅 WHO 年度 | 专项公开合同；2026-11-12 新报实跑可解析，旧快照尚未跟进 |
| CR 哥斯达黎加 | 86 个日频累计发布快照数值 | 仅年度 | 发布日和累计截至日不能改作日／周新增 |

NG 线上已有周频，但数据库另有月频／日频来源未进入线上源明细。需要按其单独指标合同审查，不能把有周报等同全部专项已发布。

## 74 个国家／地区的完整频率对照

`unknown` 表示未声明或未完成来源周期语义核验，不按日期间隔猜成月／周。数据库粒度仅用于追踪原生事实；线上是否已有周／月，以前两列共同判断。

| 代码及主体 | 线上默认曲线粒度 | 线上来源明细粒度 | 数据库原生事实粒度 |
| --- | --- | --- | --- |
| AR 阿根廷 | annual | annual | annual|weekly |
| AT 奥地利 | annual | annual | annual|monthly|unknown |
| AU 澳大利亚 | monthly | monthly | monthly|unknown |
| BE 比利时 | annual | annual | annual |
| BG 保加利亚 | annual | annual | annual |
| BN Brunei Darussalam | annual | annual | annual |
| BQ 荷兰加勒比区（博奈尔、圣尤斯特歇斯和萨巴） | annual | annual | 无 |
| BR 巴西 | monthly|unknown | monthly | monthly|unknown |
| CA 加拿大 | annual | annual | annual |
| CH 瑞士 | annual|monthly|unknown|weekly | annual|unknown | annual|monthly|unknown |
| CL 智利 | annual | annual | annual |
| CN 中国 | monthly | monthly | monthly |
| CO 哥伦比亚 | 无 | 无 | weekly |
| CR Costa Rica | annual | annual | annual|daily |
| CY 塞浦路斯 | annual | annual | annual |
| CZ 捷克 | annual | annual | annual |
| DE 德国 | annual|unknown|weekly | annual|unknown|weekly | annual|unknown|weekly |
| DK 丹麦 | annual | annual | annual |
| EC Ecuador | annual | annual | annual |
| EE 爱沙尼亚 | annual | annual | annual |
| ES 西班牙 | annual | annual | annual |
| ET Ethiopia | annual | annual | annual |
| FI 芬兰 | annual|monthly | annual|monthly | annual|monthly |
| FR 法国 | annual | annual | annual |
| GB 英国 | annual | annual | annual |
| GF French Guiana | annual | annual | annual |
| GH Ghana | annual | annual | annual |
| GP Guadeloupe | annual | annual | annual |
| GR 希腊 | annual | annual | annual |
| HK 中国香港 | monthly | monthly | monthly|unknown |
| HR 克罗地亚 | annual | annual | annual |
| HU 匈牙利 | annual | annual | annual |
| ID 印度尼西亚 | annual | annual | annual |
| IE 爱尔兰 | annual|unknown|weekly | annual|weekly | annual|weekly |
| IS 冰岛 | annual|monthly|weekly | annual|monthly|weekly | annual|monthly|weekly |
| IT 意大利 | annual | annual | annual |
| JP 日本 | unknown|weekly | weekly | weekly |
| KE Kenya | annual | annual | annual |
| KH 柬埔寨 | weekly | weekly | weekly |
| KR 韩国 | monthly | monthly | monthly|unknown |
| LA Lao PDR | annual | annual | annual|weekly |
| LI 列支敦士登 | annual | annual | annual |
| LT 立陶宛 | annual | annual | annual |
| LU 卢森堡 | annual | annual | annual |
| LV 拉脱维亚 | annual | annual | annual |
| MM Myanmar | annual | annual | annual |
| MQ Martinique | annual | annual | annual |
| MT 马耳他 | annual | annual | annual |
| MW 马拉维 | weekly | weekly | weekly |
| MX 墨西哥 | annual | annual | annual |
| MY 马来西亚 | annual | annual | annual|daily|weekly |
| NG 尼日利亚 | weekly | weekly | daily|monthly|multiweek|weekly |
| NL 荷兰 | annual | annual | annual|weekly |
| NO 挪威 | annual|monthly | annual|monthly | annual|monthly |
| NZ 新西兰 | monthly | monthly | annual|monthly |
| PA Panama | annual | annual | annual |
| PE 秘鲁 | weekly | weekly | weekly |
| PH Philippines | annual | annual | annual |
| PL 波兰 | annual | annual | annual |
| PT 葡萄牙 | annual | annual | annual |
| PY Paraguay | annual | annual | annual |
| RO 罗马尼亚 | annual | annual | annual |
| SE 瑞典 | annual|monthly | annual|monthly | annual|monthly |
| SG 新加坡 | weekly | weekly | weekly |
| SI 斯洛文尼亚 | annual | annual | annual |
| SK 斯洛伐克 | annual | annual | annual |
| TH 泰国 | monthly|weekly | monthly|weekly | monthly|weekly |
| TL Timor-Leste | annual | annual | annual |
| TW 中国台湾 | monthly|unknown | monthly | monthly|unknown |
| TZ Tanzania | annual | annual | annual |
| UG Uganda | annual | annual | annual |
| US 美国 | annual|unknown|weekly | weekly | annual|weekly |
| VN Viet Nam | annual | annual | annual|weekly |
| ZA 南非 | monthly | monthly | monthly|quarterly |

## 后续顺序

1. 先补墨西哥月表与周报的独立来源，使用本次已下载原档做真实样本验证。
2. 修复“已入库但未公开”的高频来源，并单独处理累计快照、周界和映射限制；不把所有来源统一视为患者新增病例。
3. 对区域报告已取得真实数值的候选优先接入：例如欧洲 ECDC 呼吸道专项周 CSV、爱沙尼亚月表、加拿大病毒检测周 CSV、巴拿马周报、法属三地区周监测指标、WHO FluNet 与印尼登革热月值。
4. 官网不可达、仅找到目录或数值合同尚未验证的国家继续列为待核验。年报基线继续保留；来源有周数据不要求再生造月报，来源只有月表也不要求拆出周数据。

本轮没有实施新连接器或发布新数据。完整逐期缺报回填、疾病映射及新来源上线属于后续修复工作，不由本轮频率存在性核对替代。
