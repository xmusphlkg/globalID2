# 非洲、亚洲与大洋洲周/月来源核对（2026-11-17）

核对26个国家/地区。线上版本 `data-20261116T154800Z-reviewed-countries` 的 source-series 与只读数据库快照逐源比对；各源原始粒度优先于 `country_bootstrap.json` 的默认报告类型。证据见 [JSON](evidence/frequency-audit-africa-asia-20261117.json)。

## 已确认的问题

ET、KE、UG、GH、TZ、BN、ID、PH、MM、TL 十个主体在线只有年度数据，数据库也没有周/月源；本轮 WHO FluNet 实际 API 查询十个全部返回近期周记录。这证明有可核验的官方专项周数据尚未接入，不能解释为这些国家没有周报。FluNet 是哨点/非哨点实验室检测，不能替代全国患者病例或全国全病种周报。

ID 另有明确的月度遗漏：WHO WPRO 2026-11-12 原报第2页列出印尼2026年9月登革热1920例、7死（年内累计98935例、263死单独保留）。当前印尼连接器只读年度 Health Profile，尚无此月度来源合同。该数据以WHO分发、原始提供方印尼卫生部标注，不能冒充已接通国家全病种月报。

MY、LA、VN 的网站只有年度，但已有专项高频事实存于数据库：MY FluNet 19722条周检测、iDengue 32条日事实；LA/VN WPRO分别18/7条含周值与累计快照。它们属于逐源准入、口径和公开投影缺口，不能归为原站没有数据。MY FluNet 原合同明确实验室人群、来源重叠，发布和映射通知关闭；LA/VN保留明确七天区间、独立累计类别和部分历史范围，公开仍关闭。旧合同中NG/KH/MW/ZA/TH的private文字已被后续运营者授权和线上发布取代，不能再以旧文字误判未公开。

柬埔寨不是整国没有周源，但线上精确有值周数据只到2019-02-13。数据库最大日期2026-10-19包括图表不可精确提取和缺失单元，不代表近期数值已覆盖。NG线上精确周值到2026-08-23、数据库周事实到2026-11-01，涉及不同病种与计量合同，需逐类别检查后才可认定发布滞后。

## 新一期与解析边界

