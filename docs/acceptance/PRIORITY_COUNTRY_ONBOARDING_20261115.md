# 四国历史档案接入与国家目录同步验收

日期：2026-11-15。范围：非洲、东南亚、拉丁美洲候选计划；NG、KH、AR、PE 首批源原生接入；公开 `/countries/` 列表与地图。

## 数据核验

原始文件、已组织记录、数据库事实和公开标准疾病数据分别统计；完成源原生入库不会自动把国家标记为公开已支持。

| 来源 | 官方档案 | 数据库验收 |
| --- | --- | --- |
| KH CamEWARN | 全部 39 个链接文件，展开 401 个 PDF，共 412 个原始 artifacts | 2,775 条：1,775 个精确值，1,000 条 `source_missing`；没有未注明缺失的 NULL |
| AR BEN | 现行目录 199 个 PDF，加索引与计划共 205 个 artifacts | 7,326 条：7,198 个精确值，128 条明确缺失 |
| PE CDC | 完整目录 1,661 个独立 PDF URL；已处理 94 份，9 个不可变批次已应用 | 验收时 2,365 条精确指标，全部保留原始文档 URL/SHA 维度；DB 与 94 份原档逐项重解析结果一致 |
| NG NCDC | 1,690 个发现 PDF，1,645 份成功归档；45 份及 8 个目录入口重试后仍不可用 | 15,128 条精确事实、177 条 holding 序列；全国范围、SRC_NG_NCDC、累计／本期维度均通过核验 |

KH 的 24 个未通过解析的文件和 958 个仅图表未知值分别审计；矩阵空单元另外 42 个。AR 的 116 份未支持 ENO 全国总表、13 个复杂单元格及旧归档 TLS 故障均保留原档／证据。NG 的 420 份未完成数值提取、64 个同报告期冲突已明确审计，不称为完整数值覆盖。

PE 目前仍有 1,567 份待归档文件。后台控制器使用进程锁、HTTP Range、原子校验缓存、不可变批次、串行标准导入与失败监督重试；最终状态区分 `partial` 和 `complete`。源目录的两组同周多版本必须到齐后共同组织，无证据顺序的冲突组隔离，不以下载顺序覆盖。

NG 的 4,138 个零均为源明确给出的值；无负值、非整数、NaN 或未标记空值。质量模式保留 fail_closed，只记录两条稀疏时期 gap warning；没有标准疾病 legacy 投影。独立数据库验收：`data/current/ng/database_acceptance.json`。

## 原始档与回执

- NG：`data/raw/ng/ncdc_full_history/artifacts_manifest.json`
- KH：`data/raw/kh/camewarn-history/20261115T150348Z/manifest.json`
- AR：`data/raw/ar/ben_full_history/artifacts_manifest.json`
- PE：`data/raw/pe/cdc-history/<目录 SHA>/batch-<批次 SHA>/manifest.json`
- 标准入库回执：`data/current/<国家代码>/database_import_receipt.json`；完整阶段审计在数据库 `run_stages` 和各批次 `database_import.log`。
- PE 全任务：`data/current/pe/history-state.json`、`history-controller.log`、`source_audit.json`；续跑：`venv/bin/python scripts/imports/pe_cdc_history.py --apply --supervise`。
- PE 文档身份迁移回执：`data/current/pe/document_identity_migration_receipt.json`；46 条旧身份按原件 URL/SHA 精确修复，重复执行零修改，当前 CSV 同步。

2026-11-16 的 PE 恢复验收修复了 2026W13 的多行合并单元格误识别，以及 W17 死亡事件使用未经支持列合同的问题。精确纠正 5 条 current 事实，保留原档、修订历史、纠错回执和原 Task 的历史计数；当前 CSV／审计有效数同步。94 份原件集中重解析与数据库的 identity／value 对比均为零差异。相关证据：`data/current/pe/w17_death_contract_correction_receipt.json`、`parser_repair_validation.json`。缓存改用已加载源码指纹及版本化封装，控制器阻止 CSV／数据库混用解析版本；失败采用有上限的指数退避。

修正了共享 Registry 选择器丢弃“未映射但明确 source_missing”记录的问题；没有把缺失改为零。新的全档导入工具在数据库变更前验证原档路径、大小、SHA，使用标准 immutable ArtifactWriter、collect→organize→store、任务租约心跳和源原生 holding 存储；不擅自创建标准疾病映射。

## 国家目录与地图

25 个新增国家和地区写入统一覆盖目录及[完整计划](../data-sources/AFRICA_SE_ASIA_LATAM_PLAN.md)。目录与地图共用同一名单；真实公开快照覆盖计划状态。波多黎各和美属维尔京群岛沿用现有 `US-PR`、`US-VI`，并合并已有公开数据，避免重复的未支持标记。

新条目的 WHO 区域、世界银行区域及收入组按官方来源核对。印度尼西亚按 WHO 国家办公室当前信息归 WPR。BQ、GP、MQ、GF 未在世界银行 API 独立列示，区域／收入组保留空值。US-PR／US-VI 使用地区自身分类，不套用美国本土世界银行区域。

主站使用隔离干净工作树，仅包含 5 个本任务 Astro 文件，复用已发布的数据快照；正常 template/code_only plan→task→receipt 发布，禁用订阅派送。任务级保留 Research runtime，验证当前绑定、目录分片及引用对象，避免旧 Papers publisher 回推数据指针。正式发布已完成：`20261115T153901Z-3b616bec180f`，任务 `9427770b-812b-5a19-a5c1-6469617fd283`；公网验证和数据库确认均通过，完整 baseline 已安全同步回主工作区。最终回执：`exports/countries-release-20261115/final-publication-receipt.json`。

2026-11-16（Asia/Shanghai）收尾复验：apex 与 www 的 `/release.json` 均返回上述版本；线上四国卡片、4 个 Planned 地图点、波多黎各／美属维尔京群岛已有支持状态均正确。线上 `?q=Nigeria` 的列表与地图仅含 NG，国旗无加载错误，控制台无 error/warn。

Surveillance/Situation 指针字节保持原值；Literature 指针被本次发布完成之后的正常 data_only 任务 `e3b84cc8-d82d-4bab-97d2-565be2271f6c` 更新。收尾保留这一后续版本，没有回滚，不声称三个指针在跨夜后仍全部未变。

## 检查

- 四国来源／历史接入／存储回归：初轮 171 项通过。
- PE 最终修复回归：20 项通过，包含真实 2026W13/W17、严格表头合同、缓存版本与退避行为。
- 最终 NG no-op import hook 修正及来源回归：41 项通过。
- 覆盖目录／分类／地图坐标／旗帜检查：12 项通过。
- Astro check：0 errors、0 warnings。
- 隔离完整构建：源文件、翻译、地图 ISO、SEO、性能预算及 Pages Worker 打包通过；EN/ZH/FR 本地页面已验证四国卡片与公开地区数据；四国地图点均为 Planned，搜索 Nigeria 后列表及地图仅剩 NG。
