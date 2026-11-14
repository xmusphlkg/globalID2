# Iceland 数据接入终审核对单

> 状态：本地待终审。本文档对应 2026-08-07 抓取与导入结果；未执行外部下载仓库推送或 Cloudflare 发布。

## 1. 接入边界

国家代码为 `IS`，国家级地理键为 `country:IS:national`。完整来源事实均保留在来源序列表
`disease_series_observations`；`disease_records` 只保留向旧接口兼容的安全投影，不能作为来源全量性的核对依据。

| 来源系统 | 数据含义 | 原始粒度 | 序列 | 已入库观测 | 覆盖期 |
| --- | --- | --- | ---: | ---: | --- |
| `SRC_IS_DOH_ANNUAL` | 当前国家级年度通知/诊断仪表板 | annual | 14 | 209 | 2010-01-01—2025-01-01 |
| `SRC_IS_DOH_STI` | 当前 STI 月度诊断，官方按季度发布 | monthly | 3 | 372 | 2016-01-01—2026-06-01 |
| `SRC_IS_DOH_RESPIRATORY` | 当前呼吸道疾病 ISO 周诊断 | weekly | 5 | 1,581 | 2019-07-01—2026-05-04 |
| `SRC_IS_DOH_HISTORY` | 历史国家登记年度通知 | annual | 59 | 1,144 | 1997-01-01—2021-01-01 |
| `SRC_IS_DOH_HISTORY` | 14 个疾病专项历史月度通知 | monthly | 14 | 3,225 | 1997-01-01—2021-12-01 |
| `SRC_IS_DOH_LEGACY_ICD` | Saga EHR 旧 ICD 月度登记诊断 | monthly | 30 | 2,865 | 1997-01-01—2020-12-01 |
| **合计** |  | annual/monthly/weekly | **125** | **9,396** | **1997-01-01—2026-06-01** |

125 条来源序列映射到 68 个标准疾病目标。来源序列数与疾病目标数不能互换：同一目标可保留多个
粒度、病例定义或报告基础不同的来源序列。

当前 Power BI 原站最近刷新时间：年度 `2026-06-18T14:00:43.403`、STI
`2026-07-01T10:18:59.567`、呼吸道 `2026-05-11T08:23:13.403`。这些是来源系统刷新时间，
不是本项目抓取时间。本次现行源实际抓取时间为 `2026-08-07T16:11:30.771653Z`。

## 2. 原始资料与可重放文件

- 当前 Power BI：`data/raw/is/is_doh_annual/`、`data/raw/is/is_doh_sti/`、
  `data/raw/is/is_doh_respiratory/`。每次抓取按 UTC 时间建版本目录，保存报告 HTML、
  conceptual schema、查询请求/响应、模型元数据和 SHA-256 manifest。
- 历史资料：`data/raw/is/history/`，共 22 个官方 Excel 工作簿，并有
  `raw_manifest.json` 记录来源 URL、文件大小和 SHA-256。新 manifest 使用相对路径；处理器仍兼容旧绝对路径，
  文件移动后也会按 `manifest` 所在目录和文件名回退查找。
- 当前标准化快照：`data/current/is/iceland_doh_current.csv`，2,162 行（不含表头）。
- 历史审核后来源事实：`data/current/is/history/series_rows.csv`，7,234 行。
- 历史兼容投影候选：`data/current/is/history/rows.csv`，3,711 行；它不是完整来源事实。
- 隔离明细：`data/current/is/history/quarantine.csv`，5,340 行。
- 解析审计：`data/current/is/history/manifest.json`。
- 本地 schema-v4 审核包：`exports/site-downloads/manifest.json` 与
  `exports/site-downloads/countries/is/`；共 12,411 行，其中 9,396 行为完整来源观测，
  3,015 行为公开兼容投影。
- 本地页面快照：`astro-site/src/data/countries/is.json`；保留 125 条来源序列及 9,396 个来源值。

## 3. 审核与隔离口径

历史工作簿中 7,234 条已经过明确 source-series → ontology concept 审核并入库：

- 历史年度通知 1,144 条；
- 疾病专项月度通知 3,225 条；
- 旧 ICD 月度登记诊断 2,865 条。

另有 5,340 条候选观测保留在 quarantine，未伪装成标准疾病事实：

- 50 条未审核年度观测，来自 4 个原始身份；
- 5,290 条未审核旧 ICD 月度观测，来自 98 个原始身份。

