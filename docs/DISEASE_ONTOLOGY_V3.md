# 疾病本体与来源系列系统 v3

## 1. 结论

疾病数据不能只靠“嵌套标签”治理。推荐模型是：

1. 稳定的标准疾病概念；
2. 可组合的多轴 facet 标签；
3. 具有版本和口径的来源监测系列；
4. 显式的概念关系与禁止默认汇总规则；
5. 来源可用性与缺失原因；
6. 保留来源系列自然键的事实表；
7. 只为旧调用方保留的扁平兼容投影。

核心原则是：**概念、来源口径、统计事实三者分离**。相同疾病可以有多个来源系列；
名称相似的系列也不一定是同一个疾病概念。

当前注册表为 `configs/disease_ontology.json`，版本 `2026.08.2`。

## 2. 为什么旧模型会出错

旧模型将以下信息压缩到一个 `disease_id`：

- 病原体或病因；
- 临床阶段、急慢性和严重程度；
- 确诊、疑似、暴露、死亡等事件类型；
- 成人、儿童、孕产妇等人群；
- 来源机构、来源代码和病例定义版本；
- 周、月、年以及通知日、诊断日等时间口径。

压缩后会出现三类不可逆问题：

- HIV infection 与 AIDS 被当作同义词，或重复计算；
- 病毒性肝炎总类、病毒类型、急慢性和确诊/疑似被混为一谈；
- 同一个来源代码下的多个独立系列在旧事实自然键中互相覆盖。

因此，`standard_diseases.csv` 仍提供稳定 ID，但不能独自表达完整监测语义。

## 3. 六层数据模型

### 3.1 标准概念层

概念 ID 一经发布保持稳定。概念可以有多语言标签、历史别名、定义和 facet，
但来源名称不能直接改变概念身份。

示例：

- `D162`：HIV infection；
- `D005`：AIDS，即 HIV 的晚期临床阶段；
- `D230`：儿童 HIV 暴露通知，不是 HIV 感染诊断；
- `D231`：孕产期 HIV 感染监测事件。

### 3.2 Facet 多轴标签层

Facet 是可组合的属性，不是另一套互斥疾病 ID。当前轴包括病因、临床病程、
人群、事件依据、监测范围、病例状态等。

例如 `D005` 同时具有：

```text
etiology.hiv
clinical_course.aids
population.all
surveillance_scope.condition_specific
```

这比固定树更适合疾病数据，因为同一概念往往同时属于多条分类轴。

### 3.3 导航分组层

Group 用于浏览、筛选和表达来源聚合口径，不默认代表可计算总数。

肝炎目前至少分为：

- `G_VIRAL_HEPATITIS`：病毒性肝炎家族；
- `G_HEPATITIS_BY_VIRUS`：按 A、B、C、D、E 或未特指病毒；
- `G_HEPATITIS_BY_COURSE`：按急性、慢性、围产期或未特指病程；
- `G_HBV_SPECTRUM`、`G_HCV_SPECTRUM`：病毒与病程交叉视图。

所有这些组默认 `no_auto_rollup`。只有满足病例集合互斥、时间口径一致、
定义版本兼容等条件时，才允许声明可加总关系。

### 3.4 来源系列注册表

每条来源监测系列拥有稳定 `series_id`，并记录：

- `source_id`、国家、来源代码和本地标签；
- 精确概念或来源聚合组；
- 频率、指标、单位和 reporting basis；
- 病例定义及定义版本；
- 有效期、缺失策略、可比性和汇总策略；
- facet 补充信息。

映射 CSV 中的 `source_id` 和 `series_id` 是强绑定，不应再靠模糊名称推断。

### 3.5 来源可用性层

“没有记录”不等于 0。每个来源目标必须区分：

- `available`：来源系列已注册且事实已写入；
- `not_reported_by_source`：该来源本身不报告此系列；
- `upstream_available_ingestion_pending`：上游有数据，但尚未回填；
- `parser_blocked`、`mapping_missing`、`not_assessed` 等其他状态。

缺失值默认采用 `missing_is_unknown`；只有来源明确给出 0 时才保存 0。

### 3.6 精确事实层

`disease_series_observations` 的自然键为：

```text
(time, series_code, geography_key, dimension_key)
```

