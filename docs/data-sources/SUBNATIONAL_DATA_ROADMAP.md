# 分省/分地区传染病数据接入路线

Last reviewed: 2026-08-30

## 结论

其他国家和地区也有可用的分省、州、县市或卫生区数据，可以逐步接入。当前平台已经有
两条可复用路径：

- 中国省级辖区：`CN-*` 作为独立 subdivision jurisdiction，父级为 `CN`。
- 加拿大安大略：`CA-ON` 作为独立 subdivision jurisdiction，父级为 `CA`。

后续不要把分地区事实写到父级国家序列里。每个地区应使用独立 jurisdiction code、
独立 `geography_key` 和独立 source-series 注册；国家排行、全球曲线和国家级汇总继续
只读取国家级 jurisdiction，避免重复计算。

## 优先级

| 阶段 | 候选 | 现有基础 | 建议接入方式 | 主要风险 |
| --- | --- | --- | --- | --- |
| 1 | 台湾县市 | 现有 `TW` 抓取器已读取 NIDSS 年龄/县市/性别 CSV 后汇总为月度全国数据 | 注册 `TW-*` 县市辖区，复用 CSV 解析，先接月度病例数 | 县市代码、境外移入维度和疾病代码变动要失败关闭 |
| 1 | 澳大利亚州/领地 | 已接入框架：`AU-ACT`、`AU-NSW`、`AU-NT`、`AU-QLD`、`AU-SA`、`AU-TAS`、`AU-VIC`、`AU-WA` 使用同一 NINDSS Power BI 抓取器 | 各州/领地作为独立 subdivision jurisdiction，月度病例写入 `country:AU-XX:national` | Power BI 语义模型和字段名变化；部分公开数据集只覆盖少数病种 |
| 1 | 芬兰地区 | 现有 `FI` THL cube 抓取器使用全国 `all areas` 选择器 | 改为枚举 THL 地区维度，注册可发布的医院区/福利服务县辖区 | 地理层级不是 ISO 3166-2，需确认展示名称与边界版本 |
| 2 | 德国州/县 | 现有 `DE` RKI SurvStat WebForms 导出 | 先接 16 个 `DE-*` 州，县级仅作为后续专题 | SurvStat 表单状态复杂；周度修订和州法历史序列要隔离 |
| 2 | 韩国地区 | 现有 `KR` 配置含 KDCA 地区统计页和 PeriodRegion 源 | 注册广域市/道为 `KR-*`，保留“地区别”统计源序列 | 数据口径含国内/境外感染地区，不能误认为居住地 |
| 2 | 新西兰卫生区 | 现有 `NZ` 月报/PHF Science 来源 | 以官方 dashboard 下载数据为准，注册 district health board 或替代行政层级 | DHB 体制变化，需要声明历史边界和当前口径 |
| 3 | 美国州/领地 | 现有 `US` NNDSS 只接 `TOTAL` 和 `US RESIDENTS` | 用 data.cdc.gov/CDC Stacks 周表注册 `US-*` 州和领地序列 | 州/领地报病条件、居民口径、`TOTAL` 与 `US RESIDENTS` 差异必须拆开 |
| 3 | 巴西州/市 | 现有 `BR` SINAN DBC 微数据已汇总到全国月度 | 从微数据地理字段派生 `BR-*` 州级，市级暂不公开 | 个案微数据字段差异、修订、居住地/通知地选择需逐病种审计 |

## 官方来源信号

- 美国 CDC NNDSS：周度数据已迁至 CDC Stacks 和 data.cdc.gov；CDC 说明周表来自州、
  地方和领地报告，且周度数据为 provisional。
  Source: https://www.cdc.gov/nndss/infectious-disease/index.html
- 澳大利亚 NNDSS：官方可视化工具支持按州和领地、年、月、日期范围、年龄和性别查看，
  数据每日更新。
  Source: https://www.cdc.gov.au/resources/apps-and-tools/nndss-data-visualisation-tool
- 台湾 CDC NIDSS：疾病页公开地区、年龄、性别的每周和每月 CSV。
  Source: https://nidss.cdc.gov.tw/en/
