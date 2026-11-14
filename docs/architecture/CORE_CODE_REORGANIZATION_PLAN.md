# 核心代码拆分与目录整理方案

状态：**用户已于 2026-11-14 明确授权同时实施核心拆分，49个候选的本轮职责拆分已分批集成，统一验收与发布状态见实施记录。**

实施状态及每批验证见 [进度记录](CORE_CODE_REORGANIZATION_PROGRESS.md)。以下保留原分析时的边界与建议；历史“等待开始”描述不再是当前阻塞。

分析日期：2026-11-14（Asia/Shanghai）。用户已授权使用 subagent 做只读分析；
数据更新与优化仍由另一位 agent 处理。即使观察到提交、验收记录或工作区安静，
也不自动转入代码修改。收到用户明确开始指令后，按本方案启动并行实施，无需重复询问是否拆分。

## 1. 分析交付与范围

- 本文：目录设计、文件拆分边界、兼容约束、批次、并行所有权和验收方式。
- [逐文件清单](core-code-inventory.csv)：全部纳入范围的生产代码文件，记录职责、处理建议、风险、分析深度、内容摘要与交接复核标记。
- [现有脚本索引](../../scripts/README.md)：当前可用命令的入口，不把未来目录当作现有路径。

本次由 3 个 subagent 分析后端、数据发布、前端与基础设施，主 agent 负责 CLI/脚本及覆盖复核；文献目录另由后端 subagent 补查。

| 分析组 | 生产源码文件 | 快照行数 |
| --- | ---: | ---: |
| 核心/服务/知识/控制面与 API | 235 | 75,882 |
| 数据采集/存储/生成/发布 | 284 | 85,470 |
| 文献领域 | 58 | 16,008 |
| Dashboard/Astro/Workers/shared | 653 | 85,663 |
| 脚本与根/CLI 入口 | 157 | 38,047 |
| **合计** | **1,387** | **301,070** |

另列 **103 个受保护合同文件**（依赖/CI/systemd、迁移链、配置、生成 API 合同等），不计入上述源码数量。
源码建议分类：保留 `retain` 1016；保留兼容入口 `compatibility` 269；候选拆分 `split` 49；后续归类 `group` 7；冻结或暂缓 `defer` 46。

清单合并时间（UTC）：`2026-11-14T02:13:02+00:00`。活动工作区采用逐文件采样，不是原子快照。

CSV 的 `scope` 区分源码与受保护合同；`review_depth` 标记结构盘点或重点阅读；`analysis_sha256` 是分析报告的文件摘要，`snapshot_sha256` 是合并时摘要。
`handoff_recheck=required` 表示开始施工前必须复核；`changed_since_analysis` 表示合并时发现变化，`working_tree_change` 表示文件已有未提交修改。`target` 是建议位置，不代表目录已经创建。

候选集合来自 Git 已跟踪及未忽略的新文件，覆盖 `src/`、`scripts/`、`dashboard/`、`astro-site/`、`cloudflare/`、`shared/` 和根 `main.py` 的手写源码。
测试文件、vendor、生成/缓存目录从源码分母排除；构建配置单列为合同。`dashboard/test_query.py` 是未调用的调试片段，不属于生产入口，本轮不修改；其余三个同目录 Python 测试保留为验证证据。

“全量覆盖”指对每个纳入范围的文件完成结构盘点和归属判断；热点文件另外检查
具体函数、依赖和既有测试。它不是对所有行逐一完成业务正确性审计。
源码范围排除测试夹具、第三方 vendor、生成客户端、依赖、构建产物、运行数据和秘密配置；生成 API 合同在受保护清单中单列。
测试、CI、服务模板、迁移链与配置注册表作为兼容约束另行核对。

源码仍在变化，清单的摘要是分析时的逐文件快照，不是可直接部署的发布基线。
实施前必须以交接后的真实内容重新计算，特别是新增和未提交的发布优化文件。

## 2. 已确认的设计原则

1. **按职责和状态边界拆分。** 文件行数用于定位热点，不作为强制拆分阈值。
2. **现有入口继续可用。** `main.py`、`python scripts/<name>.py`、已使用的 `scripts.<name>` 导入、systemd 的 `ExecStart` 和构建命令均保持兼容。
3. **先复用已有模块。** `src/services/data_release/`、`src/generation/direct_download/`、`src/services/ingestion/`、`src/ontology/` 和模型中心已拆出的子模块是基础，不另建平行框架。
4. **入口只处理调用边界。** 参数、环境解析和退出码属于 CLI；纯规划、领域规则和存储/发布服务属于 `src/`。
5. **一次只改变一种边界。** 搬函数、改变 SQL/算法、升级依赖、调参和修改数据契约分别实施。此次目录整理不夹带业务策略修改。
6. **共享文件由主 agent 集成。** 多个 agent 不同时改同一门面、事务编排器、指纹清单或锁文件。
7. **小而稳定的文件保持原状。** 不为视觉整齐创建大量转发文件、空包或通用 `utils.py`。

## 3. 目录分类与依赖方向

目标是“稳定入口 + 按领域组织的实现”。以下新目录是设计目标，只有出现真实职责时才创建。

```text
scripts/
  README.md                         # 命令索引和当前路径
  <existing-command>.py/.sh         # 已被调用的稳定入口；逐步缩为适配层
  automation/                      # 保留现有 CI/自动化入口
  maintenance/                     # 历史库修复和恢复工具
  imports/                         # 一次性历史输入预处理
  runtime/lib/                     # 仅在确有复用时拆出的 shell 辅助实现
src/
  cli/
    main.py                        # 保留当前辅助 Typer 应用
    commands/
      publishing/                  # 新抽出的参数解析/服务调用适配
      imports/
      maintenance/
      diagnostics/
  ontology/                        # 本体类型、纯规划、验证及既有版本化迁移
  knowledge/                       # 知识证据、规范化和质量规则
  literature/                      # 文献采集/审核/周报，复用已有 normalization/health/review_workflow
  services/
    database_rebuild_*.py           # 扩展现有 plan/import/run 分工
    data_release/                  # 继续作为发布领域实现，不整包换目录
      git_downloads/               # 下载 Git 发布职责（必要时创建）
      raw_archive/                 # 原始归档职责（必要时创建）
    ingestion/                     # 采集阶段、来源计划与导入适配
  generation/
    direct_download/               # 扩展已有行、窗口、格式、合并、调度职责
    site_export/                   # 从站点导出中拆出的上下文与写出职责
    literature_export/             # 从文献导出中拆出的投影与写出职责
  data/
    crawlers/                      # 国家入口保持；复杂来源按协议抽辅助模块
    processors/
    storage/                       # 保留 Store 门面；抽纯质量规则和行构造
```

`src/` 的新业务模块不能反向导入 `scripts/`。现有跨脚本共享能力先抽到所属领域，
再由旧脚本显式导出兼容符号。例如 R2 环境解析目前被多个脚本从
`scripts.publish_papers_r2._config_from_env` 导入，后续应归发布配置模块；
不要顺手更改凭据优先级或创建第二套环境解析。

前端继续使用 Dashboard 的 `features/`、`shared/` 和 Astro 的领域组件／库。
页面文件负责路由和装配；复杂查询、状态、表格、图表或表单按领域抽取。
Worker 的既有入口与 bindings 保持稳定，内部按路由、协议、存储、调度职责拆分。

根目录生产脚本不会在一个批次中全部移动。短脚本以索引分类即可；只有实现被
拆出且兼容测试通过，才考虑将无外部调用者的入口移动到分类目录。

## 4. 当前交接冻结区

另一位 agent 正在修改或新建的范围包括：

- `src/services/data_release_service.py`、`data_release/pipeline.py`、`build_stage.py`、`stage_receipts.py`。
- `data_release/repository_publishers.py`、`site_research_publish.py`、`realtime_monitor.py`、`publication_execution.py`、`publication_planning.py`。
- `data_release/code_workspace.py`、`published_data.py`、`research_publication_receipt.py`、相关基线模块。
- `src/services/literature_gap_service.py` 及其测试。
- Astro 的公开数据更新组件、样式、数据读取、端到端测试和版本记录；`astro.config.mjs` 与代码工作区配置测试。
- Dashboard 发布 API、publication schema、发布规划 UI、OpenAPI 与生成客户端；周报审核配置。

冻结区不是完整的固定名单：开始时重新读取 `git status`，将后来出现的修改也纳入交接。
当前文档清理产生的移动与删除也属于已有工作，不能通过 reset/checkout 恢复。

交接记录应包含：来源提交或明确的未提交差异清单、新增文件、已通过检查、尚未解决的问题、
数据更新是否已经结束，以及需要保留的发布候选／恢复收据。生产服务仍可能持续运行，
因此源码施工在隔离工作区进行，不在运行服务读取的目录中边改边测。

## 5. 首批可并行拆分：命令实现

### A. 下载与原始归档发布

| 文件／职责 | 提取边界 | 保留的行为 |
| --- | --- | --- |
| `publish_download_repo.py` 的 `validate_source`、`_asset_relative_path`、`_manifest_assets` | 下载清单与受管路径验证 | schema、允许路径、SHA、文件集合、遗漏／多余文件判断 |
| `run_git*`、`push_branch`、`push_download_snapshot` | Git 传输与分批推送 | 重试分类、超时、分支、提交顺序和中断恢复 |
| `sync_managed_assets`、`publication_is_verified`、`record_publication` | 资产同步及发布收据 | 未变分区复用、先公开校验后记录成功、精确目标绑定 |
| `publish_raw_git_archive.py` 的文件盘点、`_atomic_copy`、`_PartWriter`、`_split_file` | 文件身份、原子复制、大文件分片 | 来源变动检测、字节一致、临时文件清理、完整原件恢复 |
| `publish_raw_archive` | 保留清晰的归档编排 | 复制失败不得提交成功；重试从一致状态继续 |

两个发布器的 Git 行为并不完全相同。第一批分别提取，不先强行合并成通用 Git 工具库。
旧 `scripts.publish_download_repo`、`scripts.publish_raw_git_archive` 必须保留现有可导入符号。

### B. 本体同步

`sync_disease_ontology.py` 目前混合迁移类型、纯计划、表同步、事实修复、审计快照与验证。

- 将 `FactRemap`、`MappingTransition`、`SeriesGeographyRemap` 及其定义放到本体同步合同模块。
- `build_plan` 当前读取本体、标准 CSV 和 mapping 目录：保留读取外壳，提取接收显式输入的纯规划；将 `_mapping_deactivation_plan` 等纯规则放到 `src/ontology/`，复用 `sync_validation.py`。
- 复用 `src/services/disease_ontology_sync_service.py` 的 payload 构造职责；数据库应用作为独立明确步骤，不重复实现 payload 构造。
- `apply_sync` 继续拥有事务、锁和前后镜像记录。不要把 commit 隐藏到新 helper 中。
- 保留 `scripts.sync_disease_ontology` 的显式导出，因为 Iceland/CN 接入脚本和既有测试使用它。
- US resident/Total、CH/CHFL、Ontario 等地理语义及 HIV/AIDS 概念关系不随拆分变化。

### C. 数据库重建

`DatabaseRebuilder` 保留为兼容入口。已有 `database_rebuild_plan.py`、
`database_rebuild_import.py`、`database_rebuild_run.py` 应扩展复用：

- 参数与交互确认：留在命令层。
- `_import_single_mapping_file`、`import_standard_diseases`：拆为输入准备和写入步骤。
- `import_history_data`：拆解析／校验、批次计划、保存点回退，保留各阶段既有提交边界。
- `_build_rebuild_stages`：保留类中的阶段装配，复用现有 run 模块执行阶段和记录 checkpoint/报告。
- `verify_results`：只读结果核对，不伪装成已提交阶段。

保留四种模式的语义、国家范围、`--yes` 行为、checkpoint 字段及部分提交记录。
重建测试只使用临时输入和隔离数据库／假会话，不对生产库执行重建。

## 6. 后续拆分顺序与具体边界

具体目标、函数与兼容约束见第 11 节；以下先说明各领域的实施顺序。

### 6.1 站点与文献导出

- `site_data_export.collect_site_export_context`：分为范围/快照准备、国家结果收集、全局摘要装配；先保持既有返回字典与字段。
- `write_site_export_artifacts`、`write_incremental_site_export_artifacts`：分离输出清单和写入，保留完成收据最后提交的顺序。
- `site_data_literature.build_surveillance_evidence`：抽证据桥接计算；`collect_literature_export` 拆查询与公开投影，`write_literature_artifacts` 管文件与分片。
- `direct_download_files.build_direct_download_files`：在已有包中抽数据集计划、变更分区选择及执行协调；保持 CSV/JSON/XLSX、排序、空值与 manifest 合同。
- 抽出函数后同步源码指纹依赖。不能因旧文件变成薄入口而漏掉真正决定输出的新模块。
  文献标题缓存的 `title_cache_identity` 当前以所在文件源码作为实现指纹，提取标题匹配逻辑时也必须覆盖新模块。

