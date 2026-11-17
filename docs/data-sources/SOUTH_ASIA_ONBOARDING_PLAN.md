# 南亚五国计划支持

日期：2026-11-16，Asia/Shanghai。印度、巴基斯坦、孟加拉国、尼泊尔和斯里兰卡加入网站地理目录及覆盖地图的 **Scheduled／规划中** 清单。这是来源接入计划，不是数据入库或公开国家主页的完成声明。

## 来源计划

| 代码 | 国家 | 目录中的计划来源 | 报告周期 | 后续接入范围 |
| --- | --- | --- | --- | --- |
| IN | 印度 | WHO GHO 国家报告年度指标 | 年度 | 先核验现有 WHO 年度连接器可复用的指标；IDSP 暴发报告须另审统计口径，不视作全国常规病例 |
| PK | 巴基斯坦 | NIH IDSR Weekly Reports | 周度 | 国家周报优先；WHO 年度基线保持独立来源身份 |
| BD | 孟加拉国 | WHO GHO 国家报告年度指标 | 年度 | 年度基线；国家专项监测另行核验 |
| NP | 尼泊尔 | WHO GHO 国家报告年度指标 | 年度 | 年度基线；EDCD／EWARS 的连续归档与数值表另行核验 |
| LK | 斯里兰卡 | WHO GHO 国家报告年度指标 | 年度 | 年度基线；国家流行病学周报的访问与数值合同另行核验 |

WHO 入口：[GHO](https://www.who.int/data/gho)。巴基斯坦官方 [NIH IDSR 周报目录](https://www.nih.org.pk/phb/weekly-bulletin) 已核实可访问，列有 2026 年第 42、41、40 周等报告链接；目录可访问不代表 PDF 数值解析、疾病映射和全国覆盖已完成。上一轮实际抽查 WHO `WHS3_62` 麻疹指标，五国均有 2025 年数值记录；其他指标仍需逐项核验。

本次仅增加目录及分类，没有添加占位观测、采集任务或国家 bootstrap，没有启用生产发布门禁。目录在没有真实公开快照时显示“规划中的数据源”，不提供不存在的国家主页链接。`publication_pending` 未设置，因为这些五国尚未像既有入库国家一样进入数据发布验收。

## 官方分类核验

2026-11-16 实际读取 [WHO Country Dimension](https://ghoapi.azureedge.net/api/DIMENSION/COUNTRY/DimensionValues) 与[世界银行国家 API](https://api.worldbank.org/v2/country?format=json&per_page=400)，并使用世界银行五个单国 API 交叉确认。结果保存到网站既有分类目录 `astro-site/src/lib/data/country-classifications.json`。

| 代码 | WHO 区域 | 世行区域 | 收入组 |
| --- | --- | --- | --- |
| IN | SEAR | SAS | LMC |
| PK | EMR | MEA | LMC |
| BD | SEAR | SAS | LMC |
| NP | SEAR | SAS | LMC |
| LK | SEAR | SAS | UMC |

巴基斯坦按当前 API 归入世行 MEA，斯里兰卡按当前 API 为中高收入；不以地理名称“南亚”替代上述正式筛选分类。国旗使用已有本地完整 ISO 国旗库，无需额外下载。

## 验证

- 现有目录、分类及国旗测试：11 项通过。
- `npm run check`：525 个文件，0 errors、0 warnings、14 hints；样式边界检查通过。
- 五国目录唯一性、Scheduled 状态及分类的定向检查通过。
- 本地 Chromium 浏览器逐项核验五国：英文 1280px、中文 390px、法文 1280px 共 15 项全部通过。每项验证“规划中的数据源”对应语言文案、没有虚假国家主页链接、国旗可实际解码。本地预览使用既有公开 meta 作为请求夹具，不生成或改写事实快照。

公开发布仍需完成真实采集、原件留存、源原生口径核验、映射、质量检查、快照和页面验收。发布网站目录后，只表示这五国进入计划支持。