典型隔离原因包括宽泛呼吸道/耳鼻喉综合征、异型分枝杆菌、腺病毒、皮肤寄生虫等。
这些类别的病例定义与现有标准概念不完全等价。原始文件及逐行隔离原因均已保留，后续可逐类审核，
但在通过审核前不进入公开曲线或标准疾病汇总。

## 4. 数据语义与冲突规则

1. 年度、月度、周度值均保持原始报告期间，不换算成“等价周病例”，也不跨粒度补点。
2. 多个来源序列映射到同一疾病时保持独立；只有显式声明 `sum_disjoint` 的序列才允许求和。
3. 只有语义安全的计数序列才可生成默认兼容曲线。`broader`、`related`、`aggregate`、`ambiguous`、
   `unmapped` 均不写入通用 `cases`；多条 `narrower + non_additive` 且无官方汇总序列时只展示来源事实，
   不自动选择代表曲线。存在 `exact` 时优先选择 `exact`。
4. `registered_diagnoses` 是旧 EHR 就诊登记量，不等于国家法定传染病通知数，不写入 `cases` 兼容投影。
   本地下载包仍以 `value` 保存全部 2,865 条原始量，`cases` 列保持空值。
5. 同一疾病/年度重叠时，当前年度仪表板优先于历史年度工作簿，且这两类年度数据的导入先后顺序不影响结果；
   历史导入本次写入 3,688 条，并因当前数据优先跳过 23 条。
6. 跨粒度保护：同一 `disease/date` 已有历史月度通知时，当前年度值不会覆盖它。本次 209 条当前年度来源
   观测中有 103 条进入兼容层，106 条因该保护只保留在来源序列表。
7. Excel 中空白保持 unknown；疾病专项月表和旧 ICD 表的发布符号 `-` 按各自表格规则保留为明确零值；
   年度表中的不适用 `-` 不生成观测。

兼容表最终为 3,791 条（当前年度 103、历史年度 763、历史月度 2,925），其中未注册序列、疾病目标不一致、
不安全映射关系、旧 ICD 登记诊断、竞争性窄定义、空值和负值均为 0。

### 4.1 本轮疾病映射重点修正

| 来源定义 | 标准目标 | 关系/可比性 | 默认曲线处理 |
| --- | --- | --- | --- |
| Influenza A(H1N1)pdm09 | `D038 Influenza` | `narrower / conditional` | 不再误映射为 `D016 Novel influenza A`；与 H3 分开保留 |
| Influenza A H3 | `D038 Influenza` | `narrower / conditional` | 与 H1N1 均不可相加冒充通用流感 |
| Hib 与 invasive H. influenzae | `D100` | 两条均为 `narrower / non_additive` | `D100` 只显示来源观测，不生成单一代表曲线 |
| ESBL/AmpC 年度汇总 | `D237` | `exact / not_comparable / reported_aggregate` | 可作为来源定义下的汇总候选 |
| ESBL/AmpC 历史月度 | `D237` | `narrower / not_comparable` | 不与年度汇总相加 |
| 合并型 Hepatitis B 历史序列 | `D008` | `related / not_comparable` | 不写入通用病例兼容层 |
| Cholera 与 cholera-like infections | `D002` | `broader / not_comparable` | 仅保留来源观测 |
| `J02.0/J03.0` 咽炎/扁桃体炎 | `D224` | `related / not_comparable` | 不假定来源已证明 Group A |
| 旧 ICD 确诊流感子集 | `D038` | `narrower / not_comparable` | 保持登记诊断口径，不投影为病例通知 |

呼吸道周报也已按来源真实含义拆分：COVID-19、流感和 RSV 为实验室诊断，百日咳为来源报告诊断，
Mycoplasma 为临床诊断；人口范围统一标为 `national_reporting_catchment`，不再暗示全国居民完整通知。
周日期中的 `Year` 采用日历年，`ISOYear` 单独保存，因此跨年 ISO 第 1 周不会被错标。

## 5. 自动更新流水线

| Job ID | Source | 调度 | 处理策略 |
| --- | --- | --- | --- |
| `is-doh-live-daily` | `all` | 每日 06:30，`Atlantic/Reykjavik` | 当前年度、STI 月度、呼吸道周度；保存原始响应并 upsert 修订值 |
| `is-doh-history-monthly` | `is_doh_history` | 每 30 天 | 检查历史登记及疾病月表是否有官方修订 |
| `is-doh-legacy-icd-quarterly` | `is_doh_legacy_icd` | 每 90 天 | 检查旧 ICD 工作簿；始终保持独立 reporting basis |

三个任务均启用 `process=true`、`save_raw=true`、`fill_missing=false`。缺失值不会被自动补成零。

## 6. 可视化验收要点