### 6.2 知识、存储与映射

- 知识服务先抽证据评估、profile 规范化、质量诊断、repair payload；`update_disease` 的任务阶段、模型调用与提交边界晚于纯函数拆分。
- Observation Store 先抽质量报告类型、零尾／修订／时间完整性规则；`save_rows` 继续拥有写锁、修订与隔离并返回 `publication_changes`；由 ingestion strategy 写 dirty markers，保持调用方事实与 marker 同事务。
- Mapping Registry 先分离只读 coverage 查询、候选规划和 reviewed-source 输入转换；审核发布与 schema 初始化不跟随首批移动。
- 国别爬虫按来源协议拆 Power BI、文件解析、历史窗口等辅助能力，保留国家门面和来源自然键，不把差异抹成大量布尔参数。
- 已拆出的模型中心 catalogue/runtime、任务租约与 worker 领取逻辑优先保持原状，除非职责分析证明存在独立可验证边界。

### 6.3 发布协调器最后整合

`DataReleaseService` 的候选边界是配置读取、只读集成检查、重试/continuation 规则、
入队准入与幂等校验。`_enqueue_release_task` 的锁内检查与任务创建保持一个可见事务。

先复用 `data_release/planning.py`、`resilience.py`、`publication_execution.py` 等已有实现，
不得新增第二套重试状态机。另一位 agent 新建的代码工作区、已发布数据和研究收据模块
按最终交接版本保留；只有完成回归后再讨论是否需要调整接口。

## 7. 兼容性和容易遗漏的耦合

| 边界 | 实施要求 |
| --- | --- |
| CLI | 命令路径、参数名/默认值、退出码、输出关键字段和默认输入输出目录一致；导入不能执行工作 |
| Python imports | 保留实际使用的旧符号、异常和数据类型；显式导出，避免 `import *` |
| monkeypatch | 测试修改旧模块的 `run_git`、`_atomic_copy`、`time.sleep`、常量等；简单 re-export 不会改变新函数的全局变量绑定，需用明确依赖传递或保留兼容调度层 |
| 事务与任务 | `collect/organize/store`、锁、保存点、取消、心跳、租约、精确 dirty IDs 与提交顺序一致 |
| 发布恢复 | 固定候选、CAS baseline、已提交指针、公开验证、数据库确认分别保留；重试不重新选择输入 |
| 文件与路径 | 重新核对 `__file__.parents`、shell `BASH_SOURCE`、cwd、临时目录和锁目录；源码搬家不改变生产数据落点 |
| 缓存与指纹 | `site_data_dependencies.dependency_version`、下载 `_projection_code_sha256`、Astro fingerprint/build-cache 都包含实际输出依赖；允许一次有依据的失效，禁止伪造旧摘要 |
| 进程池 | worker 函数在模块顶层可导入和可序列化；必须测试真实子进程，不能只有直接调用 |
| 历史基线 | 迁移 `legacy_disease_access_baseline.json` 的路径时保留计数上限与理由；删除旧项和新增对应路径为同一变更，不放宽门禁 |
| 前端 | URL、同源 API、三语、主题、无数据/加载失败、无障碍、固定数据版本与缓存依赖保持一致 |
| 基础设施 | systemd/CI/Makefile/package scripts 指向稳定入口；第一批不改依赖版本、lockfiles、域名、bindings 或服务数量 |

## 8. subagent 并行施工安排

并发上限为主 agent + 3 个 subagent。下表是后续施工分组，现在仅执行分析。

| 批次 | Agent A | Agent B | Agent C | 主 agent |
| --- | --- | --- | --- | --- |
| 1 | 两个 Git 发布脚本及其独立实现/测试 | 本体同步纯规划及脚本兼容层 | 数据库重建步骤及兼容层 | 基线、任务边界、旧接口复核、共享清单、集成回归 |
| 2 | 站点导出与下载计划 | 文献查询/公开投影 | 知识证据和修复纯函数 | 指纹、导出总入口及跨模块一致性 |
| 3 | Dashboard 指定领域组件 | Astro 指定领域组件/库 | 存储质量纯规则 | 共享类型、路由/API合同、客户端与数据投影联调 |
| 4 | 文献 pipeline/enrichment 的纯规则与输入转换 | 国别来源辅助模块 | Mapping Registry 只读/规划部分 | 协调治理服务、数据指纹与跨域类型 |
| 5 | 发布行为与恢复验证（只读审阅） | 独立报告/审计命令剩余职责 | 单一 owner 串行处理订阅 Worker（仅必要边界） | 发布协调器与 API 统一串行集成 |

每次派工包含：固定基线、只允许写的文件、禁止写的共享文件、保持的符号与字段、
现有测试清单、验收样例、输出格式。不同 agent 使用独立 worktree 或隔离副本；
不会因为工具共享目录就假定修改彼此隔离。

主 agent 独占：跨组 `__init__.py` 导出、依赖指纹清单、legacy access 基线、
路由注册、API生成合同、依赖/锁文件、Makefile/CI/systemd、文档总索引。
子 agent 如需变更这些文件，只提交具体建议和补丁片段，由主 agent 串行合并。

禁止 agent 之间互相覆盖文件、批量格式化全仓、执行数据库迁移、部署、重启服务，
或为了让测试通过而删掉原有失败场景。一个分组测试失败时保留问题并修复该分组，
其他不依赖它的分析和实现可继续；依赖批次不提前进入。

## 9. 验证矩阵

本次分析不运行生产脚本，也不把之前文档整理通过的测试当成拆分验收。
施工开始后，对交接基线先运行将受影响的测试，再比较同一组测试和相同输入的结果。

| 分组 | 现有测试／检查 | 关键验收 |
| --- | --- | --- |
| Git 发布 | `test_publish_download_repo.py`、`test_publish_raw_git_archive.py` | 实际临时 Git 仓库；未变文件复用、坏清单拒绝、中断恢复、失败不提交 |
| 本体 | `test_sync_disease_ontology_script.py`、`test_ontology_sync_validation.py`、`test_disease_ontology_sync_service.py` | 纯计划一致、地理/疾病语义、审计恢复边界 |
| 重建 | `test_database_rebuild_plan.py`、`test_database_rebuild_run.py`、`test_full_rebuild_us_scope.py` | 四模式、部分提交、保存点、居民口径 |
| 站点/下载 | `test_site_data_orchestration.py`、`test_site_incremental_generation.py`、`test_direct_download_files.py`、`test_download_country_merge.py` | 固定输入输出一致、精确增量、真实进程池、未变分区复用 |
| 研究投影 | `test_literature_radar.py`、对应 research/papers publisher 测试 | 双语门禁、私有字段隔离、唯一研究指针、恢复幂等 |
| 知识 | `test_knowledge_briefs.py`、`test_knowledge_stage_boundaries.py`、`test_knowledge_automation_service.py` | 证据门禁、阶段状态与提交边界 |
| 来源存储/映射 | `test_series_observation_store.py`、`test_disease_mapping_registry_v3.py`、受影响国家夹具 | 质量隔离、自然键、修订历史、映射发布 |
| 发布协调 | `test_code_only_preflight.py`、`test_data_release_pipeline.py`、`test_release_stage_integration.py`、交接后的 workspace/receipt tests | 冻结候选与真实公开基线、取消/重试、旧指针保护 |
| 数据库快照 | `tests/integration/test_postgres_release_snapshot.py` | 仅用显式测试数据库；导出快照一致、只读拒写、失效拒绝；未提供测试库则明确记为未验证 |
| 前端/Worker | 所属 npm scripts、类型检查、相关 Vitest/Node tests、夹具 E2E | 三语与移动端、API合同、网络失败、版本固定、Workers 缓存/路由语义 |
| 全局边界 | repository boundaries、legacy access、导入/编译、文档与命令路径 | 无新遗留依赖、无缓存/数据混入源码、无断链 |

字节比较使用相同冻结夹具；时间、运行 ID 等有意变化字段单独说明后再规范化。
不通过删除整个 manifest 字段、跳过错误或放宽质量规则制造一致结果。
新增测试只针对新边界的行为风险，不为机械移动写镜像实现测试。

## 10. 开始与交付步骤

1. **等待用户“可以开始”。** 该通知之前只有清单与方案文档可修改。
2. 读取另一位 agent 的交接，记录最终提交及明确的未提交改动；保留本轮已做的文档清理。
3. 在独立工作区重现交接源码。未提交文件按明确清单和摘要复制，不复制 `.env`、凭据、数据库、运行缓存或真实发布候选。
4. 重新计算清单、核对新增模块和受影响测试，修正过时的行数与提取目标；不能将本次快照强行套到后来的代码。
5. 先记录首批合同与基线结果，再派 3 个互不重叠的实现任务。
6. 各组提交聚焦的变更与验证结果，主 agent 审阅后串行集成，随后执行跨组回归。
7. 一批的兼容行为和验证通过后进入下一批；每个单元一到两个热点，较大批次分多轮。`defer` 项只有交接和测试条件满足时才进入，不为完成目录表而强制拆分。
8. 交付可审阅的源码差异、最终目录/旧新映射、验证结果及剩余未验证项。生产上线沿用现有受管发布流程。

隔离验证失败时只撤回当前分组的源码差异，不覆盖其他成果。本轮结构施工不执行真实发布；若未来另行上线，已成功的外部上传或指针提交不能被恢复旧工作树当作撤销，须遵循发布恢复协议。
不为此次拆分重写 Git 历史或删除迁移链。

## 11. 按文件落实的详细目标

本节保留 subagent 的具体边界和验收建议。目标模块均为候选，不是一次性建立所有目录的任务清单。
阶段先后统一以第 8 节为准；源码行数仅描述分析快照。现有测试与建议补充的场景不等于本轮已执行。

### 11.1 后端、知识、映射与 API


#### `src/services/disease_knowledge_service.py`

4651 行，同时承担证据资格、双语质量、修复选取、查询、数据库写入及任务阶段；update_disease 单方法 755 行。

- `src/knowledge/repair_policy.py`：`_generated_profile_failures`、`_bilingual_alignment_failures`、`_profile_repair_sections`、`_profile_repair_diagnostics_by_language`、`_profile_repair_metadata`、`_select_knowledge_repair_candidates`、`_knowledge_repair_task_priority`、`_normalize_requested_sections_by_language`、`_normalize_repair_reasons_by_language`、`_merge_repair_payload`。先提取不获取会话、锁或任务状态的纯计算；避免与现有 quality/evidence/profile_schema 重复；治理服务的 KnowledgeRepairPlan 也由同一规则所有者协调。
- `src/knowledge/retained_profiles.py`：`RetainedProfileEvidence`、`_assess_retained_profile_evidence`、`_evidence_gate_result`、`_source_qualification_reason`、`_bilingual_publication_prerequisites`。只处理快照和明确输入；恢复与归档数据库动作仍留原编排。
- `src/services/disease_knowledge/queries.py`：`DiseaseKnowledgeUpdateService.list_catalogue`、`DiseaseKnowledgeUpdateService.get_detail`、`DiseaseKnowledgeUpdateService._find_disease`。明示数据库会话依赖；不复制同类 catalogue visibility 规则。
- `src/services/disease_knowledge/repository.py`：`_existing_sources`、`_related_parent_sources`、`_upsert_source`、`_upsert_brief`、`_reconcile_source_refresh`、`_restore_sources_demoted_by_refresh_miss`。后续阶段移出 SQL 与对象存储，但 commit/rollback、advisory lock 和 StageRun 属原调用方。
- `src/services/disease_knowledge_service.py`：`DiseaseKnowledgeUpdateService.update_disease`、`DiseaseKnowledgeUpdateService._execute_task`、`DiseaseKnowledgeUpdateService._execute_source_refresh_task`。保留公开入口与业务阶段编排，薄方法显式传依赖；本轮不机械拆分 755 行更新方法。

**兼容约束：**

- 保持 DiseaseKnowledgeUpdateService、原模块函数导入和 CLI/API 返回结构。
- _KNOWLEDGE_STAGE_RUN ContextVar 与 disease-knowledge advisory transaction lock 只存在一个权威定义，线程中的 copy_context 与取消传播保持。
- 保留 source-only 到 generation 的阶段边界、原始捕获、证据门禁、脏标记与写入的事务边界、双语修复语义。
- 原模块被 monkeypatch 的 _fetch_sources_for_update、_generator_for_mode、_generated_profile_failures、_upsert_brief 等不能仅 re-export 后悄悄失效；采用薄包装或注入。

