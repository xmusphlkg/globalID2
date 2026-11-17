# 墨西哥 DGE 月报与周报接入核验（2026-11-17）

墨西哥存在独立的官方月报和周报。网站此前仅接年度分布表，不能据此认定上游没有月／周数据。新增 `mx_dge_monthly` / `SRC_MX_DGE_MONTHLY` 与 `mx_dge_weekly` / `SRC_MX_DGE_WEEKLY`，保留各自分类、统计周期和病例定义。

## 已实采并可离线重放

| 来源 | 原始文件与实际数值覆盖 | 结果 |
|---|---|---|
| [DGE 全国年度目录](https://epidemiologia.salud.gob.mx/anuario/html/morbilidad_nacional.html) 下的按月新增病例分布表 | 动态发现 69 个年份／性别版本；本次实际捕获 **2025 general / female / male 三份 PDF** | 6,648 条月观测：general 2,256，female 2,232，male 2,160；最新月锚 2025-12-01。female 一行缺少可安全识别的病名／ICD，隔离并记录；不宣称全部历史版本均已入库。 |
| [DGE 2026 周报目录](https://www.gob.mx/salud/documentos/boletinepidemiologico-sistema-nacional-de-vigilancia-epidemiologica-sistema-unico-de-informacion-417103) | 冻结目录实际链接及其原 PDF；本次捕获第 43 和第 44 期 | 第 43 期 607 条全国观测，69 张可识别表，8 张特殊布局被隔离。第 44 期以冻结的双尺度西语 OCR 证据补两张全国汇总表标题，154 条观测；其他 75 张表标题不可读，明确 **parser_blocked**。合计 761 条来源观测，最新源锚 2026-11-01；没有推定未读取表的数值周期。 |

2026 第 43 期封面为 2026-10-25 至 2026-10-31。其特殊确认监测总表按第 43 周统计，例如脑膜结核 8 例、累计 465；常规监测总表按第 42 周（2026-10-18 至 24 日）统计，例如水痘 1,014 例、累计 46,571。两者不得按封面统一重标为第 43 周。目录在本次核验过程中新增第 44 期（2026-11-01 至 7 日），不能把第 43 期观测冒称最新第 44 期。第 44 期 Cuadro 1 标题双尺度 OCR 均明确第 44 周，Cuadro 2 均明确第 43 周；两张表的数字均直接来自原 PDF 文本层。原文第 44 期未检出明确的破折号零值图例，因此其破折号保留未知。

月表可逐行核对 12 个月之和与原文年度总数，例如 2025 流感 14,638 例，首两个月 3,182／2,672。月表内的 `§` 估计公式行独立标识为估计病例，不能发布为直接报告新增病例。

## 数据合同

- 原始目录、PDF、冻结采集计划均保存 SHA-256 和字节数。离线组织重新从冻结目录发现引用，核对计划、PDF 清单和字节完整性；损坏直接报错。
- 月表严格要求完整月份、年度总计及全国／年份／性别标题。完整数值行必须与原文年度总计对账。上游缺失值保持未知；数值 `0` 保持明确零。
- 周报使用原文明确全国总计或原文全国汇总行，**不加总州数据**。只解析当年本周／累计列，排除上年对照和历史中位数。原文图例 `(-): no se presentaron casos` 验证后，周报破折号才表示零；月表破折号仍保持缺失。
- 每张周表拥有自己的截止周，日期由原文明示的周日—周六封面及表内截止周导出。标题图形只在两次不同尺寸渲染的 OCR 全标题一致、显式周／年份与原文封面交叉校验通过后使用；冻结 OCR 原文、引擎版本、裁切范围、两张标题图片哈希，并将其作为独立 artifact 纳入采集清单与计划完整性校验。数字和病名不使用 OCR。累计观测的累计起点未知，`PeriodStart` 留空，报告周日期另存，不将累计值解释为当周新增。
- 累计、男女分层、非传染病与未映射分类全部保留在来源观测账本。只有已精确复核的全国、全年龄、全性别、单期新增计数才能参与标准病种病例投影；不得将年／月／周或不同报告系统相加。
- 页码、年总计、报告期号、脚注与验证结果进入 `SourceProvenance`，不参与事实维度身份；同事实重印合并，冲突数值隔离。修订的页码与年总计不会生成新的事实身份。
- 本轮操作员授权来源序列公开，标准病例投影仍由精确映射及 `reviewed_mx_high_frequency_record` 校验。此模块自身不向数据库写入。

## 可审阅产物与运行方法

独立注册补丁为 `configs/high_frequency_mexico_ontology.json`，映射补丁为 `configs/high_frequency_mexico_reviewed_mappings.json`，生成器 `scripts/prepare_mx_high_frequency_registry.py`。原有已审核当前 ICD-10 同名精确映射只有约 20 类。补充 `configs/mx_high_frequency_concept_review.json` 逐条审核西语病名、原文 ICD 与现有概念定义，月、周各 48 个来源类别具备病例投影条件。明确的子型保留 `narrower` 关系且不自动汇总。原文周报黄热病／委内瑞拉马脑炎的 ICD 互换冲突排除自动病种映射；不修改原文编码。其他观测保持来源原生数据。

真实采集清单（供统一导入服务导入）为：

- `data/raw/mx/high-frequency-monthly-20261117/artifacts_manifest.json`
- `data/raw/mx/high-frequency-weekly-ocr-20261117/artifacts_manifest.json`

本地采集与离线重放均不写数据库：

```bash
venv/bin/python scripts/import_mx_high_frequency.py --source mx_dge_monthly --start-year 2025 --end-year 2025 --directory data/raw/mx/high-frequency-monthly-20261117
venv/bin/python scripts/import_mx_high_frequency.py --source mx_dge_weekly --start-year 2026 --end-year 2026 --latest-reports 2 --weekly-index-url https://www.gob.mx/salud/documentos/boletinepidemiologico-sistema-nacional-de-vigilancia-epidemiologica-sistema-unico-de-informacion-417103 --directory data/raw/mx/high-frequency-weekly-ocr-20261117
venv/bin/python scripts/import_mx_high_frequency.py --source mx_dge_weekly --directory data/raw/mx/high-frequency-weekly-ocr-20261117 --replay
```

去掉 `--latest-reports` 会采集所请求年份实际链接的全部周报；三份月表和两期周报是本次已核验的实际覆盖，不能代表所有链接历史报告均已解析。重放只读取清单、原文件与冻结计划。版本控制内的真实原文回归 fixtures 位于 `tests/fixtures/ingestion/MX/high_frequency`，包含逐文件来源 URL、SHA-256 与大小收据。

已知数值缺口：2025 female 月表 1 行，第 43 期特殊表页 31、32、45、57、62、66、67、68，以及第 44 期其余 75 张表的标题图形。第 44 期两张已核验汇总表内的破折号仍是未知，不能依据前期图例补零。未来应针对真实原件补充标题 OCR／特殊布局合同，不能因解析失败补零或推定病例定义。
