# v0.20.0-rc.6：跨模块集成与提交门禁

日期：2026-11-16（Asia/Shanghai）。基线为 `development` / `b86708df70b26ba11c90a8483e90905369acd0f2`，正式主站版本仍为 `0.19.4`。编号仅标识验收批次；没有创建提交、tag 或部署。

结论：**暂不通过**。跨模块离线回归、类型与样式检查通过；部分 PostgreSQL 语义用例未运行，候选文件还有仓库大小门禁问题，各功能批次也存在待修复项。不得用现有 Git index 检查通过代替未提交候选文件的完整准入。

## 检查范围与证据

主 agent 负责整合，独立复现各 subagent 的数据、地图、宿主与调度反例。检查导出查询/人口准入/增量依赖、国家父级关系、覆盖状态、ontology 完整性、前后端 API 合同和代码仓库边界。未把已有验收文档中的上线结果当成本轮证据。

| 检查 | 本轮结果 | 证据文件 |
| --- | --- | --- |
| Python 跨模块回归，14 个文件 | 156 passed / 15 skipped | `rc6-python.log` |
| Situation、国家覆盖与监测展示 Node 回归 | 103 passed | `rc6-node.log` |
| 图表完整测试命令 | 69 passed | `rc6-chart-tests.log` |
| Astro 类型及样式 | 522 files，0 errors / 0 warnings / 14 hints；样式通过 | `rc6-astro-check.log` |
| Python 编译检查 | 通过 | `rc6-python-compile.log` |
| Git diff 空白检查 | 通过 | `rc6-diff-check.log` |
| 既有 index 仓库边界检查 | 通过；只检查 2,804 个已入 index 文件 | `rc6-repository-boundaries.log` |
| 未提交候选额外大小检查 | 3 个文件超过 5 MiB，其中 2 个为新增测试 fixture | `rc6-config-artifacts.log` |
| 实际 ontology 配置加载验证 | 216 concepts / 23 groups；原始 2,690 series，模板展开后 2,821 series | `rc6-config-artifacts.log` |
| 初始变更/未跟踪 JSON 语法检查 | 全部可解析 | `rc6-config-artifacts.log` |

上述证据保存在 [evidence/20261116_review](evidence/20261116_review/README.md)。各命令存在重复覆盖，不把跨批次通过数相加称作独立用例数。15 个 Python skip 需要显式的 `TEST_QUERY_DATABASE_URL` 才验证 PostgreSQL 查询语义，本轮没有指向生产数据库代跑。

主 agent 额外重跑了 rc.1、rc.2、rc.4、rc.5 的离线反例，见 `rc6-country-reproduction.log`、`rc6-cn-reproduction.log`、`rc6-host-reproduction.log`、`rc6-automation-reproduction.log`。地图独立重跑见 `rc6-map-reproduction.log`：美国 D094 的 `sharedCeiling` 三次为 2,655 / 2,572 / 2,390 ms，与 rc.3 结论一致；这是纯函数采样，未测量浏览器 INP。

## RC6-01 · P2：新增 PE fixture 超过代码仓库 5 MiB 准入上限

位置：[test_pe_cdc.py:16](/home/likangguo/globalID/globalID2/tests/unit/test_pe_cdc.py:16)、[test_pe_cdc.py:158](/home/likangguo/globalID/globalID2/tests/unit/test_pe_cdc.py:158)；规则实现 [check_repository_size.py:136](/home/likangguo/globalID/globalID2/scripts/check_repository_size.py:136)。

当前新增 `tests/fixtures/ingestion/PE/official_bulletins.zip` 为 **8,557,778 bytes**，`2026w13_cover_summary.pdf` 为 **5,812,723 bytes**；均超过 **5,242,880 bytes**。两个文件都由本批测试直接依赖，若原样纳入代码提交，仓库大小门禁将拒绝。另有未跟踪生成物 `output/us-population-live/raw/census_2000_2010_intercensal.csv` 为 5,481,291 bytes，也不能作为代码提交。

现有边界脚本只读取 Git index；本次新增文件尚未入 index，所以其通过输出没有覆盖这些候选。独立检查读取初始候选清单，对当前文件使用同一个 `find_violations` 规则及无豁免的新对象策略复核，不通过 staging 或修改代码来制造结果。

建议：保留小而有代表性的官方页面/表格片段，或将原档留在外部数据存储并为离线测试准备小 fixture；排除运行生成物。不要扩充 legacy allowlist 绕过新对象门禁。修复后重新执行候选大小检查，并在最终提交前复验真实 index。

## 执行命令

仓库根目录：

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_site_data_about.py \
  tests/unit/test_site_data_queries.py \
  tests/unit/test_site_data_views.py \
  tests/unit/test_site_data_orchestration.py \
  tests/unit/test_country_projection_inputs.py \
  tests/unit/test_country_region_boundaries.py \
  tests/unit/test_country_pipeline_planning.py \
  tests/unit/test_disease_ontology_models.py \
  tests/unit/test_disease_cutover.py \
  tests/unit/test_missing_rate_values.py \
  tests/unit/test_series_quality_history_projection.py \
  tests/unit/test_home_summary.py \
  tests/unit/test_repository_boundaries.py \
  tests/unit/test_repository_size_guard.py
venv/bin/python -m compileall -q src scripts tests dashboard/api migrations
git diff --check
scripts/check_repository_boundaries.sh
```

`astro-site` 目录：

```bash
node --experimental-strip-types --test \
  src/components/situation/*.test.ts \
  src/lib/country-coverage.test.ts \
  src/lib/country-classifications.test.ts \
  src/lib/surveillance-data.test.ts \
  src/lib/surveillance-presentation.test.ts \
  src/lib/chart-source-scope.test.ts \
  src/utils/international.country.test.ts
npm run test:charts
npm run check
```

## 工作区一致性与边界

验收开始记录 536 个变更/未跟踪文件的 SHA-256，见 `workspace-before.json`。11:25（北京时间）复核发现其他并行工作继续修改 13 个原文件，包括 CO/MW 连接器、配置、共享入库和测试；见 `workspace-drift.json`。本轮没有改业务代码，也没有撤销这些并行修改。对相关来源批次与共享后端补查并重跑：rc.1 为 532 passed / 1 failed（同一 CO 断言），rc.5 仍为 172 passed。11:29:25 最终相对初始清单有 15 个路径变化，多出的两份为来源说明文档，见 `workspace-final.json`；rc.1 复验期间 CO 历史导入脚本仍发生变化，需冻结后另验。这不代表取得一个不可变的完整发布快照。

没有执行完整生产构建、真实浏览器端到端操作、在线来源全量对账、PostgreSQL 实库迁移/写入、后台重启、systemd 启用、发信、提交/推送或部署。Worker 只执行了 `--dry-run` 本地打包和 Miniflare 本地运行，具体结果在 rc.4。已有代码/数据同时变化时，最终发布前仍需对冻结候选重新验收；本报告不构成部署授权。