**后续验证：** `tests/unit/test_knowledge_briefs.py`；`tests/unit/test_knowledge_stage_boundaries.py`；`tests/unit/test_knowledge_catalogue_fairness.py`；`tests/unit/test_disease_knowledge_schema_locks.py`；`tests/unit/test_update_disease_knowledge_cli.py`；`tests/unit/test_knowledge_automation_service.py`；`tests/unit/test_knowledge_source_runs_api.py`。

#### `src/knowledge/sources.py`

2261 行，来源种类和解析逻辑已自然分组；fetch_with_report 是并发、超时、来源健康与结果聚合边界。

- `src/knowledge/source_adapters/registry.py`：`DiseaseKnowledgeFetcher._build_registry_definition_source`、`DiseaseKnowledgeFetcher._build_icd10_definition_source`。保留 registry definition 只能证明限定章节的语义，不能升格为完整医学证据。
- `src/knowledge/source_adapters/who.py`：`DiseaseKnowledgeFetcher._fetch_who_pages`、`DiseaseKnowledgeFetcher._fetch_who_don`。共用由 fetcher 注入的请求会话、节流、取消与日志。
- `src/knowledge/source_adapters/wikimedia.py`：`DiseaseKnowledgeFetcher._fetch_wikidata`、`DiseaseKnowledgeFetcher._fetch_wikipedia`、`DiseaseKnowledgeFetcher._fetch_wikipedia_summary`、`DiseaseKnowledgeFetcher._search_wikipedia`。同一 adapter policy 下执行，不新建独立全局 health 计数。
- `src/knowledge/source_adapters/literature.py`：`DiseaseKnowledgeFetcher._fetch_pubmed`、`DiseaseKnowledgeFetcher._fetch_crossref_metadata`。保留 PMID/DOI、language、licence、review_status 和 metadata 字段。
- `src/knowledge/source_adapters/web.py`：`DiseaseKnowledgeFetcher._fetch_configured_sources`、`DiseaseKnowledgeFetcher._crawl_html_page`、`DiseaseKnowledgeFetcher._fetch_web_search`、`DiseaseKnowledgeFetcher._build_msd_metadata`。MSD 继续仅 metadata；configured-source 与 trusted-domain 策略不变。
- `src/knowledge/source_parsing.py`：`DiseaseKnowledgeFetcher._extract_html_content`、`DiseaseKnowledgeFetcher._normalize_search_result_url`、`DiseaseKnowledgeFetcher._phrase_variants`、`DiseaseKnowledgeFetcher._relevance_score`。优先纯 HTML/query/ranking helpers，可逐函数委托。
- `src/knowledge/sources.py`：`SourceCandidate`、`SourceFetchReport`、`DiseaseKnowledgeFetcher.fetch_with_report`、`DiseaseKnowledgeFetcher._get`、`DiseaseKnowledgeFetcher._request_session`、`DiseaseKnowledgeFetcher._throttle`。保留合同和共享状态所有者；后续若单独 transport，仅搬一个实例而非每适配器复制。

**兼容约束：**

- SourceCandidate / SourceFetchReport / DiseaseKnowledgeFetcher 继续由原路径导入。
- 共享 _host_rate_lock/_host_last_request_at、_adapter_health_lock 和 adapter health 状态不可按新模块复制。
- thread-local session、request-error lock、response cache、取消信号、copy_context 和 timed-out adapter 报告保持；不能把未完成 adapter 当零结果导致来源降级。

**后续验证：** `tests/unit/test_knowledge_live_source_resilience.py`；`tests/unit/test_knowledge_briefs.py`；`tests/unit/test_knowledge_stage_boundaries.py`。

#### `src/knowledge/llm_brief_generator.py`

1647 行，生成和翻译各为大方法；提示构造与结果修复可先独立而不改变持久模型任务。

- `src/knowledge/brief_prompts.py`：`AIDiseaseBriefGenerator._system_prompt`、`AIDiseaseBriefGenerator._prompt_payload`、`AIDiseaseBriefGenerator._user_prompt`、`AIDiseaseBriefGenerator._citation_repair_prompt`、`AIDiseaseBriefGenerator._quality_repair_prompt`、`AIDiseaseBriefGenerator._json_format_repair_prompt`。显式传 schema、evidence、language，保持现有提示内容和 token 计算。
- `src/knowledge/brief_responses.py`：`AIDiseaseBriefGenerator._parse_json`、`AIDiseaseBriefGenerator._field`、`AIDiseaseBriefGenerator._citation_failure_fields`、`AIDiseaseBriefGenerator._repairable_quality_failures`、`AIDiseaseBriefGenerator._empty_scaffold`。只抽纯转换，不重复 citations/quality 的现有算法。
- `src/knowledge/llm_brief_generator.py`：`AIDiseaseBriefGenerator.generate_with_trace`、`AIDiseaseBriefGenerator.translate_from_payload_with_trace`、`AIDiseaseBriefGenerator._complete_with_policy`、`AIDiseaseBriefGenerator._peer_review_brief`。保留模型调用、deadline、retry 和 interaction trace 编排；不同时改写模型路由策略。

**兼容约束：**

- generate/generate_with_trace/translate_from_payload_with_trace 签名与 trace、usage、模型身份不变。
- 保留 cancellation、输出失败分类、引用修复上限、双模型评审和发布门禁；纯拆分不改变生成文本策略。
- 类静态方法现有 monkeypatch 注入点继续有效。

**后续验证：** `tests/unit/test_knowledge_briefs.py`；`tests/unit/test_knowledge_peer_review.py`；`tests/unit/test_knowledge_stage_boundaries.py`。

#### `src/services/disease_mapping_registry_service.py`

2012 行；schema、来源发现、审核、覆盖统计和 release 写入混合。

- `src/services/disease_mapping/identity.py`：`normalize_source_text`、`_category_key`、`_assertion_key`、`_candidate_key`、`_source_id_for_row`。同一来源 key 算法，不与 ingestion mapping_effects 另建身份体系。
- `src/services/disease_mapping/queries.py`：`_category_page`、`DiseaseMappingRegistryService.list_categories`、`DiseaseMappingRegistryService.stats`、`DiseaseMappingRegistryService._query_effective_coverage`。先移 SQL/只读投影；保持 aggregation 设置在调用方事务。
- `src/services/disease_mapping/discovery.py`：`DiseaseMappingRegistryService.discover_rows`、`DiseaseMappingRegistryService.bootstrap_all_sources`、`DiseaseMappingRegistryService._import_reviewed_source_category_mappings`、`DiseaseMappingRegistryService._inherit_unambiguous_series_mappings`。依赖 repository/session 显式传入；reviewed mapping 优先级与 legacy 兼容保持。
- `src/services/disease_mapping/releases.py`：`DiseaseMappingRegistryService.create_release`、`DiseaseMappingRegistryService.activate_release`、`DiseaseMappingRegistryService.reconcile_placeholder_source_identities`。仅后续阶段；一个事务所有者掌握 release 锁、active pointer、outbox、失效标记。
- `src/services/disease_mapping_registry_service.py`：`DiseaseMappingRegistryService.ensure_schema`、`DiseaseMappingRegistryService.accept_and_publish_candidate`。保留兼容 facade、schema guard 和 accept/create/activate 的事务编排；不能在 __init__.py 导入时建表。

**兼容约束：**

- 保留 registry service 公共方法、候选状态、release identity 与固定 release 解析语义。
- accept_and_publish_candidate 原子性、advisory locks、outbox 与 projection invalidation 一起验证。
- ingestion/mapping_effects、mapping 文件、source scope、domain ORM 共享合同需由整合者串行处理。
- 实例方法 monkeypatch accept_candidate/create_release/activate_release 不因委托绕开。

**后续验证：** `tests/unit/test_disease_mapping_registry_v3.py`；`tests/unit/test_mapping_release_projection_invalidation.py`；`tests/unit/test_ingestion_mapping_effects.py`；`tests/unit/test_disease_mapping_config.py`；`tests/unit/test_disease_mapping_source_schema.py`。

#### `dashboard/api/routers/sources.py`

1559 行路由里包含聚合 SQL、来源配置、flow DTO 和调度 CRUD；get_sources_flow 437 行。

- `src/control_plane/source_flow_queries.py`：`_enable_parallel_aggregate`、`_observation_rollup`、`_load_series_flow_facts`。使用现有 control_plane 应用边界，与 SourceQueryRepository 协同；SQL 不下沉到 HTTP schemas。
- `src/control_plane/source_flow.py`：`_flow_bucket`、`_flow_dates`、`_series_source_scope`、`_build_data_stages`、`_scope_from_task`、`_source_from_task`、`_pick_latest_crawl_task`。接受已查询事实/任务快照并组装流状态，避免从 core 反向 import dashboard。
- `src/control_plane/source_configuration.py`：`_source_kind`、`_source_supports_start_year`、`_history_start_year`、`_source_policy_config`、`_build_source_policy`、`_country_source_config`。复用 src/core/source_scopes，不重写各国来源规则。
- `dashboard/api/routers/sources.py`：`get_sources_config`、`get_sources_flow`、`create_automation_job`、`update_automation_job`。留鉴权、参数校验、异常映射与响应 model；自动化写入可以委托既有 AutomationService。

**兼容约束：**

- series 与 legacy 统计不能重复计算；source/geography/date-window 的口径与 ECDC 等国家策略保留。
- _flow_rollup_cache/_flow_rollup_lock 与 TTL 只有一个 owner；不可因为 facade 重复 import 产生两份缓存。
- 保留 endpoint、response_model、错误码与默认 query 值；API OpenAPI/客户端统一生成。

**后续验证：** `dashboard/api/routers/test_sources_iceland.py`；`tests/unit/test_control_plane_http_contract.py`；`tests/unit/test_europe_source_scopes.py`；`tests/unit/test_collection_source_control.py`。

#### `dashboard/api/routers/ai.py`

1503 行，Pydantic schema 定义、知识治理、报告任务和 provider/model 管理放在同一模块。

- `dashboard/api/schemas/ai_tasks.py`：`AIStartRequest`、`AIContentGovernanceRequest`、`AIContentGovernanceResponse`、`KnowledgeRepairRetryRequest`、`DiseaseDuplicateAuditRequest`、`DiseaseDuplicateAuditStatusRequest`。保持 schema 类名与 component identity，避免无行为变更造成客户端全量重写。
- `dashboard/api/schemas/disease_knowledge.py`：`DiseaseKnowledgeCatalogueItem`、`DiseaseKnowledgeTaskSkipped`、`DiseaseKnowledgeStartRequest`、`DiseaseKnowledgeStartResponse`、`DiseaseKnowledgeRepairRequest`、`DiseaseKnowledgeRepairResponse`、`DiseaseKnowledgeSourceDetail`、`DiseaseKnowledgeBriefDetail`、`DiseaseKnowledgeDetail`。复用已存在 knowledge_sources schema；不要合并 source-only 与生成任务的不同合同。
- `dashboard/api/schemas/model_center.py`：`ProviderCreateRequest`、`ProviderBootstrapRequest`、`ProviderUpdateRequest`、`ProviderOut`、`AvailabilityWindowIn`、`ModelCreateRequest`、`ModelUpdateRequest`、`ModelOut`、`RuntimeRouteOut`、`AIModelCallLogOut`。只提 schema；runtime model queue 已有 routers/models.py，避免重复新增 models API。
- `dashboard/api/routers/knowledge.py`：`list_disease_knowledge_catalogue`、`start_disease_knowledge_tasks`、`start_disease_knowledge_repair_tasks`、`run_ai_content_governance`、`requeue_failed_knowledge_repairs`、`get_disease_knowledge_detail`。router 分组仅内部 include_router，不改 /api/ai 外部 URL。
- `dashboard/api/routers/model_center.py`：`list_providers`、`create_provider`、`update_provider`、`delete_provider`、`discover_provider_models`、`list_models`、`create_model`、`update_model`、`delete_model`、`list_runtime_routes`。与 knowledge 路由按文件所有权分工；公共 router 的组装由单一整合者写。

**兼容约束：**

- 原模块 router/被导入类通过兼容路径可用。
- URL、operationId、响应 enveloping、status code、auth 与 Pydantic component name 不变。
- dashboard/openapi.json 与 dashboard/src/generated/api.d.ts 由整合者一次生成。

**后续验证：** `tests/unit/test_control_plane_http_contract.py`；`tests/unit/test_knowledge_source_runs_api.py`；`tests/unit/test_model_center_catalogue_policy.py`；`tests/unit/test_model_center_bootstrap.py`。

