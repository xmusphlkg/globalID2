# v0.20.0-rc.6 修复复验：仓库准入与实库查询

2026-11-16。超限 PE fixture 已缩减，未降低 5 MiB 门槛或增加 allowlist。

保留原报告封面及国家汇总证据页，仅移除栅格图像，保留 PDF 矢量表格线与文字。`official_bulletins.zip` 从 8,557,778 缩至 1,320,002 bytes；`2026w13_cover_summary.pdf` 从 5,812,723 缩至 575,350 bytes。新增 README 与 provenance，分别记录原档/派生文件摘要及原页号映射；原件保留在 Git 忽略的本地数据目录。`/output/` 明确排除本地生成输出。

语义对比证实 1993/2013/2019 三份报告的 8/76/111 条提取记录，其身份、期间、数值、指标、口径、定义和语义维度保持一致。只排除预期变化的文件 SHA 与证据页号，不跳过业务字段。对应脚本和结果保存在本批证据目录。PE 与相关态势交叉回归 **38 passed**。

原审查依赖 PostgreSQL 而跳过的查询验证，现已在随机命名的独立临时数据库执行：`tests/unit/test_site_data_queries.py` **66 passed in 53.79s、0 skipped**。没有修改生产数据表；测试 schema 在 fixture 中删除，临时库也由维护脚本清理。清理状态单独留证。

此前对当时 536 个变更/新增路径的预检查中，没有超限或备份/数据库文件违规；正式分批 staging 后再次执行标准 index guard。整体 clean-tree 与生产构建、公开验收在最终发布报告记录。

## 恢复后的整合验证

恢复验收后，生成层的行政区地图接线、病例/死亡显式配对、人口分母语义、依赖指纹、
导出早期漂移拦截，以及发布计划忽略纯 atime 变化一起接受整合审查。原 unit 和非 unit
首轮失败日志继续保留；没有将它们改记为全绿。两个 US 冻结夹具显式指定其实际携带的
2020 年归档范围；索引测试继续断言 concurrent migration、有效索引、精确计数和
Index Only Scan，将受并行快照影响的 Heap Fetches 记录为诊断事实。

最新针对性命令覆盖 `test_priority_country_updates`、`test_publication_planning`、
`test_release_projection_snapshot`、`test_series_quality_history_projection` 和
`test_surveillance_data_export`：**118 passed / 10.56s**。首次命令误指不存在的
`tests/contract/test_repository_boundaries.py`，只产生 collection error，随后已纠正路径；
该次不计为测试通过。数据库 marker、前端隔离构建及下载成品分别独立验收。
