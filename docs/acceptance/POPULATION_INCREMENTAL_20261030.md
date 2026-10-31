# 人口数据增量更新验收：2026-10-30

本轮按用户“迁移已有数据、优先增量、不需要全量采集”的最新要求完成。验证只使用隔离 SQLite 数据库和本地测试输入，未请求实际来源、未执行 production apply、未触发实际发布或部署。

## 变更文件

- `scripts/import_subdivision_population.py`：提供共用 `store_population_changes`，官方人口写入只提交实际变化，并返回 `changed_country_codes`。
- `scripts/import_wpp_population.py`：两个显式 WPP 导入入口复用上述事务逻辑，保留官方来源优先级，删除运行时手写建表路径。
- `scripts/refresh_official_population.py`：报告实际变化范围；`--export` 入队现有 `site-release`，不再传入 `full=True`。

新增验证文件为 `tests/unit/test_population_incremental_store.py`；更新 `tests/unit/test_import_wpp_population.py` 和 `tests/unit/test_official_population_refresh.py`。源码及测试已冻结，本记录不扩展其他入口。

## 实际增量与 checksum 语义

按地区和年份比较已有记录。只有人口数、来源或官方分母的语义 metadata 变化才写入正式行并更新时间戳。官方语义 metadata 包括参照日期、人口口径、来源 URL、地区身份和同年分母政策。相同记录不会执行更新，也不会创建新的审计任务或 dirty。

整份输入文件的 checksum、WPP snapshot 文件名等属于运行 provenance，不是人口分母变化。文件中某一个地区变化导致整体 hash 改变时，其他相同地区和年份保持原行、原时间戳与原 checksum。原行 checksum 表示最后一次改变该行语义的输入；当前输入 checksum 仍保留在 CLI 报告或返回结果中，有正式变化时也写入真实审计任务。仅改文件名或 checksum 不会触发全部曲线重算。

实际变化的地区以 `surveillance:<country_code>` 写入 `publish_dirty`，与人口正式行及审计记录使用同一数据库事务。发布仍通过统一 dirty 消费流程进行；入队不提前确认 dirty。没有新增 CLI 协议或 dirty schema。父国范围扩展到已知子地区仍遵循既有发布规则，保留保守扩展，不另行区分人口维护来源。

## WPP 官方来源优先级

`import_rows` 和 `ensure_wpp_population` 都不覆写任何非 WPP 来源的已有分母；NBS、ABS、IBGE 分别有真实数据库测试覆盖。WPP 可新增缺失年份或更新已有 WPP 人口实际值。相同人口的 snapshot 文件名或 checksum 更换不会更新正式行。

这两个入口只执行显式导入。人口表必须由正常数据库迁移提供；缺少 `population_records` 时明确失败，不在运行中创建表或索引。

## 真实手动审计与阶段含义

每次有正式变化时，共用事务创建一个真实、已完成的维护 Task，并登记三个阶段：

| 阶段 | 状态 | 含义 |
| --- | --- | --- |
| collect | skipped | 此维护写入阶段消费已有输入，没有执行新的来源抓取 |
| organize | skipped | 本次审计记录不声称重新执行来源整理流水线 |
| store | completed | 实际人口变化和发布 dirty 在调用者事务中提交 |

这三条记录不是历史采集阶段的回填，没有伪造历史原始文件或下载证据。未变化的输入不创建这样的维护 Task。

## 隔离数据库证据

测试使用真实 SQLAlchemy AsyncSession、SQLite 表、正式 PopulationRecord 模型及 Task、RunStage、PublishDirty 模型；没有用 mock 核心 SQL 代替写入验证。

- 幂等：重复导入相同值，以及仅改变整体 checksum 或 snapshot 文件名，正式行和时间戳均不变，不增加 Task、阶段或 dirty。
- 精确范围：输入同时包含未变化的 `CN-GD` 与发生变化的 `CN-ZJ`，只更新 `CN-ZJ`，只登记该地区的 dirty；其他行连时间戳和原 checksum 都保持不变。
- 原子回滚：在真实 `flush` 已执行之后注入 dirty 登记失败，人口更新或新增年份、审计 Task、三个阶段均回滚。官方与 WPP 两个导入入口都有相应覆盖。
- 官方优先：WPP 分别面对 NBS、ABS、IBGE 已有记录时，保持人口、来源、metadata 和时间戳不变。
- schema 缺失：删除隔离数据库人口表后导入失败，验证表没有被运行时重新创建。
- 发布参数：调用实际 release 入队方法并在隔离数据库创建 EXPORT_DATA Task。只有 `CN-ZJ` 分母变化时，任务参数为 `incremental_country_codes=["CN-ZJ"]`、`incremental_site=true`、`full=false`，对应 dirty 仍未确认。该测试没有执行 worker、Astro 构建或部署。
- CLI：`refresh_official_population.py --export` 调用现有 `site-release`，不带 `full=True`；变化范围进入本地报告。CLI 测试使用本地来源替身，核心人口事务由上述真实数据库测试验证。

## 验证命令与结果

最终命令：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_import_wpp_population.py \
  tests/unit/test_population_incremental_store.py \
  tests/unit/test_subdivision_population.py \
  tests/unit/test_official_population_refresh.py \
  tests/unit/test_manual_ingestion_runs.py \
  tests/unit/test_publish_dirty.py \
  --tb=short
```

结果：**51 passed in 4.44s**。其中前四个人口测试文件的组合此前为 **42 passed**。

三个脚本的 `compileall` 和本轮文件的 `git diff --check` 均通过。源码检查中不再存在这些入口的 `CREATE TABLE`、`CREATE INDEX`、`ensure_table` 或 `full=True` 路径。

本记录证明人口增量写入与发布范围的离线行为，不声称已进行实际来源采集、生产数据库应用、生产发布或部署。