- 国家页显示来源实际频率 `ANNUAL / MONTHLY / WEEKLY`，而不是统一标成周度。
- 存在多条来源序列时可选择具体 `series_code`，并显示来源、报告基础、频率、有效状态和覆盖期。
- 默认疾病曲线不拼接不同粒度或不同 reporting basis 的点。
- 年度/季度序列不进入月度季节性热图，避免把 1 月 1 日存储日期误当作季节峰值。
- 仅有 `registered_diagnoses` 的疾病允许查看原始来源序列，但不生成误导性的公开病例投影。
- 首页沿用中国、美国等国家原有的统一信息层级，不另设 Iceland 方法论或来源序列登记区；
  具体序列仅在曲线的按需选择控件中出现，详细审核数字留在本文档。
- 终审材料明确记录“7,234 条已审核历史观测 / 5,340 条隔离候选”，隔离项不计入可视化数据。
- 下载 schema v4 区分 `public_projection` 与 `source_series_observation`，并携带完整来源字段。

控制面板已按 series-first 接入，并将完整来源事实与兼容投影分开展示：

- 来源流：`http://localhost:3001/sources/flow`，显示 5 个来源系统、125 条序列、9,396 条观测、
  68 个疾病目标及逐序列映射审核表；
- 自动化：`http://localhost:3001/sources/automation`，显示 3 个 Iceland job；
- coverage end、source last refresh、retrieved at、pipeline last run 分栏展示，历史源不会借用当前任务时间伪造 freshness；
- history/legacy 禁止 `start_year`，Iceland 全来源禁止 `fill_missing=true`，API 在写入前返回 400；
- 旧 ICD 明确标为 `registered diagnoses (not case notifications)`。

## 7. 本地终审命令

以下命令均不执行外部发布：

```bash
# Registry 只读计划
PYTHONPATH=. venv/bin/python scripts/sync_iceland_registry.py

# 从已下载的 22 个工作簿离线重放并重建审核文件，不写数据库
# 注意：会覆盖本地 rows.csv、series_rows.csv、quarantine.csv 和 manifest.json
PYTHONPATH=. venv/bin/python scripts/import_iceland_history.py \
  --no-download --raw-manifest data/raw/is/history/raw_manifest.json --dry-run

# 查看调度配置差异，不写数据库
PYTHONPATH=. venv/bin/python scripts/configure_iceland_automation.py

# Iceland 与发布数据相关回归
PYTHONPATH=. venv/bin/pytest -q \
  tests/test_is_doh.py \
  tests/unit/test_iceland_crawl_pipeline.py \
  tests/unit/test_iceland_history.py \
  tests/unit/test_sync_iceland_registry.py \
  tests/unit/test_audit_iceland_integration.py \
  tests/unit/test_generate_site_series_first.py \
  tests/unit/test_direct_download_files.py \
  tests/unit/test_site_data_queries.py \
  tests/unit/test_site_data_views.py

# 生成本地站点 JSON 与下载分片；会在本地初始化数据库 schema，并确保国家、scope 和 WPP population
# 不会推送下载仓库，也不会部署站点
PYTHONPATH=. venv/bin/python scripts/generate_site_data.py

# 本地前端检查
cd astro-site
npm run test:charts
npm run check
npm run build

# Dashboard 静态检查
cd ../dashboard
npm run lint
```

终审计数门槛：数据库和本地 schema-v4 国家下载包均应为 `125 source series / 9,396 source observations`；
若任一侧不一致，不应进入发布流程。可运行 `PYTHONPATH=. venv/bin/python scripts/audit_iceland_integration.py`
一次性执行只读计数、来源拆分、原始文件哈希、隔离清单、自动任务和本地下载包核验。

## 8. 主要实现位置

- 当前 Power BI 抓取：`src/data/crawlers/is.py`、`src/data/crawlers/powerbi_public.py`
- 当前数据处理：`src/data/processors/is.py`
- 历史 Excel 抓取/解析：`src/data/crawlers/is_history.py`、`src/data/processors/is_history.py`
- 流水线编排：`src/services/crawl_pipelines/is_.py`
- Iceland registry 同步：`scripts/sync_iceland_registry.py`
- 历史导入：`scripts/import_iceland_history.py`
- 自动任务：`scripts/configure_iceland_automation.py`
- 只读终审核验：`scripts/audit_iceland_integration.py`
- 映射与 ontology：`configs/mapping/is.csv`、`configs/disease_ontology.json`
- 国家与来源配置：`configs/country_bootstrap.json`、`configs/reporting_sources.yml`