[WHO WPRO 2026-11-12 #758](https://www.who.int/westernpacific/publications/m/item/dengue-situation-update---758--12-november-2026) 已实际下载并核验SHA256。现有纯解析器离线重放：VN成功解析2026-11-02—11-08新增8898例及独立累计190009例/21死；数据库快照仍只到10-29，存在可跟进的新报。LA原文26—31 October仅六天，现有七天合同拒绝，不能改日期或补一天。

## 全部负责主体

| 代码 | 国家/地区 | 线上来源粒度及最后有值日期 | 核对结论 |
| --- | --- | --- | --- |
| ET | 埃塞俄比亚 | 年 2025-01-01 | 仅 WHO 年度；EPHI 页面有周报栏目，但连续数值原档尚未验证。FluNet 已核实近期周样本。 |
| KE | 肯尼亚 | 年 2025-01-01 | 仅 WHO 年度；MOH 首页的 Weekly News 不能当疫情周报。FluNet 已核实近期周样本。 |
| UG | 乌干达 | 年 2025-01-01 | 仅 WHO 年度；UNIPH 公报目录返回406。FluNet 已核实近期周样本。 |
| GH | 加纳 | 年 2025-01-01 | 仅 WHO 年度；GHS 首页可达不足以证明全国数值周报。FluNet 已核实近期周样本。 |
| TZ | 坦桑尼亚 | 年 2025-01-01 | 仅 WHO 年度；MOH报告入口可达但未建立全国周报合同。FluNet 已核实近期周样本。 |
| NG | 尼日利亚 | 周 2026-08-23 | 已公开周数据；DB还存月度/日度/多周及其他周记录，不等于全部已公开。在线最新周值8-23，需逐类别核对覆盖。 |
| ZA | 南非 | 月 2026-08-01 | 已公开月数据；DB另有季报。当前源档存在月份缺口，季度不能拆月；本轮源目录TLS失败。 |
| MW | 马拉维 | 周 2026-10-25 | 已公开周数据；已知2025/2026缺周，表1不含疟疾，不能声称全病种覆盖。 |
| BN | 文莱 | 年 2025-01-01 | 仅 WHO 年度；卫生部首页不证明周/月数值档案。FluNet 已核实近期周样本。 |
| ID | 印度尼西亚 | 年 2025-12-31 | 仅 Health Profile 年度；已核实 WPRO 的2026-09登革热月病例1920、死亡7；FluNet也有周样本。SKDR跳转登录。 |
| PH | 菲律宾 | 年 2025-01-01 | 仅 WHO 年度；DOH EB页403；WPRO现行合同未启用PH。FluNet已核实近期周样本。 |
| MM | 缅甸 | 年 2025-01-01 | 仅 WHO 年度；WHO有月度 Health Cluster bulletin，尚未验证全国病例合同。FluNet已核实近期周样本。 |
| TL | 东帝汶 | 年 2025-01-01 | 仅 WHO 年度；WHO出版目录本轮未验证连续全国周/月病例。FluNet已核实近期周样本。 |
| VN | 越南 | 年 2025-01-01 | 网站仅年度；DB另有7条WPRO专项记录，混合周值/累计快照，未公开。11-12新报现有解析器可解析3行，尚不在DB快照。 |
| LA | 老挝 | 年 2025-01-01 | 网站仅年度；DB另有18条WPRO专项记录，未公开。11-12新报为六天区间，现有七天合同正确拒绝。 |
| MY | 马来西亚 | 年 2025-01-01 | 网站仅年度；DB另有19722条FluNet周检测、32条iDengue日记录，未公开专项。 |
| KH | 柬埔寨 | 周 2019-02-13 | 已公开周数据但最新公开有值2019-02-13；DB时限到2026不代表近年图表有精确数值。另有[官方月度流感栏目](https://cdcmoh.gov.kh/surveillance/ili-surveillance-2)（HTTP200，2009–2024有缺年）；PDF数值和连续性待核验，未接入。 |
| TH | 泰国 | 周 2026-11-14；月 2023-12-01 | 已公开周和月数据；历史月值至2023，当前DDS周数据至11-14；历史ID_TH周界未验证，仍不能直接绘周日期曲线。 |
| CN | 中国 | 月 2026-10-01 | 已有全国月度来源；CDC Weekly刊名本身不代表全国全病种周病例。 |
| HK | 中国香港 | 月 2026-09-01 | 已有全国/地区月度来源；部分unknown粒度不应自动改判周/月。 |
| TW | 中国台湾 | 月 2026-10-01 | 已有月度来源；本轮未验证额外周源，不等于官方没有。 |
| JP | 日本 | 周 2026-11-01 | 已有全国周度来源；月度数据不是必备另一源，不宜任意按月切分跨月周。 |
| KR | 韩国 | 月 2026-11-01 | 已有全国月度来源；API或原站更新频率不等于入库粒度。 |
| SG | 新加坡 | 周 2026-11-01 | 已有全国周度来源，2012–2022和2023+两条官方来源。 |
| AU | 澳大利亚 | 月 2026-10-01 | 已有全国月度来源；州/领地数据独立，unknown不自动当月。 |
| NZ | 新西兰 | 月 2026-09-01 | 已有全国月度来源；DHB年值是地区来源，不能替代全国周/月。 |

## FluNet 已验证样本（均 HTTP 200）

[WHO FluNet 官方说明](https://www.who.int/tools/flunet)；实际接口 `https://xmart-api-public.who.int/FLUMART/VIW_FNT`，按 `COUNTRY_CODE` 过滤、`ISO_WEEKSTARTDATE desc` 排序、`$top=1`。下面只是最近返回的一条来源/周样本，不是完整年度或历史覆盖验收。

| 国家 | 周起日 | ISO周 | 来源人群 | 流感阳性字段 INF_ALL |
| --- | --- | --- | --- | --- |
| ET | 2026-09-28 | 2026-W40 | SENTINEL | 4 |
| KE | 2026-10-19 | 2026-W43 | SENTINEL | 22 |
| UG | 2026-11-02 | 2026-W45 | SENTINEL | 1 |
| GH | 2026-10-05 | 2026-W41 | SENTINEL | 47 |
| TZ | 2026-11-09 | 2026-W46 | NOTDEFINED | 4 |
| BN | 2026-11-02 | 2026-W45 | NONSENTINEL | 16 |
| ID | 2026-11-02 | 2026-W45 | SENTINEL | 42 |
| PH | 2026-10-26 | 2026-W44 | SENTINEL | 5 |
| MM | 2026-11-02 | 2026-W45 | NONSENTINEL | None |
| TL | 2026-11-02 | 2026-W45 | SENTINEL | 2 |

缅甸最新样本 `INF_ALL=null`，不能改为0；其各独立已报亚型零值与缺失总值必须分开。监测点数量、标本处理数和阳性数也不能相加当患者数。

## 原国家入口访问结果

| 国家 | 官方入口 | 本次结果与证据界限 |
| --- | --- | --- |
| ET | [入口](https://ephi.gov.et/publications/latest-downloads/) | 200；Latest Downloads – Ethiopian Public Health Institute |
| KE | [入口](https://www.health.go.ke/) | 200；MoH / Ministry of Health |
| UG | [入口](https://uniph.go.ug/category/bulletins/) | 406；Not Acceptable! |
| GH | [入口](https://ghs.gov.gh/) | 200；Ghana Health Service - Your Health Our Concern |
| TZ | [入口](https://www.moh.go.tz/services/27) | 200；Services/Ministry of Health |
| ID | [入口](https://skdr.surveilans.org/) | 200；Auth / Auth |
| PH | [入口](https://doh.gov.ph/epidemiology-bureau/) | 403；Just a moment... |
| MM | [入口](https://www.who.int/myanmar/emergencies) | 200； 	Emergencies  |
| BN | [入口](https://moh.gov.bn/) | 200；Ministry of Health - Brunei Darussalam |
| TL | [入口](https://www.who.int/timorleste/publications) | 200； 	Publications  |
| VN | [入口](https://www.who.int/westernpacific/wpro-emergencies/surveillance/dengue) | 200； 	Dengue  |
| NG | [入口](https://ncdc.gov.ng/reports/weekly) | 200；Nigeria Centre for Disease Control and Prevention |
| ZA | [入口](https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/) | SSLError: HTTPSConnectionPool(host='www.nicd.ac.za', port=443): Max retries exceeded with url: /nmc-overview/nmc-monthly |
| MW | [入口](https://phim.health.gov.mw/wp-json/wp/v2/search?search=IDSR&per_page=100&page=1) | 200； |
| KH | [入口](https://cdcmoh.gov.kh/surveillance/camewarn) | 200；CamEWARN |
| TH | [入口](https://dds.moph.go.th/) | SSLError: HTTPSConnectionPool(host='dds.moph.go.th', port=443): Max retries exceeded with url: / (Caused by SSLError(SSL |

ET周报栏目存在不等于本轮下载并解析连续周档；KE Weekly News不是病例报表；PH403和UG406只是访问障碍；ID登录跳转不能当作数值来源已公开；MM Health Cluster月刊不等于连续全国全病种病例。

本轮仅审计，未改库、改调度、改发布或向外部发送消息。完整病种/时间连续性、最新源数据更新和新增连接器需逐源验收；已有周源不要求另造一个月源，已有月源也不表示官方没有其他专项周源。
