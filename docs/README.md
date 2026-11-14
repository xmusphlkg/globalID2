# Documentation

Start with [Quick start](../QUICKSTART.md), the [script index](../scripts/README.md)
and the [control-center operator guide](control-center/operator-guide.md).
Current implementation and operational contracts are grouped by topic below.
Release records, audits and acceptance results describe their stated dates;
they do not establish today's queue size, source health or publication pointer.

The independent JSON CDN proposal and old Dashboard redesign plan were retired.
The former v2 mapping guide is consolidated into the v3 ontology's compatibility
section. Small queue, snapshot and frontend notes are merged into their owning
publication guides. Public-site review/closure and production compatibility
records are consolidated with their release evidence. Source review conditions
for Switzerland and Ireland are in the source directory. Removed documents
remain available in Git history; they are not active deployment instructions.

## Architecture and database

- [核心代码拆分与目录整理方案（分析完成，待开始）](architecture/CORE_CODE_REORGANIZATION_PLAN.md)
- [Database architecture](architecture/DATABASE.md)
- [数据线、模型池与控制面重构方案](architecture/DATA_LINE_AND_CONTROL_PLANE_REFACTOR.md)
- [疾病本体与来源系列系统 v3](architecture/DISEASE_ONTOLOGY_V3.md)
- [静态站点与动态数据发布架构](architecture/STATIC_SITE_AND_DYNAMIC_DATA.md)

## Countries and source contracts

- [Add New Country Guide](data-sources/ADD_NEW_COUNTRY_GUIDE.md)
- [Canada Provincial High-Frequency Surveillance Data Plan](data-sources/CA_HIGH_FREQUENCY_SURVEILLANCE_PLAN.md)
- [中国省级法定传染病数据接入](data-sources/CN_PROVINCE_DATA.md)
- [Iceland 数据接入终审核对单](data-sources/ICELAND_INTEGRATION_REVIEW.md)
- [Parser 模块使用文档](data-sources/PARSER.md)
- [Provisional surveillance-data audit](data-sources/PROVISIONAL_SURVEILLANCE_AUDIT.md)
- [Country Surveillance Sources](data-sources/README.md)
- [Situation authority coverage](data-sources/SITUATION_SOURCE_COVERAGE.md)
- [分省/分地区传染病数据接入路线](data-sources/SUBNATIONAL_DATA_ROADMAP.md)

## Control center

- [GIDS Control Center](control-center/README.md)
- [API Guide](control-center/api-guide.md)
- [Architecture](control-center/architecture.md)
- [Deployment and Rollback](control-center/deployment-and-rollback.md)
- [Extension Guide](control-center/extension-guide.md)
- [Operator Guide](control-center/operator-guide.md)
- [Troubleshooting](control-center/troubleshooting.md)

## Development and operations

- [历史数据与兼容映射重建](operations/DATABASE_REBUILD.md)
- [Data versioning and repository boundaries](operations/DATA_VERSIONING.md)
- [Development and local operations](operations/DEVELOPMENT.md)
- [国家数据写入并发](operations/INGESTION_CONCURRENCY.md)
- [Unattended ingestion resilience](operations/INGESTION_RESILIENCE.md)
- [Realtime publication monitoring](operations/REALTIME_DATA_MONITORING.md)
- [Repository size maintenance](operations/REPOSITORY_SIZE_MAINTENANCE.md)
- [Situation automation](operations/SITUATION_AUTOMATION.md)

## Publication and public data

- [Independent surveillance JSON releases](publishing/DATA_EXPORT_INCREMENTAL.md)
- [Data release automatic recovery](publishing/DATA_RELEASE_RESILIENCE.md)
- [Incremental site build](publishing/INCREMENTAL_SITE_BUILD.md)
- [主站分层发布实施记录](publishing/MAIN_SITE_JSON_PUBLICATION_IMPLEMENTATION.md)
- [主站主题 v1 发布与试点](publishing/MAIN_SITE_THEME_PILOT.md)
- [Papers hosting channel (R2)](publishing/PAPERS_HOSTING.md)
- [Papers article identity and subscription publication boundary](publishing/PAPERS_RELEASE_IDENTITY.md)
- [Papers JSON templates and unified research publication](publishing/PAPERS_TEMPLATE_MIGRATION.md)
- [监测投影的耗时与优化](publishing/PROJECTION_PERFORMANCE.md)
- [GlobalID 分块公开数据协议](publishing/PUBLIC_DATA_DOWNLOADS.md)
- [Public data update feed](publishing/PUBLIC_DATA_UPDATES.md)
- [Reports hosting and cadence-based generation](publishing/REPORTS_HOSTING.md)
- [Situation JSON publication](publishing/SITUATION_JSON_PUBLICATION.md)
- [Integrated weekly subscription digest](publishing/SUBSCRIPTION_WEEKLY_DIGEST_DESIGN.md)

## Research and literature

- [缺失摘要修复并重新进入 AI 评审](research/LITERATURE_ABSTRACT_REPAIR.md)
- [Literature source activation](research/LITERATURE_SOURCE_ACTIVATION.md)
- [GIDS Research Radar](research/RESEARCH_RADAR.md)
- [Research Radar health check](research/RESEARCH_RADAR_HEALTH.md)

## Release records

- [2026-11-13 生产部署记录](releases/MAIN_SITE_PRODUCTION_DEPLOYMENT_20261113.md)
- [GIDS project status (2026-11-14)](releases/PROJECT_STATUS_20261114.md)
- [Public site 0.18.2 — 2026-11-11](releases/PUBLIC_SITE_RELEASE_0_18_2_2026-11-11.md)
- [v0.18.0 实时数据发布切换](releases/REALTIME_UPDATE_ROLLOUT_2026-11-07.md)

## Audits

- [AI 模型中心与文献流程架构复核](audits/AI_MODEL_CENTER_ARCHITECTURE_REVIEW_2026-11-10.md)
- [Research Radar completion audit](audits/RESEARCH_RADAR_COMPLETION_AUDIT.md)
- [GIDS 订阅系统审计与重构方案](audits/subscription-audit-2026-11-04.md)

## Acceptance evidence

- [数据线实际运行验收：2026-10-30](acceptance/DATA_LINE_RUNTIME_20261030.md)
- [更新页、数据发布与控制面验收（2026-11-14）](acceptance/DATA_UPDATES_20261114.md)
- [增量生成优化验收：2026-10-30](acceptance/INCREMENTAL_GENERATION_20261030.md)
- [人口数据增量更新验收：2026-10-30](acceptance/POPULATION_INCREMENTAL_20261030.md)

## Incident diagnostics

- [2026-11-06 重启和近 24 小时失败分析](diagnostics/2026-11-06-restart-and-failures.md)
- [2026-11-07 文献同步修复与验证](diagnostics/2026-11-07-literature-sync-repair.md)

## Communication assets

[News assets](news/) retain the public communication material and its images.
Operational evidence under `benchmarks/` is local generated output and is not
versioned with the documentation. Keep required release receipts in the
project's retained evidence store rather than assuming a temporary file is a backup.
