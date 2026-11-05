# 大型脚本审计与拆分基线

统计日期：2026-11-05（Asia/Shanghai）。本页记录本轮拆分**之前**的基线，源码版本为 `3fb0ddb3571b763ae9d3f7843cb7eb80201b762b`。这里的行数包含空行和注释，仅用于发现需要审阅的模块，不是质量评分，也不是要求所有文件低于某个阈值。

## 统计口径

统计 Git 已跟踪的生产源码文件：`src/`、`dashboard/api/`、`dashboard/src/`、`astro-site/src/`、`astro-site/scripts/`、`scripts/`、`cloudflare/` 和 `shared/` 下的 Python、TypeScript、JavaScript、Astro 和 Shell 文件。排除测试目录、`.test.`/`.spec.` 文件、声明文件、前端生成的 API 类型、`astro-site/src/data/` 数据以及 charts vendor 目录。未跟踪文件、虚拟环境、依赖、构建结果、数据和日志不计入。

基线共 **1,109 个文件、268,811 行**；达到 500、1,000、1,500、2,000、3,000 行的文件分别为 **161、49、19、9、1** 个。数据目录中的手写文件也按目录统一排除，因此这些数字是上述生产代码口径的规模，不是整个仓库所有代码的规模。

下列命令从指定 Git 提交创建临时只读统计样本，不受当前工作区后续修改影响：

```bash
revision=3fb0ddb3571b763ae9d3f7843cb7eb80201b762b
audit_dir=$(mktemp -d)
git archive "$revision" | tar -x -C "$audit_dir"
git ls-tree -r -z --name-only "$revision" > "$audit_dir/paths.nul"
python3 - "$audit_dir" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = (root / 'paths.nul').read_bytes().split(b'\0')
extensions = {'.py', '.ts', '.tsx', '.js', '.mjs', '.astro', '.sh'}
roots = ('src/', 'dashboard/api/', 'dashboard/src/', 'astro-site/src/',
         'astro-site/scripts/', 'scripts/', 'cloudflare/', 'shared/')
excluded = ('dashboard/src/generated/', 'astro-site/src/data/',
            'astro-site/src/components/charts/vendor/')
rows = []
for raw in paths:
    if not raw:
        continue
    path = Path(raw.decode())
    name = str(path)
    if (path.suffix not in extensions or not name.startswith(roots)
            or name.startswith(excluded) or '.test.' in name or '.spec.' in name
            or any(part in {'test', 'tests', '__tests__'} for part in path.parts)
            or name.endswith('.d.ts')):
        continue
    try:
        with (root / path).open(encoding='utf-8') as stream:
            rows.append((sum(1 for _ in stream), name))
    except (OSError, UnicodeError):
        continue
print(f'files={len(rows)} lines={sum(n for n, _ in rows)}')
print({limit: sum(n >= limit for n, _ in rows)
       for limit in (500, 1000, 1500, 2000, 3000)})
for count, name in sorted(rows, reverse=True):
    print(f'{count:5d} {name}')
PY
rm -rf "$audit_dir"
```

例如 `dashboard/src/generated/api.d.ts` 为生成类型、`astro-site/src/components/charts/vendor/qrcode.js` 为第三方代码，分别有 19,790 和 2,289 行；测试文件 `tests/unit/test_knowledge_briefs.py` 有 4,039 行。这些文件值得分别维护或审计，但不应混入生产脚本排名或直接按生产代码方式拆分。

## 生产源码大文件

以下为基线中不少于 1,500 行的全部文件：

| 行数 | 文件 |
| ---: | --- |
| 4,647 | `src/services/disease_knowledge_service.py` |
| 2,936 | `src/ai/model_center.py` |
| 2,665 | `src/data/storage/series_observation_store.py` |
| 2,590 | `src/generation/site_data_literature.py` |
| 2,261 | `src/knowledge/sources.py` |
| 2,210 | `src/services/data_release_service.py` |
| 2,196 | `scripts/sync_disease_ontology.py` |
| 2,026 | `src/services/ai_content_governance_service.py` |
| 2,002 | `src/generation/site_data_export.py` |
| 1,992 | `src/services/disease_mapping_registry_service.py` |
| 1,971 | `src/data/crawlers/ca.py` |
| 1,856 | `dashboard/src/features/ai/models/view.tsx` |
| 1,676 | `src/generation/direct_download_files.py` |
| 1,660 | `src/services/agent_workflow_service.py` |
| 1,651 | `src/knowledge/llm_brief_generator.py` |
| 1,605 | `src/data/crawlers/au.py` |
| 1,580 | `src/data/processors/is_history.py` |
| 1,559 | `dashboard/api/routers/sources.py` |
| 1,531 | `dashboard/api/routers/ai.py` |