#### `src/services/ai_content_governance_service.py`

1550 行；可将 knowledge retry 策略与 literature/source/learning review 适配分开，但存在重要并发新鲜度协议。

- `src/knowledge/repair_policy.py`：`KnowledgeRepairPlan`、`_language_sections_from_failure`、`_model_failure_languages`、`_publication_status_languages`、`plan_failed_knowledge_repair`、`is_recoverable_knowledge_failure`。与 disease_knowledge_service 同一 owner 提取，防止两个 agent 同时写 repair_policy.py。
- `src/services/content_governance/review_inputs.py`：`AIContentGovernanceService._literature_review_item`、`AIContentGovernanceService._literature_article_item`、`AIContentGovernanceService._source_review_item`、`AIContentGovernanceService._learning_item`。只抽输入快照/序列化；先复用 src/literature/review_application.py。
- `src/services/ai_content_governance_service.py`：`_release_review_connection`、`_reload_review_input`、`_mark_review_dirty`、`AIContentGovernanceService.run_once`。保留前置 commit 释放连接、模型返回后 FOR UPDATE 重读与 fingerprint 确认、脏标记编排。

**兼容约束：**

- 模型调用前释放数据库连接，回答应用前校验当前 row 版本，不能重新把长模型调用包入事务。
- 不降低审核门禁、第二意见的独立模型约束和精确发布 scope。

**后续验证：** `tests/unit/test_ai_content_governance_service.py`；`tests/unit/test_literature_scoped_governance.py`；`tests/unit/test_literature_worker_governance.py`；`tests/unit/test_knowledge_automation_service.py`。

#### `src/services/literature_automation_service.py`

1376 行；已有纯 decide_* 策略，_reconcile_database 480 行是有状态批处理边界。

- `src/literature/automation_policy.py`：`AutomationDecision`、`decide_evidence_link`、`decide_article`、`decide_summary`、`_article_hold_reasons`、`_has_explicit_correction_parent`、`_published_revalidation_status`。由 literature 目录 owner 接收；保留 existing policy/review_application 的语义，不新建独立 governance 引擎。
- `src/services/literature_automation_queries.py`：`_link_reconciliation_statement`、`_article_reconciliation_statement`、`_summary_reconciliation_statement`、`_english_summary_fingerprint_statement`。后续 query 层候选；先核对另一 agent 的查询优化结果再确定，避免覆盖。
- `src/services/literature_automation_service.py`：`LiteratureAutomationService.reconcile`、`LiteratureAutomationService._reconcile_database`、`restore_parent_excluded_summaries`。保留 batch/commit、幂等 audit 和人工排除恢复策略。

**兼容约束：**

- reviewed/excluded/published 状态和父子文章约束不变。
- 批次大小、锁时间上限、审计 provenance 与无变化项计数必须保持。
- 与 src/literature、literature_gap_service、发布侧共享接口，跨 owner 改动串行。

**后续验证：** `tests/unit/test_ai_content_governance_service.py`；`tests/unit/test_literature_scoped_governance.py`；`tests/unit/test_literature_worker_governance.py`。

#### `src/services/disease_mapping_quality_service.py`

939 行，以审计和发现规则为主，适合提纯数据规范化而不改审核语义。

- `src/services/disease_mapping/quality_inputs.py`：`normalize_mapping_name`、`split_mapping_aliases`、`_series_descriptor`、`_normalized_registry_rows`、`_registry_candidates`、`_coerce_edges`、`_descendant_depths`。纯数据规范化与 ontology 索引，无数据库副作用。
- `src/services/disease_mapping/quality_rules.py`：`DiseaseMappingQualityService._multiple_series_findings`、`DiseaseMappingQualityService._malformed_mapping_findings`、`DiseaseMappingQualityService._ambiguous_name_findings`、`DiseaseMappingQualityService._aggregate_child_findings`。按 rule family 接收 snapshot，保持 finding 排序、key 和 severity。

**兼容约束：**

- 不将 normalized display label 当 source identity。
- 审计输入/输出结构、确定性排序、旧版与 release pinned 对照语义不变。

**后续验证：** `tests/unit/test_disease_mapping_registry_v3.py`；`tests/unit/test_disease_mapping_config.py`。

#### `src/services/disease_mapping_ai_service.py`

856 行；确定性防护规则与调用/审核/保存编排可分离。

- `src/services/disease_mapping/ai_guardrails.py`：`DiseaseMappingAIService.sanitize_mapping_draft`、`DiseaseMappingAIService._apply_interpreted_name_guardrail`、`DiseaseMappingAIService._apply_concept_relation_guardrail`。纯规则接收已解析 shortlist/ontology，避免自行开会话。
- `src/services/disease_mapping_ai_service.py`：`DiseaseMappingAIService.suggest_for_category`、`DiseaseMappingAIService._save_reviewed_candidates`、`DiseaseMappingAIService._publish_reviewed`、`DiseaseMappingAIService._queue_suggestion_notification`。保留 claim/release、审阅 provenance、候选保存与通知事务。

**兼容约束：**

- reviewer/author 模型身份与确定性 guardrails 不变。
- 通知 outbox 与 release 更新原子性、失败释放 claim 和恢复幂等不变。

**后续验证：** `tests/unit/test_disease_mapping_registry_v3.py`；`tests/unit/test_mapping_release_projection_invalidation.py`。

#### `src/services/report_service.py`

1050 行；报告复用选择、期次匹配、邮件与生成流程可以按职责分开。

- `src/services/report_reuse.py`：`ReuseCandidate`、`ReportService._normalize_reuse_strategy`、`ReportService._is_hard_failure`、`ReportService._is_reusable_v4_report`、`ReportService._score_reuse_candidate`、`ReportService._format_candidate_summary`。先纯复用判定；明确数据签名与支持版本保持。
- `src/services/report_queries.py`：`ReportService._collect_reuse_candidates`、`ReportService._find_report_with_period_fallback`、`ReportService._find_report_by_data_signature`。后续会话注入查询；保留 period fallback 和输出 completeness 判据。
- `src/services/report_service.py`：`ReportService.execute`、`ReportService._send_reused_report_email`、`ReportService._adjust_period_to_available_data`。编排与有副作用的邮件/发布入口初期不动，保持国家和时间窗口。

**兼容约束：**

- 重复运行复用/resume 策略、Task/Report 状态与取消语义不变。
- 不得因重构多发邮件、多发布或扩大 report scope。

**后续验证：** `tests/unit/test_reports_auto_publish.py`；`tests/unit/test_report_schedule_service.py`；`tests/unit/test_report_attention_semantics.py`；`tests/unit/test_report_surveillance_snapshot.py`。

#### `src/ai/model_center.py`

当前 1327 行，已完成 catalogue/runtime 分层；旧约 2936 行报告过时，不应重新拆一套。

- `src/ai/catalogue/（既有）`：`get_runtime_routes`、`test_route_connection`、`check_model_by_id`、`check_provider_by_id`、`check_all_models`。仅未来有真实改动需求时增补现有 catalogue 分工；不把 durable dispatcher 移回 catalogue。
- `src/ai/model_center.py`：`bootstrap_model_center_from_env`、`invalidate_model_center_bootstrap_cache`、`record_route_runtime_failure`、`record_route_runtime_success`。目前保持应用 facade、engine 级缓存和状态持久化边界。

**兼容约束：**

- engine scoped bootstrap cache、WeakKeyDictionary event-loop locks 不得在新模块复制。
- metadata-only call log、provider cooldown、模型 route/lease 语义不变。
- 原模块 get_engine/get_runtime_routes/test_route_connection 的 patch 注入继续工作。

**后续验证：** `tests/unit/test_model_center_anthropic_base.py`；`tests/unit/test_model_center_bootstrap.py`；`tests/unit/test_model_center_catalogue_policy.py`；`tests/unit/test_model_center_native_probe.py`；`tests/unit/test_model_center_runtime_health.py`；`tests/unit/test_model_center_thinking_mode.py`；`tests/unit/test_ai_runtime.py`；`tests/unit/test_ai_runtime_metrics.py`；`tests/integration/test_postgres_ai_runtime.py`。

#### `src/services/agent_workflow_service.py`

1660 行但已有 agent_workflow runner/repository/actions/helpers/prompts/search/memory/serializers；不能按行数认定尚未拆分。

- `src/services/agent_workflow/（既有）`：`AgentWorkflowService.execute`。继续使用现有 runner.execute_workflow 并通过参数注入数据库、task manager、时钟等依赖，保持薄 wrappers。

**兼容约束：**

- 不删除现有 facade 私有方法，先检索测试与 CLI/API 引用。
- 状态恢复、memory backend、动作队列与最终结果序列化保持。

**后续验证：** `tests/unit/test_agent_workflow_actions.py`；`tests/unit/test_agent_workflow_helpers.py`；`tests/unit/test_agent_workflow_memory.py`；`tests/unit/test_agent_workflow_pure_helpers.py`；`tests/unit/test_agent_workflow_repository.py`；`tests/unit/test_agent_workflow_runner.py`；`tests/unit/test_agent_workflow_search.py`。

#### `src/services/task_worker.py`

1003 行，claim 230 行与 owned worker 250 行跨越 capacity、lease、停止恢复及三条工作通道；改动风险高。

- `src/services/task_worker.py`：`_claim_next_task_uuid`、`_run_owned_worker`、`_requeue_interrupted_tasks_for_restart`、`_interrupt_resumable_tasks_for_restart`。首轮保留；未来优先纯 admission 决策，claim/heartbeat/requeue 必须在单一状态所有者下演进。

**兼容约束：**

- TASK_CLAIM_LOCK_KEY、_claimed_* 容器、role lane、heartbeat、owner fencing 和优雅重启语义不变。
- task_executor/task_manager/control_plane scheduler 与 AI runtime 的所有权协议不分头同时修改。

**后续验证：** `tests/unit/test_task_worker_config.py`；`tests/unit/test_task_worker_claim_resilience.py`；`tests/unit/test_worker_shutdown_lifecycle.py`；`tests/unit/test_ai_runtime.py`；`tests/integration/test_postgres_ai_runtime.py`。

#### `src/services/disease_ontology_sync_service.py`

348 行，已有 ontology → seed payload 构造；主流程拟把脚本纯规划提至 src/ontology，应复用此合同。

- `src/ontology/（由 CLI owner 确定纯规划模块）`：`DiseaseOntologySyncPayload`、`build_disease_ontology_sync_payload`。如果合并新 planner，保留旧模块 re-export 并复用 payload，不造第二套 registry→database 转换。
- `src/services/disease_ontology_sync_service.py`：`restore_missing_registry_series`。保留调用方事务内插入缺失、ON CONFLICT DO NOTHING 与回读语义；不覆盖 reviewed mappings。

**兼容约束：**

- sync script、ingestion 与 ORM registry 的自然键/metadata/availability 语义一致。
- database_rebuild_plan/import/run 已有分工，维持而非新造 rebuild 框架。

**后续验证：** `tests/unit/test_disease_ontology_sync_service.py`；`tests/unit/test_sync_disease_ontology_script.py`；`tests/unit/test_ontology_sync_validation.py`；`tests/unit/test_disease_ontology_models.py`。

### 11.2 生成、存储、来源与发布


#### `src/generation/direct_download_files.py`

已有 5 个 direct_download 子模块；1121 行入口仍包含分区构造、复用校验、任务调度、结果/manifest 组装，适合继续沿用既有包边界。

- `src/generation/direct_download/partitions.py`。_split_spec、_dataset_parts 与分区输出规划；先提取纯计算部分
- `src/generation/direct_download/jobs.py`。顶层可 pickle 的 country/disease/dataset 任务计划和调度依赖，保持进程工作函数可导入
- `src/generation/direct_download/manifest.py`。结果与 manifest 组装、复用输入身份；不偷偷改变失败时删除旧制品行为

**兼容约束：**

- 保留 direct_download_files 入口符号、常量、签名和默认值。
- _projection_code_sha256 必须包含新提取模块；一次正常失效允许，不能伪造旧指纹。
- CSV/JSON/XLSX 字节、路径、分区窗口、排序和 checksum 相同；未变化分区重用行为相同。
- ProcessPoolExecutor、模块全局函数替换和 spawn/forkserver 下真实序列化必须验证；简单重导出不等于保留 monkeypatch 语义。
- 避免并行共享可变清单，异常和原子写入顺序不变。

**后续验证：** `tests/unit/test_direct_download_files.py`；`tests/unit/test_download_country_merge.py`；`tests/unit/test_site_incremental_generation.py`。