确诊/疑似、HIV/AIDS、成人/儿童或不同病例定义不会因为共享标准疾病 ID 而互相覆盖。
原始行和来源元数据同时保存，支持追溯与重新投影。

`geography_key` 表示的不是一个随意的展示标签，而是统计总体（population scope）的一部分。
国家级居民总体、来源自行发布的跨地域合计、州/省和其他地区必须使用不同键。以美国
NNDSS 为例：

| 来源 Reporting Area | `geography_key` | 语义 |
| --- | --- | --- |
| `US RESIDENTS` / `U.S. Residents` | `country:US:national` | 美国居民国家级总体，不含领地居民和非美国居民 |
| `TOTAL` | `source:SRC_US_NNDSS:reporting-area:total` | 来源发布的更宽合计，包含美国领地和非美国居民 |

因此，即使两行具有相同疾病、周和病例状态，也不能互相覆盖或互为回退值。

## 4. HIV/AIDS 的处理规则

- HIV infection 和 AIDS 是有关联但不等价的概念；关系为 `clinical_stage_of`，
  默认不自动相加。
- 美国 NNDSS 周报不报告 AIDS/HIV 诊断系列时，必须标记来源不报告，不能展示为 0。
- 美国 NHSS 是独立来源，HIV 和 AIDS 各自拥有年度系列。
- 美国 NNDSS 的国家级展示只使用居民口径；`TOTAL` 必须保留为独立来源聚合范围，
  不得在居民数据缺失时回退到 `TOTAL`。
- 巴西 AIDA、AIDC、HIVA、HIVC、HIVE、HIVG 分别保留为六条来源系列；
  HIVE 是儿童暴露，HIVG 是孕产期事件，不能投影成普通 HIV 感染。
- 台湾 042 和 044 显式绑定 AIDS/HIV 系列，不再依赖名称匹配。

## 5. 肝炎的处理规则

- `D006` 只表示来源明确报告的未细分病毒性肝炎或兼容视图，不能作为所有肝炎事实的自动总计。
- 病毒类型与病程是两条独立分类轴。例如急性乙肝、慢性乙肝、围产期乙肝均有独立概念。
- confirmed、probable 等病例状态作为独立来源系列保留，即使它们映射到同一概念。
- “病毒性肝炎（排除 A/E）”等来源口径应指向 group，而不是强行等同 `D006`。
- 聚合系列与子系列并存时，查询层必须选择一种统计口径，禁止重复相加。

## 6. API

主要读取接口：

```text
GET /diseases/disease-ontology
GET /diseases/disease-ontology/facets
GET /diseases/disease-ontology/concepts/{disease_code}
GET /diseases/disease-ontology/series
GET /diseases/disease-ontology/availability
GET /diseases/disease-ontology/series/{series_code}/observations
```

新分析和前端应优先读取系列事实接口。旧 `disease_records` 仅作为迁移期兼容数据，
不得用于区分病例状态或来源口径。

## 7. 修改和发布流程

1. 在 `standard_diseases.csv` 添加真正的新概念；不要为拼写变体创建概念。
2. 在 ontology 中补充概念、facet、group 和关系。
3. 为来源实际发布的每个口径创建独立 `series_id`。
4. 在国家 mapping CSV 中显式写入 `source_id` 和 `series_id`。
5. 声明 availability；未知与 0 必须分开。
6. 每个既有映射目标变化必须先登记到
   `configs/disease_mapping_transitions.csv`，并选择以下动作之一：

   - `remap_legacy`：单一 raw component 可用精确字段和值安全迁移；
   - `remap_and_reingest`：可迁移幸存分量，但还必须从来源重建被旧自然键覆盖的分量；
   - `source_reingest`：legacy 已丢失来源语义或把 missing 压成 0，只允许从来源重建。

7. 默认命令执行真实数据库 preflight；它不会写入，但会检查目标变化、命中数量、
   病例总量、零值数、时间范围、raw evidence 纯度和目标键碰撞。完整 SQL 演练使用
   `--rehearse`，事务结束时强制回滚：

```bash
PYTHONPATH=. venv/bin/python scripts/sync_disease_ontology.py
PYTHONPATH=. venv/bin/python scripts/sync_disease_ontology.py --rehearse
PYTHONPATH=. venv/bin/python scripts/sync_disease_ontology.py --apply
```