## 本轮拆分

- 下载入口从 1,676 行减至 1,121 行；观测行、时间窗口、格式编码、疾病分区合并和 worker 限额分别放入 `src/generation/direct_download/`。原入口继续导出既有调用符号，所有新模块都纳入下载投影指纹。
- 发布流水线从 925 行减至 650 行；构建与本地校验、仓库与 R2 同步准备、Pages 部署及恢复检查点分别放入 `src/services/data_release/` 的阶段模块。
- 文献导出入口从 2,590 行减至 1,928 行；可视化计算和仅含元数据的论文完整性提醒分别放入 `literature_visualizations.py` 和 `literature_integrity.py`，原导入入口仍可使用。

这些是按职责整理，而不是通过删除业务逻辑减少总代码量。下载投影源码及指纹规则改变，后续受影响的数据集会重新评估；内容未变的分区仍按原有规则复用。尚未处理的其他大文件仍需按下列顺序逐步拆分。

## 优先级与拆分边界

1. **下载与发布路径：优先。** `direct_download_files.py` 将观测行构造、序列化、分区、增量合并和进程池调度放在同一文件；`build_direct_download_files` 单函数约 426 行。可按稳定职责拆出行投影、分区/编码、合并和调度，同时保留原导入入口。相关回归包括 `tests/unit/test_direct_download_files.py`、`test_download_country_merge.py`、`test_site_incremental_generation.py` 和 `test_site_data_orchestration.py`。`src/services/data_release/pipeline.py` 虽为 925 行，但承担真实部署、检查点和多个外部通道，也值得按构建、同步、部署等阶段拆分；相关回归为 `test_data_release_pipeline.py` 和 `test_data_release_resilience.py`。
2. **网站数据与文献导出：优先。** `site_data_literature.py` 包含约 655 行的 `collect_literature_export`，同时处理公开投影、可视化和产物输出。适合先提取输入输出明确的纯计算/可视化模块，再逐步拆数据库读取、公共投影和产物写入。`site_data_export.py` 的 `collect_site_export_context` 约 744 行，适合以后按国家数据收集、全局上下文和增量写出梳理；须确保范围选择和发布确认保持一致。文献相关测试包括 `tests/unit/test_literature_radar.py`。
3. **知识和模型服务：随后分阶段处理。** `disease_knowledge_service.py` 的服务类约 2,474 行，其中 `update_disease` 约 755 行；证据门禁、来源刷新、双语修复和数据库事务交织，不宜机械按行切分。`model_center.py` 同时承担路由健康、限流、并发准入、配置和探测，可从可独立测试的状态规则逐步提取。相关知识测试包括 `test_knowledge_briefs.py`、`test_knowledge_stage_boundaries.py`；模型中心在多个测试文件中有覆盖，需保留路由和限流语义。
4. **存储、爬虫和页面：按风险单独推进。** `series_observation_store.py` 同时包含写入、修订、质量和完整性判断，拆分时应保留数据库事务、锁、批处理和兼容性；加拿大、澳大利亚等爬虫应按数据源协议和时间语义拆，不按地理代码长度切割。前端大页面应按状态、数据访问和可复用展示组件划分，避免只把 JSX 搬到新文件。

## 兼容和验证重点

- 下载分区的投影指纹在基线只读取原模块和 `disease_series_policy` 源文件。若影响下载内容的代码迁出，必须同步覆盖新模块，确保后续修改能正确失效缓存；指纹算法或源码变化也可能引起一次缓存失效。回归应覆盖旧清单、增量范围、未变分区复用、来源字段、损坏分区失败和中断恢复。
- 下载测试会对原模块的 `ProcessPoolExecutor`、`_xlsx_bytes` 和 `_file_sha256` 等名称打补丁；重新导出名称不一定改变新模块函数的全局绑定。进程池还要求任务函数可导入和可序列化，必须测试真实子进程而非只测直接调用。
- 发布重构必须保留阶段顺序、并发同步、重试及检查点、外部副作用与数据库确认之间的关系；不能因为导入整理而提前确认待发布标记。控制面板和旧调用方使用的入口也应兼容。
- 保留文献公共输出和数据库事务行为，避免把目录、JSON 键、排序、时间或调用时的动态依赖当作无关实现细节。对实际抽出的边界运行针对性测试、服务健康检查和适当的发布验证，再决定是否扩大范围。
