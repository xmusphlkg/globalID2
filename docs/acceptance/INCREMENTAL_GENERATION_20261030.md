# 增量生成优化验收：2026-10-30

本次复用已有数据库和站点快照，修改生成流程及测试；没有进行全量采集、生产数据库操作、发布或生产重启。本记录覆盖生成器的隔离测试与合成性能对照，不代表生产环境的全部验收已经完成。

## 已验证行为

| 场景 | 验证结果 |
| --- | --- |
| `incremental_site=True`、国家列表为空 | 已有快照和发布覆盖元数据有效时，不读取任何国家或疾病的完整曲线 JSON，不调用国家或疾病浏览器投影函数；文献仍然导出。 |
| 仅美国范围更新 | 只读取美国的新数据，重建美国投影和受影响疾病；疾病快照里的日本片段保持原值，日本国家文件及不受影响疾病文件的字节和修改时间均不变。 |
| 美国某疾病数据删除 | 受影响疾病快照删除美国片段，同时保留日本片段，文献覆盖范围同步更新。 |
| 知识单独更新 | 小型知识 JSON 按内容更新，不依赖国家曲线脏范围；相同知识内容重复生成不改文件修改时间，知识质量摘要同步更新。 |
| 知识撤稿与目录清理 | 撤稿后的已知疾病生成不包含旧简报内容的 blocked 结果；已退出目录的知识文件删除。两种操作均不修改曲线文件。 |
| 相同数据再次标脏 | CSV/JSON/XLSX 输入指纹相同时复用已有文件，不启动下载生成进程，下载文件字节和修改时间保持原值。 |
| 同日期、同 `generated_at` 数据修订 | 修改病例数和人口相关的发病率数组后，内容指纹改变，重新生成下载文件；实际读取 CSV 验证修订后的 `cases=99`、`incidence_rate_per_100k=2.5`。 |
| 已有数据库未登记全部标准子地区 | 父国家范围仅刷新数据库已有子地区；单独明确指定的未知国家或子地区仍报错。 |
| 发布表缺失 | 缺少 `country_publish_state` 或 `public_disease_series` 时，全量及增量导出均明确提示先迁移。检查在 readiness 自动建表之前执行；测试验证 readiness、国家查询和投影均未执行。 |
| 发布表存在但尚无覆盖记录 | 保留有效的已有快照及未变投影，使用已有疾病快照保留文献覆盖信息。这是已迁移数据库的初始化行为，不支持缺表旧 schema。 |

态势导出由发布流水线独立执行 `scripts/run_situation_notices.py --export-only`，不受国家范围为空的分支影响；本次未修改发布流水线。文献增量写入函数也没有以国家范围为空为条件跳过。

下载指纹使用完整国家或疾病结构，包括所有序列内容、来源元数据、疾病各国来源和国家名称，另纳入下载实现与序列策略的源码哈希以及文件分区大小参数。生成时间 `generated_at` 不参与内容指纹。没有使用文件修改时间或高水位时间替代内容校验。人口计算后的发病率及其来源字段包含在序列输入中；知识文稿单独生成知识 JSON，不作为下载曲线内容的一部分。

## 公开国家撤下与只读导出补充

IE 的公开开关改为 false 或国家退出活动名单时，生成器通过旧 `meta.json` 与当前公开国家名单的差集识别退出范围。这属于明确撤下，不能作为普通未标脏国家复用。

- 仅加载退出国家的旧国家快照，定位受影响疾病；不重新采集任何国家。合并疾病时按当前允许的国家名单过滤旧片段，重新生成受影响疾病的完整快照、浏览器投影和下载文件。
- 在替换产物全部写入成功后，删除退出国家的完整快照、公开浏览器快照及 source-series 文件。未标脏的正常国家，其国家快照及国家下载文件的字节、修改时间保持不变。
- 集成测试使用先前公开过 IE 的隔离快照，撤下后实际读取 CSV、JSON、XLSX，验证均无 IE；仅有 IE 的疾病清除旧下载分片。国家曲线哈希均未变化的全量缓存路径也会处理撤下；随后再次空脏范围生成仍不读取或投影曲线。
- 文献覆盖元数据排除已撤下国家。发布成功后的数据库公开投影清理由发布事务负责，生成器自身不删除或改写数据库的观察账本、公开投影或脏标记。