#### `src/generation/site_data_export.py`

collect_site_export_context 839 行，写完整和写增量制品各 244/154 行；根模块承担多种 orchestration，但周围 helper 已基本分离。

- `src/generation/site_export/context.py`。选择输入、收集 country/disease 元数据、组装 context；保持快照 session 所有权
- `src/generation/site_export/countries.py`。现有 country_projection_inputs/workers 的编排和已发布国家基线组装
- `src/generation/site_export/artifacts.py`。完整/增量制品写入顺序和最终完成回执；继续调用现有 writer/catalogue

**兼容约束：**

- 原 collect_site_export_context、write_site_export_artifacts、write_incremental_site_export_artifacts、export 路径与签名保留。
- 保持选择范围、来源身份、锁、内存释放点与冻结候选；不得把 live DB 最新值伪装成冻结快照。
- 读事务只覆盖导出；构建/上传前结束，不把 AsyncSession 传入并行工作。
- 只有所有 country/disease 曲线制品成功后才提升 curve-dependencies.json；失败重跑不能把半成品当完成基线。
- site_data_dependencies.dependency_version 显式加入新实现源文件；保留代码和配置变化失效行为。
- 完整与增量导出固定样本语义/字节等价；已发布研究基线保持同一个 generation。

**后续验证：** `tests/unit/test_site_data_orchestration.py`；`tests/unit/test_site_incremental_generation.py`；`tests/unit/test_site_data_writer.py`；`tests/unit/test_site_data_about.py`；`tests/unit/test_release_projection_snapshot.py`；`tests/unit/test_literature_publication_snapshot.py`。

#### `src/generation/site_data_literature.py`

文献选择/映射、监测证据关联、公开制品输出耦合；648 行 collect 和 296 行证据函数有清晰数据边界。

- `src/generation/literature_export/selection.py`。发布资格与书目/证据页层级选择
- `src/generation/literature_export/records.py`。历史 seed、审核记录与标题估计记录投影
- `src/generation/literature_export/evidence.py`。使用固定监测/Situation 基线关联证据
- `src/generation/literature_export/writer.py`。公开索引/详情/shards 输出，调用已有 research_shards 与 writer

**兼容约束：**

- 公开书目、来源摘要、完整 AI 证据页资格保持分离；双语/审核/权利控制及撤稿过滤不变。
- 对外字段、稳定 article/slug 身份、时间排序、源链接及保留元数据不变。
- 保留已发布监测和 Situation 上下文，不查询或拼入任意最新版本。
- title_cache_identity 当前仅包含本文件字节；提取标题投影实现后须聚合真实实现指纹。
- 复用 literature_integrity、literature_visualizations、literature_title_cache 与 research_shards，避免第二套逻辑。

**后续验证：** `tests/unit/test_literature_radar.py`；`tests/unit/test_literature_publication_snapshot.py`；`tests/unit/test_literature_publish.py`。

#### `src/data/storage/series_observation_store.py`

2669 行且同时包含纯质量规则、身份归一化、holding 序列选择、批量保存和修订；风险来自事务/业务规则，不是行数本身。

- `src/data/storage/series/quality.py`。_zero_tail_issues、_time_completeness_issues、单序列修订与映射覆盖的纯规则
- `src/data/storage/series/identity.py`。时期、自然键和不依赖 DB 的来源身份标准化
- `src/data/storage/series_observation_store.py`。继续拥有 save_rows、修订批量落库、JP holding 变更事务及旧 API

**兼容约束：**

- 事实、修订、发布变化与 publish_dirty 不能分批独立提交。
- 观察自然键、来源/统计口径、missing/suppressed/zero 区分和 authoritative revision 优先级保持。
- 质量隔离及 JP all-case/sentinel holding series 身份不可因提取改变。
- 不以结构整理为名修改查询、schema 或执行生产库更新。
- 数据库验证只能使用显式配置隔离库；当前分析阶段不执行测试或数据库动作。

**后续验证：** `tests/unit/test_series_observation_store.py`；`tests/unit/test_precise_ingestion_changes.py`；`tests/unit/test_crawl_service_series_dual_write.py`；`tests/unit/test_provisional_ingestion_policies.py`。

#### `src/generation/site_data_views.py`

国家展示、国家月汇总、疾病展示与紧凑投影均在一个模块；适合按纯结果投影拆分。

- `src/generation/site_export/country_views.py`。国家月汇总与国家展示
- `src/generation/site_export/disease_views.py`。疾病完整/紧凑展示
- `src/generation/site_export/provenance_views.py`。lossless source-series 与 provenance 的共享纯变换

**兼容约束：**

- 保持原模块重导出、完整/紧凑字段与默认值。
- 来源 series 的事实粒度、missing 值和月汇总规则不变。
- 输出排序、时间序列缺口和 country-to-disease 依赖身份不变。
- 与 site_data_export 和 dependency_version 的修改串行集成。

**后续验证：** `tests/unit/test_site_data_views.py`；`tests/unit/test_site_data_orchestration.py`；`tests/unit/test_download_country_merge.py`。

#### `src/generation/site_data_queries.py`

1269 行查询同时覆盖来源序列、旧记录、报告和知识；纯按行数重写 470 行来源查询风险大。

- `src/generation/site_export/report_queries.py`。报告列表/详情查询
- `src/generation/site_export/knowledge_queries.py`。知识/文献支持查询；仅在实际调用关系支持时提取
- `src/generation/site_data_queries.py`。暂保留源序列与兼容记录 SQL 及原导出路径

**兼容约束：**

- 不变更 SQL 过滤、来源覆盖、统计口径或 legacy fallback 条件。
- 保持 caller 提供的 AsyncSession 和 snapshot；不能在 helper 中悄悄新开 live session。
- 不共享 AsyncSession 给并行任务。
- 迁移后 dependency_version 纳入实际实现文件。

**后续验证：** `tests/unit/test_site_data_queries.py`；`tests/unit/test_release_projection_snapshot.py`；`tests/integration/test_postgres_release_snapshot.py`。

#### `src/services/data_release_service.py`

2571 行 service 仍同时负责配置/检查、准入/队列、重试与触发；当前另一个 agent 正在优化，必须冻结后复核。

- `src/services/data_release/ 既有检查与配置模块`。优先抽取只读配置/视图/检查组合
- `src/services/data_release/ 调度与准入职责模块`。交接后确认任务锁、冻结候选和重试的唯一所有者；不按方法数切成 mixin
- `src/services/data_release_service.py`。保留 service 公共 API 和兼容委托

**兼容约束：**

- 保持任务准入锁、冷却/重试、渠道状态和公共 API。
- 冻结 candidate 的 scope、dirty IDs、来源/目标身份；不能在异步阶段重新选取另一批数据。
- 不修改队列运行状态或发布生产数据来验证结构重构。
- 新 code_workspace/published_data/research receipt/baseline 模块继续保留其职责，不能合回 service。

**后续验证：** `tests/unit/test_data_release_service.py`；`tests/unit/test_data_release_checks.py`；`tests/unit/test_data_release_resilience.py`；`tests/unit/test_data_release_pipeline.py`。

#### `src/services/data_release/pipeline.py`

顶层执行函数涵盖多阶段，但已有多个职责清晰的 stage/helper；当前 code-only 与研究发布快照持续变化。

- `既有 data_release/build_stage.py、repository_publishers.py 等`。优先让顶层通过既有 helper 表达流程
- `后续 preflight / artifact preparation / acknowledgement helper`。仅在交接后确认输入输出与恢复边界后提取；保留顶层发布顺序

**兼容约束：**

- 冻结输入 → 构建/上传 → 验证 → DB ack 的顺序不变。
- CAS baseline、publication fence、immutable 对象和 dirty IDs ack 保持；失败不能虚构已发布成功。
- 外部上传成功后的故障不能通过恢复旧工作树/制品假装撤销外部成功；保留已成功子发布证据。
- _run_repository_publications 的聚合失败、retry stage/cause、取消传播不变。
- code_workspace.prepare 改 runtime.astro_dir 的上下文边界与 snapshot 引用需以交接后代码为准。

**后续验证：** `tests/unit/test_data_release_pipeline.py`；`tests/unit/test_data_release_stage_receipts.py`；`tests/unit/test_publication_fence.py`；`tests/unit/test_published_data_workspace.py`；`tests/unit/test_research_publication_monitor.py`；`tests/unit/test_research_publication_verifier.py`。

#### `src/services/crawl_service.py`

已存在 ingestion runner/strategies/countries 和 crawl_pipelines；剩余服务接口/国家 dispatch 与 243 行 series 保存需要围绕既有边界继续提取。

- `src/services/ingestion/strategies.py 与现有 country adapters`。来源专属归一化/提交策略与 service 委托
- `src/services/crawl_service.py`。保持公共任务 API、取消处理、事实与发布变化事务协调

**兼容约束：**

- collect/organize/store 的阶段记录和取消检查保持。
- 事实、mapping effects、修订、dirty 标记共同提交；mark_dirty 不独立 commit。
- 精确空 publication_changes 与 unknown/conservative 的区别必须保留。
- holding rows、registry discovery、source audit、force/revision 语义不变。
- 不创建一个覆盖所有国家的强制通用 parser 或 runner。

**后续验证：** `tests/unit/test_crawl_service_series_dual_write.py`；`tests/unit/test_precise_ingestion_changes.py`；`tests/unit/test_ingestion_artifact_safety.py`；`tests/unit/test_ingestion_capture_safety.py`；`tests/unit/test_ingestion_mapping_effects.py`；`tests/unit/test_ingestion_replay.py`；`tests/unit/test_ingestion_resilience.py`；`tests/unit/test_ingestion_source_audit.py`；`tests/unit/test_cn_us_jp_crawl_pipelines.py`；`tests/unit/test_monthly_crawl_pipelines.py`；`tests/unit/test_iceland_crawl_pipeline.py`。

#### `src/data/crawlers/ca.py`

Ontario 采集同时包含 embed/PBIR 发现、Power BI DM0 解码、指标归一化和范围验证。

- `src/data/crawlers/ca_ontario/discovery.py`。embed URL、资源及 PBIR visual discovery
- `src/data/crawlers/ca_ontario/powerbi.py`。query 构造与 DM0 响应解码
- `src/data/crawlers/ca_ontario/normalization.py`。export table 指标/范围归一化

**兼容约束：**

- 保持 Ontario 与 national 来源独立、来源 scope 和指标粒度。
- 不统一 AU 与 Ontario 的不同 Power BI 协议。
- ca.py 的外部导入/API 保留，使用离线响应样本对齐结果。

**后续验证：** `tests/test_ca_ontario.py`；`tests/contract/test_ca_on_observations.py`。

#### `src/data/crawlers/au.py`

来源发现、DAX 请求、DM0 响应、州/全国抓取聚合可分边界，已有 powerbi_public helper 可复用。

- `src/data/crawlers/au_nindss/discovery.py`。runtime config/capture 与资源发现
- `src/data/crawlers/au_nindss/queries.py`。DAX/query 请求构造与 AU 特定响应解码
- `src/data/crawlers/au_nindss/projection.py`。州/全国来源粒度聚合

**兼容约束：**

- 保留既有 powerbi_public.py。
- 国家/州计数、时间维度、来源口径和并发上限不变。
- 使用离线 fixture，不在结构验证时采集实时外部来源。

**后续验证：** `tests/contract/test_au_observations.py`；`tests/unit/test_au_dax_list_parser.py`；`tests/unit/test_au_subdivision_backfill.py`。

#### `src/data/processors/is_history.py`

1580 行包含多种历史 workbook 解析、文件准备/审计与最终导入，先提取纯解析有明确收益。

- `src/data/processors/iceland_history/parsers.py`。annual/monthly/legacy ICD workbook 读取与标准化
- `src/data/processors/iceland_history/audit.py`。来源质量/缺口审计输出
- `src/data/processors/is_history.py`。保留 prepare_files/import_rows 入口与事务

**兼容约束：**

- 不同工作簿结构、年份/月份和疾病映射规则不变。
- 国家/地区以及 observation 来源身份保持。
- 不在拆分过程中执行历史数据库导入或更新 registry。

**后续验证：** `tests/unit/test_iceland_history.py`；`tests/unit/test_iceland_crawl_pipeline.py`；`tests/unit/test_audit_iceland_integration.py`；`tests/unit/test_sync_iceland_registry.py`。

#### `src/data/crawlers/cn_provinces.py`

省份抓取已有明确 cn_province_adapters 分类，剩余 narrative/table normalization 与 PHSM 历史审计可逐步分离。