`--offline` 只输出配置计划，不能作为数据库迁移验证。

8. `--apply` 会在同一事务内协调 Schema、目录、source-aware mapping、ontology
   权威状态和事实迁移。每条事实写入前都会记录到 `disease_migration_audit`；恢复前先预览：

```bash
PYTHONPATH=. venv/bin/python scripts/restore_disease_migration.py --list
PYTHONPATH=. venv/bin/python scripts/restore_disease_migration.py --migration-key '<key>'
PYTHONPATH=. venv/bin/python scripts/restore_disease_migration.py \
  --migration-key '<key>' --run-id '<exact-run-id>' --apply
```

正式恢复强制指定一个精确 `run-id`，禁止同名 migration key 跨批次批量恢复。

9. 从来源回填精确事实，并核对自然键、时间范围、总量、missing/zero 和所有 skip 计数。
   美国 NNDSS 居民范围使用专用脚本；默认只预检，正式写入必须显式给出输入和日期边界：

```bash
PYTHONPATH=. venv/bin/python scripts/backfill_us_nndss_resident_series.py \
  --input data/history/us/NNDSS_Weekly_Data_20260317.csv \
  --from-date 2022-01-08 --to-date 2026-03-07

PYTHONPATH=. venv/bin/python scripts/backfill_us_nndss_resident_series.py --apply \
  --input data/history/us/NNDSS_Weekly_Data_20260317.csv \
  --from-date 2022-01-08 --to-date 2026-03-07
```

   回填只接受两个居民别名；空值被省略，非法值、同键异值、非居民 provenance 或
   Registry 不完整都会阻断事务。重复执行时同键同值为 no-op。

10. 运行映射质量门：

```bash
PYTHONPATH=. venv/bin/python scripts/audit_disease_mappings.py --fail-on-error
```

11. 运行测试并重新生成静态数据。series-first 投影按 period 覆盖；Registry
    缺失期只能显式标为 `legacy_gap_fill`，不得隐藏较长的 legacy 历史。

## 8. 强制不变量

- 一个 active series 必须恰好指向一个 concept 或 group；
- 同一个 `series_id` 不得跨来源或跨目标复用；
- available 系列必须已经同步注册表并拥有事实；
- 来源代码有歧义时必须通过显式 `series_id` 消歧；
- 不允许把缺失月填成 0；
- 不允许把 aggregate 与 child 默认相加；
- deprecated series 只为兼容保留，不接收新事实；
- Registry-required 双写中 unmatched、ambiguous、registry-not-synced 或零有效观察必须 fail closed；
- 较低质量状态不能覆盖 final/revised，正值变 0 或大幅回撤需要显式权威修订；
- source-aware mapping 的自然键必须包含 `source_id`，精确来源优先于 wildcard；
- `geography_key` 必须与原始地理证据一致；未知 Reporting Area、显式键冲突或将
  来源 aggregate 写入国家居民键都必须 fail closed；
- Registry 部分覆盖时只能选择已注册且非空的来源行；已注册行解析失败、缺失必要范围
  或双写结果为零必须阻断，不能退回模糊名称匹配；
- 实时 legacy/series 双写、系列 Store、回填、ontology sync 和恢复必须在首次事实写入前
  获取同一个事务级 disease mutation lock；禁止各脚本自行定义互不相同的锁；
- 语义修复必须可审计、可重复运行，并保存可执行恢复的 before-image。

## 9. 美国 NNDSS 范围迁移与恢复

本次迁移先将带有精确原始证据 `ReportingArea=TOTAL` 的 1,319 条观测从
`country:US:national` 移至来源 aggregate 键，再从 CDC 历史文件回填 1,736 条
U.S.-resident 观测。两个动作分别保存完整 before/after image，并可独立预检恢复。

恢复存在明确依赖顺序：如果两个动作都要撤销，必须先恢复居民回填（删除仍与
after-image 完全一致的 1,736 条插入），再恢复 geography remap（把 1,319 条
`TOTAL` 放回旧键）。反向执行会因旧自然键已被居民事实占用而安全阻断。