生产生成必须通过事务式发布入口 `src.services.data_release.pipeline.execute_release_task` 调用。该入口在运行生成器之前备份 `astro-site/src/data`、`astro-site/public/site-data`、`astro-site/dist` 与 `exports/site-downloads`；生成或下载失败时连同后续构建、部署、验证失败一样恢复整组本地产物，包括旧 `meta.json` 与已删除的退出国家文件。因此重试仍能从一致的旧快照识别待撤下范围。独立运行生成脚本本身不提供这层整组回滚。最终测试命令包含 `test_failed_release_restores_generated_files_including_deletions`，验证失败后旧文件恢复、新文件清除。

站点导出入口已移除 WPP 自动导入。测试拦截 `scripts.import_wpp_population` 的导入，一旦触发即失败；导出准备函数只调用迁移后数据库的 readiness 验证。人口导入保留为独立、显式的数据操作，不能由增量发布隐式触发。数据库 readiness 的只读实现由本次并行集成一并收紧；测试验证缺 schema 时不执行 DDL 或 seed，完整 schema 时仅查询国家数。

## 测试命令与结果

最终源码冻结后执行：**170 passed，40.52 秒**。

```bash
venv/bin/python -m pytest -q \
  tests/unit/test_site_export_readiness.py \
  tests/unit/test_site_incremental_generation.py \
  tests/unit/test_site_data_orchestration.py \
  tests/unit/test_direct_download_files.py \
  tests/unit/test_site_data_writer.py \
  tests/unit/test_site_data_queries.py \
  tests/unit/test_site_data_views.py \
  tests/unit/test_release_dirty_selection.py \
  tests/unit/test_public_series_store.py \
  tests/unit/test_data_release_service.py \
  tests/unit/test_data_release_pipeline.py
```

静态检查结果：`All checks passed!`。

```bash
venv/bin/ruff check \
  src/generation/site_data_export.py \
  src/generation/site_data_database.py \
  src/generation/direct_download_files.py \
  tests/unit/test_site_export_readiness.py \
  tests/unit/test_site_incremental_generation.py \
  tests/unit/test_direct_download_files.py \
  --select F
```

## 合成性能对照及限制

基准使用临时目录中的 **2 个国家（US、JP）、2 个疾病（D001、D002）**。US 有 D001，JP 有 D001 和 D002；每个国家—疾病序列切片的数组扩展为 **20,000 个元素**。这些是合成重复点，用于观察曲线反序列化及投影开销，不是 20,000 个真实报告日期。完整国家和疾病快照合计 7,455,230 字节。

对照在相同隔离输入下运行仓库 HEAD 中的旧 `collect_site_export_context` 与当前实现，参数均为 `incremental_site=True`、`incremental_country_codes=[]`。数据库元数据使用测试替身；无生产数据库、真实来源或模型请求。Python 峰值内存用 `tracemalloc` 测量，不等同于进程 RSS。

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 完整曲线 JSON 读取次数 | 4 | 0 |
| 读取曲线 JSON 字节数 | 7,455,230 B | 0 B |
| 国家浏览器投影次数 | 2 | 0 |
| 疾病浏览器投影次数 | 2 | 0 |
| 进程 CPU 时间 | 0.3803 秒 | 0.0135 秒 |
| 墙钟时间 | 0.4619 秒 | 0.0135 秒 |
| Python 分配峰值 | 18,812,872 B | 82,041 B |

本基准只衡量上述小型合成快照的增量上下文生成。它不包含真实数据库查询、知识质量计算写入、Astro 构建、下载仓库推送或 Cloudflare 部署，不能用作生产全库耗时或生产环境加速倍数。

本机会话的复现命令及原始证据：

```bash
PYTHONPATH=. venv/bin/python /tmp/globalid_incremental_generation_perf.py
```

- `/tmp/globalid_incremental_generation_perf.py`：使用隔离测试快照、仓库 HEAD 函数源码与当前实现进行对照。
- `/tmp/globalid_incremental_generation_perf_20261030.json`：原始计数与性能结果。
- `/tmp/globalid_generation_verification_20261030.json`：测试命令、结果、修改范围与指纹说明。

以上 `/tmp` 文件为本机会话证据，本文件保留其关键结果和限制；它们不是生产发布产物。