- `src/data/crawlers/cn_province_adapters/ 现有相关模块`。只将来源专属规则下沉到对应 adapter
- `src/data/crawlers/cn_provinces.py`。保留发现/分发和兼容 API

**兼容约束：**

- 保留已存在的 registry、province adapter 和来源解析语义。
- 没有覆盖所有省份的稳定 fixture 前不批量重写省份 adapter。
- 不创建第二套省份来源路由。

**后续验证：** `tests/unit/test_cn_us_jp_crawl_pipelines.py`；`tests/unit/test_ingestion_source_audit.py`。

### 11.3 文献领域


#### `src/literature/pipeline.py`

1476 行；LiteraturePipeline.execute 761 行同时处理多个 provider、fallback、规范化、checkpoint、分批写入、autopilot 和任务阶段。已有 clients/normalization/collection_sources/controlled_discovery 应继续复用。

- `src/literature/candidate_merge.py`：`_candidate_key`、`_merge_candidate`、`_deduplicate_candidates`。先拆确定性候选合并，保持来源优先级、同 DOI preprint 信号、version relations 和 controlled_discovery_origins。
- `src/literature/catalogues.py`：`_global_country_catalogue`、`LiteraturePipeline._classification_catalogues`。共享 disease/country/taxonomy 快照；literature_gap_service 正在被其他 agent 优化，交接后一次更新调用方并保留旧 facade。
- `src/literature/transactions.py`：`_is_postgres_deadlock`、`_retry_postgres_deadlock`。复用已有事务模块放 PostgreSQL 明确 deadlock 的重试工具；保留 pipeline 兼容入口，literature_automation_service 当前直接引用该私有函数。
- `src/literature/collection.py`：`LiteraturePipeline.execute`、`_isolate_optional_source`、`_preserve_optional_checkpoint`。P2 从 execute 的 collect 区块抽有界 provider collection；返回 records/checkpoints/source outcomes，复用既有 clients 和 collection_sources.source_plan，不造另一套 provider registry。
- `src/literature/pipeline.py`：`LiteraturePipeline.execute`、`LiteraturePipeline._create_run`、`LiteraturePipeline._finish_run`、`LiteraturePipeline._load_latest_completed_checkpoint`。保留 StageRun、capture 生命周期、分批事务与完成记录编排；只让 orchestration 委托上面的明确步骤。

**兼容约束：**

- 每次 execute 重置且整次使用同一已提交 ingest checkpoint 快照；named source subset 保留未采集来源 cursor。
- Crossref 失败的 PubMed fallback 不能推进 Crossref watermark，provider timeout/partial failure 不能被解释成完整成功。
- batch upsert 与 stages.mark_dirty 同一事务，明确死锁后重试整个 transaction；_finish_run 只有 completed 才随完成记录写 dirty marker。
- metadata enrichment 整体降级时保留 review hold 并跳过 autopilot；preprint 不能因来源合并自动进入正式发布。
- capture.__exit__(*sys.exc_info()) 和 BaseException 取消传播保持；provider 子任务退出前必须取消并 join，不能留下失去父任务的请求/写入。
- 保留旧 module/class patch 点和 ROOT/日期辅助导入，replay、reclassification、automation、gap service 都有现有引用。

**后续验证：** `tests/unit/test_literature_radar.py`；`tests/unit/test_literature_pipeline_classification_batches.py`；`tests/unit/test_literature_provider_failure_isolation.py`；`tests/unit/test_literature_crossref_recovery.py`；`tests/unit/test_literature_discovery_checkpoints.py`；`tests/unit/test_literature_deadlock_retry.py`；`tests/unit/test_literature_concurrent_cancellation.py`；`tests/unit/test_literature_scoped_governance.py`；`tests/unit/test_collection_source_control.py`。

#### `src/literature/enrichment.py`

1140 行，LiteratureSummaryGenerator 与 LiteratureEnrichmentPipeline 承担提示、翻译拓扑修复、候选查询、并发和写入。

- `src/literature/summary_contracts.py`：`canonical_summary_fingerprint`、`source_fingerprint`、`_parse_json`、`_has_long_verbatim_overlap`、`LiteratureSummaryGenerator._coerce_field_payload`、`LiteratureSummaryGenerator._raw_field_is_null`、`LiteratureSummaryGenerator._canonical_contract_fallback`、`LiteratureSummaryGenerator._canonical_contract_fallback_summary`、`LiteratureSummaryGenerator._source_scope_limitations`。与 SUMMARY_FIELDS/ALLOWED_EVIDENCE/语言及协议常量同一所有者；保持序列化和 fingerprint 字节，纯转换先提。
- `src/literature/summary_generation.py`：`LiteratureEvidenceAgent`、`EnrichmentResult`、`LiteratureSummaryGenerator.generate`、`LiteratureSummaryGenerator._repair_translation_topology`。保留 generator 类公共路径；请求 evidence/schema 和结果清洗可独立纯函数，模型调用仍经过现有 BaseAgent/durable runtime，不另建 scheduler。
- `src/literature/enrichment_repository.py`：`LiteratureEnrichmentPipeline._load_articles`、`LiteratureEnrichmentPipeline._should_skip`、`LiteratureEnrichmentPipeline._store`、`LiteratureEnrichmentPipeline._store_failure`。后续明确会话依赖与写入事务，保持人工编辑保护、质量尝试与脏标记；搬迁不改变 SQL 选择和优先级。
- `src/literature/enrichment.py`：`LiteratureEnrichmentPipeline.execute`、`LiteratureEnrichmentPipeline._load_route_shard_preferences`、`LiteratureEnrichmentPipeline._rotated_route_preferences`。保留任务入口/并发与取消所有权；原模块薄 wrapper 显式传 generator/task manager/数据库依赖。

**兼容约束：**

- 先生成/载入 canonical English，再生成 zh/fr；null topology、translation_provenance 与 canonical fingerprint 保持，失败目标仍 retry_pending。
- 模型生成先存 review，最终发布由既有 autopilot+peer review 决定；不能因为提取纯解析直接转 published。
- generated_by=control-plane-editor、editorial_reviewed_at 及人工发布 summary 不被后台覆盖；瞬时模型失败不消耗同样的 quality attempt。
- _store/_store_failure 写 summary 与 mark_dirty 同一事务；progress failure 不掩盖正在传播的原始异常。
- execute 任一子任务失败必须取消并 join 所有 article_tasks；模型容量仍由现有 runtime 决定，业务 semaphore 只是有限输入供给。
- SUMMARY_FIELDS/source_fingerprint/canonical_summary_fingerprint 被 governance/automation 直接导入；兼容 wrapper 与猴补丁点保留。

**后续验证：** `tests/unit/test_literature_source_enrichment.py`；`tests/unit/test_literature_enrichment_cancellation.py`；`tests/unit/test_literature_capacity.py`；`tests/unit/test_literature_publication_readiness.py`；`tests/unit/test_literature_scoped_governance.py`；`tests/unit/test_literature_worker_governance.py`。

#### `src/literature/repository.py`

487 行，是内聚的 normalized candidate → ORM 持久化边界；upsert 186 行但锁、状态保护和事件需要一致执行。

- `src/literature/repository.py`：`LiteratureRepository.upsert`、`LiteratureRepository._lock_candidate_identifiers`、`LiteratureRepository._find_identifier_matches`、`LiteratureRepository._map_version_relations`、`LiteratureRepository._replace_links`。当前保留 caller session，不独立 commit，不按表创建多个会话 repository。
- `src/literature/identity.py（仅后续有复用需求时）`：`LiteratureRepository._select_article_match`、`LiteratureRepository._find_identifier_conflicts`、`_merge_version_relations`、`_classification_metadata`。可提纯输入/输出规则；_select_article_match 当前会更新 candidate.article_id/slug，抽取时不可漏掉这份既有 mutation。

**兼容约束：**

- advisory locks 对 doi/pmid/pmcid/openalex_id 按排序获取，避免跨写入者冲突和死锁；稳定标识优先级与冲突只记录不抢占保持。
- 已有 editorial_locked、autopilot 已发布、历史 excluded、published 不因普通 classifier 重新变 review 的保护保持；integrity change 仍生效。
- classification fingerprint 未变化不反复 flip-flop 状态与 links；article facts、links、version relations 与 status events 留在同一调用方事务。

**后续验证：** `tests/unit/test_literature_radar.py`；`tests/unit/test_literature_deadlock_retry.py`；`tests/unit/test_literature_payload_compaction.py`；`tests/unit/test_literature_mutation_transactions.py`；`tests/unit/test_literature_classification.py`。

#### `src/literature/review_workflow/repository.py`

853 行，其中 advance_many 251 行、feed 129 行。已经配有 coordinator/reducer/application，当前复杂性来自一个有界持久工作流的事务协议。

- `src/literature/review_workflow/repository.py`：`ReviewWorkflowRepository.ensure_drain`、`ReviewWorkflowRepository.feed`、`ReviewWorkflowRepository.advance_many`、`ReviewWorkflowRepository.cancel_open`、`ReviewWorkflowRepository.recover_orphans`。初期保留事务、generation 和 owner fencing 一处；不能同时拆 coordinator/runtime store。
- `src/literature/review_workflow/prompts.py（P3 可选）`：`ReviewWorkflowRepository._request`。未来只抽 request/prompt 构造，schema/policy 以参数注入；payload 文件写入与 queue submit 的事务协作仍留 repository。

**兼容约束：**

- lock order 固定为 parent → workflow IDs → article IDs → summaries；先取得整批锁再变更，不能分 helper 各自乱序锁。
- parent execution generation 校验防旧 worker 回写，ensure_drain 的 frozen settings/high_watermark/cursor/admitted 保证有限 pass 与幂等续跑。
- 等待 provider 不持有数据库事务或 parent row lock；只有 coordinator 推进自身 parent，runtime 负责 provider concurrency/retry。
- activity enqueue、workflow activity_id/state/version 关联原子提交；取消仅 request cancellation，由 dispatcher 停 transport 并释放 lease。
- policy version/deadline/fingerprint 变化终结为 deferred/stale，模型晚到回答不能覆盖更新内容；开放 subject guard 与 orphan recovery 保持。

**后续验证：** `tests/unit/test_review_workflow.py`；`tests/unit/test_review_workflow_reducer.py`；`tests/unit/test_literature_capacity.py`；`tests/unit/test_literature_worker_governance.py`；`tests/unit/test_ai_runtime.py`；`tests/integration/test_postgres_ai_runtime.py`。

#### `src/literature/review_application.py`

395 行；已是同步治理和 durable review 共用的事务应用入口，继续复用，不再拆出另一套 publish/exclude 应用器。

- `src/literature/review_application.py`：`LiteratureReviewApplication.apply`、`LiteratureReviewApplication.archive_incomplete`、`lock_review_subjects`、`_lock_active_parent`。保留 parent/subject locks、fingerprint 重读、editorial lock、summary parent checks 与 dirty marker。

**兼容约束：**

- article publish/exclude 必须 decision_is_grounded；关键词/索引词不能代替 abstract 原文支持片段，无法证明则 hold。
- 仅 status=review 且 fingerprint 一致、integrity current、peer_reviewed 的条目可应用；summary 同时核验 canonical 与 parent。
- dry_run 不改 ORM、不写 dirty；真改变内容发布状态后才在同事务标记。

**后续验证：** `tests/unit/test_literature_screening.py`；`tests/unit/test_literature_worker_governance.py`；`tests/unit/test_literature_scoped_governance.py`；`tests/unit/test_review_workflow.py`；`tests/unit/test_literature_mutation_transactions.py`。

#### `src/literature/weekly_ai_review.py`

604 行，包含公共 review packet、确定性验证、注册表原子写入以及模型 runner。可以拆纯合同，但不能在周报发布优化还未交接时搬持久审核状态。

- `src/literature/weekly_review_contracts.py`：`public_review_packet`、`deterministic_review_issues`、`parse_ai_review_response`、`project_weekly_ai_review`、`sanitize_weekly_verdict`。若开始拆，与 weekly_briefs 的 fingerprint/人审 projection 协同设计；保持每种审核身份和已有版本，不合并人审/AI审状态。
- `src/literature/weekly_review_registry.py`：`load_weekly_ai_review_registry`、`bound_weekly_ai_review`、`_registry_payload`、`_valid_stored_review`、`_stored_reviewed_at`、`write_weekly_ai_review`。单一 registry owner；保留 fcntl flock、brief 二次读取、严格更新版本判定、fsync+atomic replace。
- `src/literature/weekly_ai_review.py`：`WeeklyAIReviewRunner`、`review_weekly_brief_files`。保留 worker/CLI/发布直接调用的 facade；当前 weekly_briefs↔weekly_ai_review 有 lazy import 协作，不可通过顶层互导引入循环。

