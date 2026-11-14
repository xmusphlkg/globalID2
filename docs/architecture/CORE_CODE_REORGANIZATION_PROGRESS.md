# 核心代码拆分实施记录

用户已授权按方案分批实施，并允许修正不合理代码。兼容入口、数据合同、事务、任务租约与发布收据保持有效。

源码在 `/home/likangguo/globalID/worktrees/gids-0.19.2*` 隔离工作区施工；原生产目录保留，旧的失败发布任务不重新执行。此前数据更新页、JSON 版本记录、统一 header/footer 和地图隐藏 footer 的改动纳入交接。

交接基线：`8ea11ca345e0b6e473b84018cae79d38f4592291` 加用户与前序任务的现有未提交改动。逐文件 SHA 清单保存在隔离目录的 `exports/refactor-0.19.2/handoff.json`，未复制凭据、数据库、真实发布候选。

49个候选均已完成本轮职责拆分并集成；具体边界如下。未机械拆分需要共同持有事务或锁的长编排。

## 候选落实

| 原文件 | 状态 | 实施与验证 |
| --- | --- | --- |
| `astro-site/src/components/charts/DiseaseDetailView.tsx` | 已集成 | 图表/目录/报告组件提取，69 图表+6 主题测试及3语浏览器验证通过 |
| `astro-site/src/components/charts/ReportV4Panel.tsx` | 已集成 | 图表/目录/报告组件提取，69 图表+6 主题测试及3语浏览器验证通过 |
| `astro-site/src/components/public/PublicDownloads.astro` | 已集成 | 图表/下载/订阅职责拆分；三语浏览器与Astro检查通过，统一回归中 |
| `astro-site/src/components/research/ResearchHotspotCharts.tsx` | 已集成 | 图表/下载/订阅职责拆分；三语浏览器与Astro检查通过，统一回归中 |
| `astro-site/src/components/situation/SituationExplorer.tsx` | 已集成 | 状态/时间线/检索合同提取，52+25 单测、6 浏览器测试通过 |
| `astro-site/src/lib/research-ask.ts` | 已集成 | 状态/时间线/检索合同提取，52+25 单测、6 浏览器测试通过 |
| `astro-site/src/pages/subscribe.astro` | 已集成 | 图表/下载/订阅职责拆分；三语浏览器与Astro检查通过，统一回归中 |
| `cloudflare/subscriptions/src/handlers/campaigns.ts` | 已集成 | 活动/投递/令牌/队列职责分离；169项mock回归通过，SQL/claim/ledger/unknown与路由保持 |
| `cloudflare/subscriptions/src/handlers/situation-alerts.ts` | 已集成 | 活动/投递/令牌/队列职责分离；169项mock回归通过，SQL/claim/ledger/unknown与路由保持 |
| `cloudflare/subscriptions/src/index.ts` | 已集成 | 活动/投递/令牌/队列职责分离；169项mock回归通过，SQL/claim/ledger/unknown与路由保持 |
| `dashboard/api/routers/ai.py` | 已集成 | 拆分请求响应合同与源流程查询；OpenAPI 逐字节相同，19 项相关回归通过 |
| `dashboard/api/routers/sources.py` | 已集成 | 拆分请求响应合同与源流程查询；OpenAPI 逐字节相同，19 项相关回归通过 |
| `dashboard/src/components/tasks/TaskDetailPanel.tsx` | 已集成 | 提取状态/显示/组件边界，lint 与 17 项相关测试通过 |
| `dashboard/src/features/ai/interactions/view.tsx` | 已集成 | 提取状态/显示/组件边界，lint 与 17 项相关测试通过 |
| `dashboard/src/features/ai/models/view.tsx` | 已集成 | 按视图/表单/导出/引文职责拆分；主树类型检查与39文件167测试通过 |
| `dashboard/src/features/data/overview/view.tsx` | 已集成 | 按视图/表单/导出/引文职责拆分；主树类型检查与39文件167测试通过 |
| `dashboard/src/features/operations/automation/view.tsx` | 已集成 | 源策略/表单/流程组件分离；类型检查与8项相关测试通过 |
| `dashboard/src/features/operations/sources/flow/view.tsx` | 已集成 | 源策略/表单/流程组件分离；类型检查与8项相关测试通过 |
| `dashboard/src/features/reports/list/view.tsx` | 已集成 | 按视图/表单/导出/引文职责拆分；主树类型检查与39文件167测试通过 |
| `scripts/audit_iceland_integration.py` | 已集成 | 只读审计与源行解释独立；17 项相关回归通过 |
| `scripts/benchmark_ai_literature.py` | 已集成 | 只读源快照与指标提取；CLI帮助检查通过，新增只读连接/异常释放测试通过 |
| `scripts/full_rebuild_database.py` | 已实现 | 历史行转换归 database_rebuild_import；批写/提交仍由原编排管理，修复 NNDSS 常量遗漏，25 项测试通过 |
| `scripts/publish_download_repo.py` | 已集成 | 领域资产/传输/收据模块；原脚本兼容包装，45 项测试通过 |
| `scripts/publish_raw_git_archive.py` | 已集成 | 领域资产/传输/收据模块；原脚本兼容包装，45 项测试通过 |
| `scripts/rebuild_source_series_components.py` | 已集成 | 只读审计与源行解释独立；17 项相关回归通过 |
| `scripts/sync_disease_ontology.py` | 已实现 | 提取 sync_contracts / sync_planning；dry-run JSON 逐字节相同，相关测试通过 |
| `src/data/crawlers/au.py` | 已集成 | 纯查询与工作簿解析独立；50 项回归通过，补充静态检查遗漏 |
| `src/data/crawlers/ca.py` | 已集成 | 源序列写入转换、Ontario解析提取；181 项回归通过 |
| `src/data/crawlers/cn_provinces.py` | 已集成 | 省级明确归一化/PHSM历史审计提取；45项回归通过 |
| `src/data/processors/is_history.py` | 已集成 | 纯查询与工作簿解析独立；50 项回归通过，补充静态检查遗漏 |
| `src/data/storage/series_observation_store.py` | 已集成 | 视图、身份与质量规则提取；133 项回归通过 |
| `src/generation/direct_download_files.py` | 已集成 | 分区/区域组装职责独立；依赖指纹同步，110 项测试含真实 spawn 与三格式字节比较 |
| `src/generation/site_data_export.py` | 已集成 | 分区/区域组装职责独立；依赖指纹同步，110 项测试含真实 spawn 与三格式字节比较 |
| `src/generation/site_data_literature.py` | 已集成 | 查询与文献导出按职责提取；181 passed / 15 skipped |
| `src/generation/site_data_queries.py` | 已集成 | 查询与文献导出按职责提取；181 passed / 15 skipped |
| `src/generation/site_data_views.py` | 已集成 | 视图、身份与质量规则提取；133 项回归通过 |
| `src/knowledge/llm_brief_generator.py` | 已实现 | 纯提示/结果或网页解析独立；原方法分派与共享状态保留，166 项测试通过 |
| `src/knowledge/sources.py` | 已实现 | 纯提示/结果或网页解析独立；原方法分派与共享状态保留，166 项测试通过 |
| `src/literature/enrichment.py` | 已集成 | 纯合并/摘要/周报合同独立；157 项相关回归通过 |
| `src/literature/pipeline.py` | 已集成 | 纯合并/摘要/周报合同独立；157 项相关回归通过 |
| `src/literature/weekly_ai_review.py` | 已集成 | 纯合并/摘要/周报合同独立；157 项相关回归通过 |
| `src/services/ai_content_governance_service.py` | 已实现 | 共享纯修复策略归 knowledge/repair_policy；模型/锁/提交不迁移，186 项前后回归通过 |
| `src/services/crawl_service.py` | 已集成 | 源序列写入转换、Ontario解析提取；181 项回归通过 |
| `src/services/disease_knowledge_service.py` | 已实现 | 共享纯修复策略归 knowledge/repair_policy；模型/锁/提交不迁移，186 项前后回归通过 |
| `src/services/disease_mapping_ai_service.py` | 已集成 | 语义保护与报告复用规则独立；71 项相关回归通过 |
| `src/services/disease_mapping_quality_service.py` | 已集成 | 纯输入/身份/自动化决策和查询独立；137 项相关回归通过 |
| `src/services/disease_mapping_registry_service.py` | 已集成 | 纯输入/身份/自动化决策和查询独立；137 项相关回归通过 |
| `src/services/literature_automation_service.py` | 已集成 | 纯输入/身份/自动化决策和查询独立；137 项相关回归通过 |
| `src/services/report_service.py` | 已集成 | 语义保护与报告复用规则独立；71 项相关回归通过 |