- 芬兰 THL：传染病登记统计可按年龄、性别、时间和地区查看；月度和周度 cube 都含地区。
  Source: https://thl.fi/en/data-and-statistics/infectious-diseases
- 德国 RKI SurvStat：查询属性包含 State、NUTS 2 territorial unit、County/Region，
  并支持 CSV 下载。
  Source: https://survstat.rki.de/Content/Query/Main.aspx
- 韩国 KDCA Infectious Disease Portal：全数监测统计含主要统计按地区，年度统计说明为
  可变动的暂定统计。
  Source: https://dportal.kdca.go.kr/pot/is/summaryRginEDW.do
- 新西兰 PHF Science：notifiable disease dashboard 同时提供全国和 district health board
  维度，并说明可下载可视化所用数据。
  Source: https://www.phfscience.nz/digital-library/notifiable-disease-dashboard/

## 接入闸门

每个候选地区上线前必须满足：

- `configs/country_bootstrap.json` 或等价 registry 中有独立 jurisdiction code、
  `parent_country_code`、`location_type=subdivision` 和明确 `geography_key`。
- 源数据保留原生地理标签；未知地区标签、未知疾病标签和重复冲突必须失败关闭。
- `disease_series_observations` 保留 lossless 事实；兼容投影只能写到该 subdivision 的
  country/region id，不能写到父级国家 id。
- 国家级页面、国家排行、全球曲线和下载汇总继续排除 subdivision，除非显式请求地区视图。
- 缺失、未报告、被抑制和零值必须分开处理。
- 每个来源记录发布状态、修订窗口、许可/再分发限制、获取 URL、获取时间和内容 hash。

## 建议执行顺序

1. 台湾县市：最接近现有中国模式，CSV 稳定，改造面小。
2. 澳大利亚州/领地：框架已接入；下一步是生产运行 8 个 `AU-*` 月度任务并核验站点导出。
3. 芬兰地区：cube 结构规范，适合抽象“同一 cube 多地理 selector”。
4. 德国州：高价值、高覆盖，但 WebForms 状态机更重，放在前几项模板稳定之后。
5. 韩国地区、新西兰卫生区：先做源字段审计和边界口径说明，再接公开页面。
6. 美国州/领地、巴西州：数据量和口径复杂，适合在 subdivision Registry 与公开汇总规则更稳后推进。

## 澳大利亚接入状态

澳大利亚州/领地接入采用和 `CA-ON` 一致的 jurisdiction 模式：

- `AU` 保留国家级 NINDSS 月度合计。
- `AU-ACT`、`AU-NSW`、`AU-NT`、`AU-QLD`、`AU-SA`、`AU-TAS`、`AU-VIC`、`AU-WA`
  作为 `location_type=subdivision` 独立注册，父级为 `AU`。
- 抓取器复用同一 Power BI 查询；国家级处理器继续汇总州/领地，州级处理器只写入目标
  subdivision 的行。
- 兼容投影使用父级 `AU` 疾病映射，不复制 8 份 mapping 文件；lossless series 行以
  `GeographyKey=country:AU-XX:national` 保留地区身份。
- 国家排行、全球曲线和国家级汇总继续排除 `AU-*` subdivision，避免与 `AU` 重复计算。

通过控制面板创建月度抓取任务时，将 `country_code` 设为目标州/领地代码，例如
`AU-NSW`。直接调用处理器时，`AUMonthlyUpdater(country_code="AU-NSW")` 会生成
`data/current/au/subdivisions/au-nsw_nindss_monthly.csv`。

## 首个候选的实施切片

以台湾县市为例，最小可发布切片为：

1. 注册县市 jurisdiction metadata，不先改国家级 `TW` 序列。
2. 让 `tw.py` 输出原始县市月度行，同时继续生成现有全国汇总。
3. 为 3-5 个高质量疾病先注册 source-series 并导入 `disease_series_observations`。
4. 生成 `TW-*` 详情页和下载，不纳入国家级汇总。
5. 补充 parser、projection、site export 和汇总排除测试。

完成该切片后，再批量扩展其余县市和疾病映射。