**兼容约束：**

- review 绑定 exact brief_fingerprint；在锁内重读 brief 拒绝审核期间已变化内容，只允许严格较新有效审核替换同一 fingerprint 的旧记录。
- registry 写入使用同目录临时文件+fsync+os.replace，跨进程锁只有一份；延迟的旧回答不能覆盖较新 verdict。
- AI 公开投影只含 allowlisted verdict/issue_codes/time/protocol/model/provider，不暴露内部提示或其他私有审核字段。
- 现有 configs/literature/weekly_ai_reviews.json 被另一任务修改；结构计划不能回退、重写或迁移其中记录。

**后续验证：** `tests/unit/test_research_weekly_review_workflow.py`；`tests/unit/test_literature_weekly_briefs.py`；`tests/unit/test_literature_release_validation.py`；`tests/unit/test_research_digest_dispatch.py`。

#### `src/literature/weekly_briefs.py`

356 行，周报证据投影与内容绑定的人审证明结构内聚；无按行数拆分必要。

- `src/literature/weekly_briefs.py`：`enrich_weekly_briefs`、`weekly_brief_review_fingerprint`、`project_weekly_editorial_review`、`load_weekly_review_registry`、`_bound_editorial_review`。当前保留；只有 weekly_review_contracts 确立时才将共享 fingerprint 单点迁移并保持原 public exports。

**兼容约束：**

- fingerprint v2 对既有 en/zh brief 必须字节兼容；出现法语正文时才纳入法语字段，不能因重构无故使全部历史审核失效。
- 人审要求安全公开字段、时区明确且非未来的时间，重复 week/损坏 registry fail closed。
- 公开 cited_findings 只来自已发布双语结构化 summary；监测关联保持非因果解释，review 状态不能虚构。

**后续验证：** `tests/unit/test_literature_weekly_briefs.py`；`tests/unit/test_research_weekly_review_workflow.py`；`tests/unit/test_literature_release_validation.py`；`tests/unit/test_research_digest_dispatch.py`。

#### `src/literature/release_validation.py`

162 行，现有单一 fail-closed 公共 payload 校验，应保持清晰入口而非碎成多层微模块。

- `src/literature/release_validation.py`：`validate_public_research_payload`、`assert_public_research_payload`。保留 stable blockers 和统一 API；若未来规则大量增长，可内部按 article/evidence/weekly family 提 helper，不改合同。

**兼容约束：**

- 禁止 abstract/source_payload/full_text 等私有字段越过发布边界；bibliographic-only 与全文摘要 catalogue 的不同条件保持。
- 重复 article_id/DOI、过期 classification、无双语已发布 summary、retracted/EOC、preprint 混入正式目录仍阻断。
- exact evidence 必须 current_window 且年龄符合限制；无 exact evidence 有显式 gap；weekly reviewer/AI reviewer 必须对应合法 brief_status。

**后续验证：** `tests/unit/test_literature_release_validation.py`；`tests/unit/test_literature_publication_readiness.py`；`tests/unit/test_literature_publication_snapshot.py`；`tests/unit/test_literature_bibliographic_catalogue.py`；`tests/integration/test_literature_publication_postgres.py`。

#### `src/literature/metadata_backfill.py`

573 行，backfill_existing_literature_metadata 377 行；职责明确但 provider cursor、coverage、file checkpoint 与批次事务不能随目录移动改变。

- `src/literature/metadata_backfill_plan.py（有复用需求才新增）`：`_coverage_plan`、`_normalize_targets`、`_checkpoint_payload`、`_backfill_projection`。优先可测试纯规划，继续复用 normalization 与 payloads。
- `src/literature/metadata_backfill.py`：`backfill_existing_literature_metadata`、`_apply_candidate_projection`、`_write_checkpoint`。保留默认 dry-run、apply 批次提交与每 provider cursor；写入不能抢占 editorial 或 reclassification 权限。

**兼容约束：**

- dry-run 不写 DB 或 checkpoint；file checkpoint 必须在成功 DB commit 后推进。
- 某 provider 失败只保留该 provider 的旧 cursor，其他 provider 的有效 enrichment 可以成功提交；下次幂等重试不漏批次。
- 只更新 metadata/OA/provider projection，保持发表/人工审核状态；formal commit 失败不能吞成可忽略 provider error。

**后续验证：** `tests/unit/test_literature_metadata_backfill.py`；`tests/unit/test_literature_mutation_transactions.py`；`tests/unit/test_literature_backpressure_health.py`。

#### `src/literature/classification.py`

653 行，为版本化、可审计 lexical/semantic classifier；已与 screening/review 分离。

- `src/literature/classification.py`：`classify_candidate`、`apply_surveillance_relation`。保持 classification version、evidence traces、分值与排序；当前 classifier 返回 review，不越权发表或据无匹配排除。
- `src/literature/classification_matching.py（后续可选）`：`_semantic_terms`、`_lexical_hits`、`_semantic_hits`、`_drop_shadowed_country_matches`、`_controlled_entity_matches`。只有匹配器实际复用或扩展复杂度增加才提取，保持词条优先级与国家/疾病消歧。

**兼容约束：**

- 候选匹配是发现证据，不是最终 disease relevance/review 结论；screening 仍需 abstract context。
- 保持 CLASSIFICATION_VERSION 对重分类/发布门禁的意义；纯搬迁不随意升版本导致全库重分类。

**后续验证：** `tests/unit/test_literature_classification.py`；`tests/unit/test_literature_screening.py`；`tests/unit/test_literature_historical_rescreen.py`；`tests/unit/test_literature_reclassification.py`；`tests/unit/test_literature_pipeline_classification_batches.py`。

#### `src/literature/controlled_discovery.py`

608 行，query planning 与 bounded provider execution 已独立于 pipeline，不能因主 pipeline 拆分重复实现。

- `src/literature/controlled_discovery.py`：`build_controlled_query_batches`、`select_controlled_query_batches`、`fetch_controlled_discovery`。如需后续提纯 planning，保持 query ID、plan fingerprint 和 checkpoint schema 兼容；首轮只由新 collection 层调用原模块。

**兼容约束：**

- 每个 selected provider/query 至少一个记录预算再分配剩余上限，不能在未真正查询时推进 checkpoint。
- hard query/request/record cap、legacy coverage unknown、per-query continuation 保持，cursor 不被新框架统一冲掉。

**后续验证：** `tests/unit/test_literature_controlled_discovery.py`；`tests/unit/test_literature_discovery_checkpoints.py`；`tests/unit/test_literature_provider_failure_isolation.py`。

### 11.4 Dashboard、Astro、Workers 与运维


#### `dashboard/src/features/ai/models/view.tsx`

模型中心混合供应商/模型/路由表单、查询筛选、运行状态与日志面板

- filters.ts: matchesQuery/providerMatchesFilter/modelMatchesFilter/routeMatchesFilter
- presentation.ts: 日期/耗时/错误信息及 routingStatusLabel/routingStatusReason
- components/: provider-form、model-form、runtime-route-panel、调用日志；view 仅组合页面
- hooks/use-model-center.ts: 当前编辑对象、筛选/弹窗状态；沿用 lib/hooks/useAIModels

**兼容约束：**

- 所有 mutation 输入和 query key 保持；权限/路由可用性解释与错误显示保持
- shared/ui 和 useAIModels 由集成人单独持有，功能 agent 不同时修改

**后续验证：** dashboard npm run lint / npm test；模型新增/编辑/删除、供应商禁用、路由不可用原因、刷新后选择状态的组件用例。

#### `dashboard/src/features/operations/automation/view.tsx`

自动化页同时负责时区/周期语义、源策略默认值、表单和列表

- schedule-model.ts: intervalMinutesToFields/intervalFieldsToMinutes/describeIntervalMinutes/scheduleLabel
- form-model.ts: toForm/findCountryTimezone/source 默认策略/presetTimeForIndex
- components/: schedule-form、schedule-table、详情面板；沿用同目录 api.ts

**兼容约束：**

- 时区和夏令时、interval 值往返保持
- process/save_raw/fill_missing/force 与 current-month 策略、retry 阈值不能因 UI 拆分改变

**后续验证：** dashboard lint/test；时区/间隔往返与各来源默认策略的行为测试。

#### `dashboard/src/features/operations/sources/flow/view.tsx`

来源流程页混合阶段图、映射风险登记和创建采集任务表单

- components/stage-pill.tsx: StageIcon/StagePill；flow-row.tsx: FlowRow
- mapping-risks.ts: flowRowKey/buildSeriesMappingRisks；components/series-mapping-register.tsx
- components/create-crawl-modal.tsx: CreateCrawlModal；复用 series-format.ts 和 collection-sources/shared

**兼容约束：**

- useTaskEventStream 刷新/关闭清理、源名称与国家范围保持
- 创建采集请求的每个选项默认值和 API schema 保持

**后续验证：** dashboard lint/test；series-format 既有测试；创建任务 payload/映射风险/流事件更新的组件测试。

#### `dashboard/src/features/data/overview/view.tsx`

数据概览页内含完整 QR 编码、Canvas 下载和图表页面

- export/qr-code.ts: appendBits、GF/RS 运算、QR 功能图案、createQrDataUrl
- export/chart-card.ts: loadImage/fitRect/downloadCanvas 及页面中卡片绘制流程
- presentation.ts 和 overview-panels/: 指标格式、趋势/月份对比面板；沿用 data/api.ts

**兼容约束：**

- QR 字节容量、非 ASCII URL 与超长 URL 当前处理规则保持，不趁拆分换算法
- 导出尺寸、文件名、标题/二维码、明暗主题保持；空值不能变 0

**后续验证：** dashboard lint/test；QR 输出可解码到原有目标的行为用例、超长/Unicode URL；Canvas 导出浏览器回归。

#### `dashboard/src/features/ai/interactions/view.tsx`

AI 交互页混合多语言文本、URL 模式、聊天分组与任务实时状态

- messages.ts: uiText
- presentation.ts: JSON/quality/UUID/status 格式
- components/interaction-row.tsx、grouped-run-chat.tsx；hooks/use-interaction-context.ts 管理任务/报告上下文

**兼容约束：**

- ?task= 与 ?uuid=&disease= 旧链接兼容；Suspense 边界保留
- 任务事件流订阅清理、知识任务 workflow-only 模式、选中线程状态保持

**后续验证：** dashboard lint/test；旧 URL/任务切换、空交互但有工作流、事件后自动刷新用例。

#### `dashboard/src/features/reports/list/view.tsx`

报告列表承载引用合并编号、证据图选择与报告预览动作

- citations.ts: sourceIdentity/referenceIndex/referenceUrl/formatReferenceCitation/collectReportReferences/linkCitationMarkers
- figure-selection.ts: assignFigureNumbers/sectionFigures/sectionDisplayType/curateSectionFigures
- components/: CitationAnchor/ReportReferences/preview；继续使用 report-figures.tsx、v4-preview.tsx、workflow.tsx

**兼容约束：**

- 引用顺序、去重、跳转安全与图号稳定
- 报告状态与发布/重试动作、API shape 不变；不与报表 API 同时改

**后续验证：** dashboard lint/test；混合旧版/V4 报告、重复引用、缺失证据图、动作可用性回归。

#### `dashboard/src/components/tasks/TaskDetailPanel.tsx`

任务详情混合日志展示、结构化字段抽取、阶段摘要与报告运行数据

- task-metadata.ts: asDisplayString/extractStructuredRows/normalizeStructuredRows/deriveRawArchivePath/tokenTotal/qualityOverall/latestStageSnapshots
- TaskLogEntry.tsx: LogBody/TaskLogEntry；TaskStageSummary.tsx
- 沿用 task-log-compaction.ts 与 task-log-display.ts，不重复实现折叠和截断

**兼容约束：**

- raw-collapsed/minimal/default 三种模式、日志窗口和折叠语义
- 保留当前公开 Props 与 useTasks/useReports 兼容导入

**后续验证：** dashboard lint/test；既有 task-log 测试；长日志/连续重复/原始路径缺失/阶段重试行为用例。

#### `dashboard/src/features/data/release/view.tsx`

发布控制页与正在更新的发布计划/执行/回执契约相连，交接后再拆

- form-model.ts: scheduleLabel/toForm；presentation.ts: statusTone/priorityTone
- components/: release-schedule-form、运行/检查/进度面板；view 继续负责组合

**兼容约束：**

- 以另一 agent 完成后的 publication schema/OpenAPI 为基线
- 发布计划、run、receipt、恢复/重试展示契约，不改执行编排