## 同步问题修复

- Compare 筛选布局及下级行政单位选择：已集成，5 项单元测试、15 项浏览器回归通过；360/768/1280 与中英法均覆盖。
- 中英法地理标签统一为准确中性用词：48 个文件已集成，96 项相关测试及 Astro 检查通过；保留代码/API/URL 与旧搜索别名。
- 版本与数据记录继续使用独立 JSON 更新，不随页面构建重算版本。
- 发布 checkpoint 错误区分源码、配置、基线及文件盘点变化；昂贵构建前先检查冻结条件。
- `.gitignore` 覆盖 Vite 缓存、隔离工作目录与依赖软链接；测试源码与必要夹具仍需跟踪。

## 验证与发布

- 主集成树完整 Python 首轮：4,092 passed、21 skipped、7 failed；6个阶段顺序测试的替身未创建必需发布产物，1个测试沿用进程已加载的AU漏导入版本。补齐真实产物合同夹具、集成导入修复后，相关165项重测全部通过，未放宽发布门禁。
- OpenAPI：159个路径、208个模型与现有JSON及TypeScript生成合同一致。
- Dashboard：主树类型检查与39个测试文件、167项测试全部通过。
- 独立复核：13个新Python模块可导入、137项运行时类型解析通过；58项针对性测试通过。
- 全局Python未定义名称检查通过；既有legacy disease access门禁通过。
- Astro主树：493文件0错误/0警告，224项单测及三语门禁通过。
- Workers主树：169项测试、类型、生成bindings与生产配置隔离检查通过，Wrangler dry-run打包通过。
- 主树浏览器验收：66个独立用例通过（核心42、Compare12、订阅12），覆盖360/768/1280与中英法。首轮5个失败为Astro开发计时属性/工具栏干扰；仅规范化HTML开发计时属性、关闭测试工具栏后，6项相关重查通过，JSON对象摘要仍逐字节检查。
- 六张手机/桌面截图已留存。开发夹具与生产数据分离，所有生成数据保存在忽略路径。


各批次在隔离工作区完成相关测试后集成。最终记录版本、完整验证、Git 提交与推送结果；生产发布继续使用 plan → task → receipt，未取得真实回执不得记为上线。