```bash
# 1. 先预检、再删除居民回填
PYTHONPATH=. venv/bin/python scripts/restore_disease_migration.py \
  --migration-key 'series_resident_backfill:SRC_US_NNDSS:country:US:national:2022-01-08..2026-03-07' \
  --run-id 'us-nndss-resident:ec0b7214-3f2f-4c26-8241-fcd9470f732b'

# 2. 再预检、恢复旧 geography（仅用于整体回滚）
PYTHONPATH=. venv/bin/python scripts/restore_disease_migration.py \
  --migration-key 'series_geography:SRC_US_NNDSS:country:US:national->source:SRC_US_NNDSS:reporting-area:total:evidence=total' \
  --run-id 'globalid-disease-ontology:2026.08.2:aa32f40d-0085-412d-8342-b556764e9f08'
```

两个命令都只有增加 `--apply` 才会写库。恢复器会锁定目标行并校验完整 after-image；
任何上线后的合法修订都会使恢复失败，避免静默覆盖新数据。恢复预检和正式恢复还会
与实时双写、系列 Store、ontology sync 和回填任务取得同一事务级 advisory lock，
统一写锁顺序，避免恢复与生产者交错或交叉锁行死锁。

## 10. 终验快照（2026-08-04）

- 标准疾病目录：233 条；
- 已增强 ontology 概念：79 条；
- 导航组：22 个；
- 显式概念关系：46 条；
- 来源系统：11 个；
- 来源系列：126 条，其中 115 active、9 historical、2 deprecated；
- availability assertions：128 条；
- 精确系列事实：39,329 条（终验期间正常的 BR 定时采集新增 2 条）；其中美国 NNDSS
  居民范围 1,736 条、来源 `TOTAL` aggregate 1,319 条；后续正常采集会继续增加总数；
- 自然键重复：0；
- 映射审计：0 error、0 pending backfill。

旧扁平投影仍会产生可预期的有损警告。它们用于提醒调用方迁移，不应通过再次合并概念来“消除”。

## 11. 旧事实表的分阶段退役

运行策略与疾病语义分离，统一维护在 `configs/disease_cutover.json`。默认策略为
`series_with_fallback + shadow_compare + dual`；严格读取只能按
`country_code + concept_id` 单独批准，停止旧写只能按 `source_id` 单独批准。

切换顺序固定为：

1. `series_with_fallback + shadow + dual`，逐期比较两层；
2. 所有准入门满足后，目标疾病进入 `series_only + dual`；
3. 来源分区水位迁出旧表并完成观察期后，才允许 `compare_only`；
4. 再完成一个观察期后才允许 `off`；
5. 所有线上读取退出后，把旧表迁入只读 archive，经过保留期再删除。

严格准入使用统一审计：

```bash
PYTHONPATH=. venv/bin/python scripts/audit_disease_cutover.py --strict
PYTHONPATH=. venv/bin/python scripts/audit_disease_cutover.py \
  --country US --concept D162 --json
PYTHONPATH=. venv/bin/python scripts/check_legacy_disease_access.py
```

准入至少要求：required series 有事实且明确 available，national/all 粒度唯一，单位一致，
无 suppressed/rejected 被当作 0，覆盖率 100%，影子差异为 0 或经过人工批准，并且投影为
`single_series` 或每期组件完整的 `sum_disjoint`。`series_only` 路径从 SQL 层禁止访问
旧事实表；事实缺失必须暴露为 unknown，不能静默 gap-fill。

首个 canary 是美国 HIV infection（D162）：`SER_US_NHSS_HIV_ANNUAL` 在
2014–2024 的 11 个年度期间与兼容投影 11/11 对齐，影子差异为 0，且无 suppressed、
rejected 或单位冲突。该目标已进入 `series_only`，但 NHSS 仍保持原子双写以便快速回滚。
美国甲肝、急性乙肝和急性丙肝仍被覆盖缺口、非可加组件或数值差异门阻断，继续使用
显式 `legacy_gap_fill`，不得提前切换。

仓库级 legacy access ratchet 保存当前直接旧表访问的“文件 + 最大引用数”基线。新文件
直接访问或现有文件引用数增加都会使检查失败；随着 reader/checkpoint 迁移，只允许基线
单向下降。