**后续验证：** dashboard lint/test/openapi:check；发布计划与运行回执组件测试；仅 mock API，禁止真实发布。

#### `astro-site/src/pages/subscribe.astro`

订阅页混合静态多语言结构、DOM 控制、预填/校验和网络请求

- components/subscription/: SubscriptionForm.astro、SubscriptionModal.astro、messages.ts
- form-model.ts: locale/国家来源预选/表单标准化
- controller.ts: 状态/提交/模态框/重发事件；处理 define:vars 的数据交接

**兼容约束：**

- EN/ZH/FR 路径、DOM id 与 focus/键盘行为、consent 保持
- 请求字段、确认/重发/管理链接、queued 与 sent/pilot 区分保持；不得发送真实邮件

**后续验证：** astro npm run check/test:public/test:i18n；浏览器 mocked API: 初次提交、校验失败、限流、重发和预填。

#### `astro-site/src/components/public/PublicDownloads.astro`

公开下载页混合目录构造、文案、筛选交互和列表样式

- public/downloads/catalogue.ts: countries/diseases 项、current/all-period 行数和文件选择
- messages.ts、DownloadCatalogue.astro、controller.ts 与本地样式
- 继续调用 lib/download-presentation.ts/downloads-data.ts，避免数据统计重复实现

**兼容约束：**

- current file 与全部历史行数区别、国家/疾病重复计数提示
- CSV/JSON/XLSX、历史分片、SEO 详情路径及三语言搜索保持

**后续验证：** astro test:public/test:i18n/check；空目录/仅历史分片/多语言别名筛选/下载链接回归。

#### `astro-site/src/components/situation/SituationExplorer.tsx`

Situation 浏览器混合 URL 初始化、地图选择、时间线图与国家 dossier

- explorer-state.ts: applyDeepLinks/diseaseIdFromQuery/tally；与现有 model.ts/state 语义对齐
- SituationTimeline.tsx: GraphCell/AlertItem；CountryDossier.tsx: SeriesRow/FusionCard/CountryDossier
- useSituationExplorer.ts 组合现有 useSituationData/currentSurveillanceSite，保持请求生命周期

**兼容约束：**

- 国家/疾病深链接、地图与列表选择一致、分页边界 LIST_LIMIT/TIMELINE_PAGE
- 列表/事件详情懒加载与取消、防止旧请求覆盖新选择，public visibility 保持

**后续验证：** astro test:public/test:charts/check；深链接、快速切换/取消、档案缺失、地图与时间线同步回归。

#### `astro-site/src/components/research/ResearchHotspotCharts.tsx`

研究热点图把数据类型、多个图形 option 和 React 生命周期集中在一个组件

- research/hotspots/types.ts 与 presentation.ts: 日期/百分比/escapeHtml
- 各图 option builder: stream/heatmap/burst/alluvial，先按现有 effect 的真实依赖划界
- 保留 ResearchHotspotCharts.tsx 作为组件入口，统一 resize/dispose 生命周期

**兼容约束：**

- 图表数据排序、tooltip escaping、EN/ZH/FR、主题和空数据行为
- 沿用 echartsResearch 模块注册，不能引入全量 ECharts

**后续验证：** astro test:research/test:charts/check；各图 fixture 与主题/resize/卸载的浏览器回归。

#### `astro-site/src/lib/research-ask.ts`

本地研究问答混合查询展开、文献排序、引用与答案综合

- research-ask/types.ts；query.ts: researchQueryTerms/normalizeText/alias 合并/searchUnits
- ranking.ts: rankResearchArticles/字段权重/去重/recency/tie-break/sourceAboutnessReview
- answer.ts: collectSignals/collectThemes/collectGaps/citationMarkers/answerResearchQuestion；根文件保留 re-export

**兼容约束：**

- 原有导出签名、确定性排序、显式 now、去重 DOI、证据置信阈值、引用编号
- catalogue/query-scope/facet 类型复用；不在整理时改变检索评分或医学结论

**后续验证：** astro test:research；现有 research-ask 测试；查询别名、相同分数、证据不足与跨语言的 golden fixtures。

#### `astro-site/src/components/charts/ReportV4Panel.tsx`

V4 报告展示混合本地化、趋势/病例曲线计算和章节布局

- charts/report-v4/presentation.ts: labels/number/status helpers
- curve-model.ts: numericSeries/aggregateCurveByPeriod/currentYearCumulativeCases
- components/: BackgroundCurve/ChangeCurveCell/CasesMoMCell/MarkdownBlock/Metric，保留懒加载 ReportEvidenceFigures

**兼容约束：**

- 缺失/0 区分、年累计/按月汇总边界、引用和 Markdown 输出
- 现有 chartExport/chartPngCard/共享 report-figure-options 避免重复；保持代码分割

**后续验证：** astro test:charts/test:public/check；跨年曲线、缺失年份、V4 报告空段、证据图懒加载回归。

#### `astro-site/src/components/charts/DiseaseDetailView.tsx`

疾病报告详情混合生命周期、月份分布、章节 Markdown 和图片导出壳

- charts/disease-detail/presentation.ts: formatter/categoryLabel；MonthlyDistributionChart.tsx
- ReportSection.tsx: localizedText/markdownHtml/SectionBlock；Figure.tsx: Figure 结构
- 保留根入口并复用已有 ChartResultActions/chartExport/site-theme，不创建另一套全局主题状态

**兼容约束：**

- 章节顺序、图号、翻译 fallback、图表主题事件清理、导出文件名
- Markdown 和图形导出的输入/输出等价

**后续验证：** astro test:charts/test:theme/check；月分布/缺失数据、EN/ZH/FR、导出和主题切换浏览器回归。

#### `cloudflare/subscriptions/src/handlers/situation-alerts.ts`

告警 ingest/outbox/queue/状态回收/SQL 和邮件投递集中；状态敏感，后置拆分

- lib/situation-alert-dispatch.ts: DispatchAlert/parseDispatchAlert 兼容两种 payload
- lib/situation-alert-repository.ts: fanOut/due/claim/recover/status SQL，事务和比较条件整体搬迁
- lib/situation-alert-delivery.ts: processSituationAlertOutbox 与邮件 ledger/SMTP；queue adapter 保持 ack/retry
- handler 保留 ingest/管理鉴权与 64 KiB bounded JSON，index 导入保持兼容 re-export

**兼容约束：**

- 幂等键+payload hash 冲突、pilot 命名空间与确认时间受众快照
- claim token/expiry/原子状态条件、发送前二次订阅校验
- 4xx 临时拒收重试、5xx dead_letter、发送结果 unknown 不自动重发；ledger 记账顺序
- 队列 ack/retry、自动政策开关、允许来源和隐私日志保持

**后续验证：** subscriptions npm test/npm run typecheck；existing situation-alert-handlers/official-notice-alert-integration/pilot/delivery-feedback suites；增加有必要的 claim 竞争/超时/SMTP unknown 合同测试，所有邮件 mock。

#### `cloudflare/subscriptions/src/handlers/campaigns.ts`

营销/周报任务 handler 同时负责受众匹配、个性化、领取投递与状态统计

- lib/campaign-audience.ts: contentItems/personalizedTemplates/matchedItems/audience/retryChangedPreferences
- lib/campaign-repository.ts: loadRow/loadRowByContentRef/progress/summary/detail/refreshStatus
- 保留 createCampaignHandlers 适配器与现有 lib/campaign.ts；按状态事务而非行数拆分

**兼容约束：**

- contentRef 幂等、weekly issue ownership、preference revision 改变后的重试语义
- claim/ledger/反馈后的终态、受众过滤与 personalized marker 不变

**后续验证：** subscriptions npm test/typecheck；campaign-handlers/campaign-integration/weekly-ownership/pilot/delivery-feedback integration suites。

#### `cloudflare/subscriptions/src/index.ts`

Worker 入口在路由装配之外还拥有确认投递、维护、限流和签名辅助逻辑

- lib/confirmation-delivery.ts: sendAndRecordConfirmationEmail 与对应 outbox 处理
- lib/maintenance.ts: runMaintenance 和既有 scheduled domain 的组合
- lib/token-service.ts: createSignedToken/verifySignedToken/signingSecret；路由和 handler factory 装配继续留在入口

**兼容约束：**

- fetch/scheduled/queue 入口与 bindings 名称保持
- 异步执行等待、确认邮件幂等/限流、签名 purpose/TTL、CORS 和验证路径保持
- 同一 agent 串行整合 index 与情况告警/campaign，禁止多个 agent 同改入口

**后续验证：** subscriptions npm test/typecheck；router/security/subscription-handlers/subscription-lifecycle/scheduled/rate-limit suites。

#### `cloudflare/subscriptions/scripts/wrangler-env.sh`

环境加载、Python 配置生成、D1 选项 SQL、部署/密钥命令共用高风险运维入口

- 保留脚本路径与命令界面；候选 scripts/lib/config_builder.py 提取 write_config 的嵌入 Python
- 候选 scripts/lib/options_sql.py 提取 generate_options_sql；shell 仅加载环境/校验门槛/分发
- 不得为了分类移动运行入口、远程目标默认值和密钥同步行为

**兼容约束：**

- local/staging/production 选择、远程认证/执行门槛和 secret 值不泄漏
- wrangler.generated/run 临时配置与导出权限/清理行为保持；不提交生成文件

**后续验证：** bash -n；配置/SQL 生成纯本地 fixture，禁止 remote/deploy/secret/migration 命令；实施前读取 wrangler skill 和当前 CLI 帮助。

### 11.5 脚本的检索分类与后续候选

153 个脚本分为运行/调度 25、发布/导出 24、历史输入/人口 22、质量/诊断 29、维护/修复 40、CI/自动化 7、已归档维护工具 6。
分类先体现在索引；有现存调用者的入口不立即物理移动。

| 文件 | 后续职责边界 |
| --- | --- |
| `audit_iceland_integration.py` | 来源/映射/制品/下载/站点只读校验归 `src/services/ingestion/audits/`；保留 CLI 汇总结构。 |
| `rebuild_source_series_components.py` | 来源 adapter 注册和纯计划归 ingestion；执行锁与数据库编排仍有明确所有者。 |
| `benchmark_ai_literature.py` | 样本选择、场景执行、报告聚合分开；CLI 可归 diagnostics；用隔离数据库与模拟模型验证。 |
| `import_{ch,hk,kr,tw}_history.py` | 核对差异后共用启动/参数适配，保留来源范围、月份口径和返回结构。 |
| `backfill_us_nndss_resident_series.py` | 先纯计划；保留居民/Total 身份与审计恢复，暂缓数据库写入编排调整。 |
| `cn_missing_data_get.py` | 先核对遗留写入路径与替代方案，不直接复制成新生产服务。 |
| `restore_disease_migration.py` | 保留审计恢复入口，本体拆分期间不同时改恢复实现。 |
| `dashboard.sh`、`run_*_service.sh` | 保留入口，确有重复再抽 runtime/lib；验证进程所有权、信号、cwd、BASH_SOURCE。 |

跨脚本共享还包括 `import_subdivision_population` 与 `backfill_pubmed_abstracts`；迁出时验证直接 CLI 和被其他脚本导入两种用法。
根 `main.py` 与 `src/cli/main.py` 保留两个 Typer 命令面，本轮不合并。

### 11.6 保护配置与运行边界

- `migrations/versions/` 的 31 个版本、Alembic 入口/模板与 `schema.sql` 保留；已退役业务的历史迁移仍是版本链，不因名称旧而删除。
- 主 agent 持有依赖/lockfile、CI、systemd、Makefile、共享 schema 与生成类型；不在拆分中升级依赖或改变默认远程目标。
- Dashboard 写代码前读取 `dashboard/AGENTS.md` 指定的已安装 Next 文档；公共 UI/hooks 与生成 API 合同由主 agent 统一集成。
- Astro 的 changelog、法律内容、翻译词表不因行数多而拆；全局 CSS 尊重 cascade/token。手写 analytics loader 与已有 surveillance 分层保留。
- Subscriptions 三个入口由同一个 agent 串行处理，维护 claim/idempotency/ledger/unknown 投递状态。所有邮件与外部调用使用 mock。
- Worker 保留 bindings、current pointer、generation/hash、GET/HEAD/OPTIONS、R2 streaming、缓存头、ASSETS fallback 与 HTML 校验。
- Workers 实施时读取对应 Cloudflare/Workers skill 并重新核对官方文档；本次是仓库结构分析，未完成平台最新 API/配额审查。运行 wrangler 前读取其 skill。
- `cloudflare/papers-json` 没有独立 `package.json`，不能假设进入目录执行 `npm test`；须明确 tests/tsconfig 与已有本地工具链后再验证。
