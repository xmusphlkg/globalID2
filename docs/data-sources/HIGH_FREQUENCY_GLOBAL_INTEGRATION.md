# WHO FluNet、加拿大 PHAC 与法属美洲周度来源接入

核验时间：2026-11-17。此文记录实际源抓取、离线整理和待合并注册表补丁；正式入库、映射发布及网站数据发布的结果由主任务另行记录。

## 覆盖范围及实际结果

已实际抓取并离线整理 24 个国家/属地来源范围，共 632,178 条标准化观测，其中 307,542 条为源站明确给出的数值，其余保留为缺失/抑制状态。缺失值不补零。

| 来源 | 国家/属地 | 实际获取范围 |
| --- | --- | --- |
| WHO FluNet | AR、BN、CL、CR、EC、ET、GH、ID、KE、LA、MM、MX、MY、PA、PH、PY、TL、TZ、UG、VN | 全部分页的已结束周记录及检测字段字典 |
| PHAC respiratory virus detections | CA | 当前滚动 CSV 中 Canada/Canada 全国记录，100 条源记录、300 条观测；不声称已覆盖完整历史归档 |
| SPF ODISSÉ SurSaUD | GP、MQ、GF | 流感样疾病、毛细支气管炎及 COVID-19 三个区域数据集的全部已发布已结束周 |

GB 和 BQ 的本次 FluNet 查询未取得数据，因此未根据空响应宣称已覆盖。

实际抓取清单保存在：

- `data/raw/frequency-expansion/20261117T012534Z/<country>/<scope>/artifacts_manifest.json`
- `data/raw/frequency-expansion/20261117T012830Z-flunet/<country>/who_flunet_weekly/artifacts_manifest.json`

同名日期的第一批并行采集曾共用汇总回执路径，应以各国家各来源的独立清单为准。后续采集 ID 已包含来源名。标准化 CSV 路径及观测计数在各清单中。

## 来源口径

WHO FluNet 使用 `SRC_<CC>_WHO_FLUNET`，保留 sentinel、nonsentinel、notdefined 三种实验室监测来源。25 个检测字段分别保留，流感总数和亚型不得相加，检测数不是患者发病数。通用定义版本为 `who_flunet_detections_v1`；MY 保留原 `my_who_flunet_detections_v1` 及已有源身份。输入国家、ISO 周、周起止、监测来源、重复键、整数检测数及缺失/抑制状态均验证。

加拿大来源为 [PHAC respiratory virus surveillance](https://health-infobase.canada.ca/respiratory-virus-surveillance/)，实际 CSV 为 `https://health-infobase.canada.ca/src/data/respiratory-virus-detections/RVD_WeeklyData.csv`。仅使用源站明确的全国记录，排除省级行。每种病毒分别保存检测阳性数、检测数和阳性率，单位分别为 count、count、percent。使用 PHAC 的周六结束周历，不重新标注为 ISO 周。来源身份为 `SRC_CA_PHAC_RESPIRATORY`，定义为 `ca_phac_respiratory_weekly_v1`。

SPF 使用以下数据集：

- `grippe-passages-urgences-et-actes-sos-medecin_reg`
- `bronchiolite-passages-aux-urgences-et-actes-sos-medecins-region`
- `covid-19-passages-aux-urgences-et-actes-sos-medecins-region`

区域代码 01、02、03 分别对应 GP、MQ、GF。保留年龄、数据集和医疗活动指标维度。每十万急诊活动/医疗行为比率是医疗服务地点的活动指标，不是居民病例数。住院字段描述的分母与叙述性元数据存在矛盾：保留原字段单位与两种描述，并明确记录 `field_passages_vs_narrative_admissions_disagree`，不自行换算。元数据实际标示 Licence ouverte 2.0。定义版本为 `spf_odisse_sursaud_weekly_v1`。

## 本体及映射补丁

由 `scripts/build_high_frequency_global_ontology.py` 从上述实际标准化记录生成：

- `configs/high_frequency_global_ontology.json`：23 个新 source、622 个 source series、622 个 availability 条目。
- `configs/onboarding/high_frequency_global_mappings.json`：647 条原始类别的审阅映射，包含 25 条 MY 映射。

合并方式为按稳定 ID 合并。补丁已与主本体在内存合并并通过 `DiseaseOntology.from_dict` 校验。availability 在正式存储验收前维持 `upstream_available_ingestion_pending`。

**MY 不添加任何新静态 series**：其事实已经存在于 holding series，应发布新的映射版本连接原事实，避免重建系列造成历史观测重复。原 MY CSV 不由本脚本覆盖。

流感实验室类别只映射到一般流感 D038，RSV 映射 D142，SARS-CoV-2 映射 D004；均为 narrower / conditional / non_additive。H5 检测类别不等于 H5N1，不映射 D014。加拿大未有安全概念对应的其他病毒、SPF 的流感样疾病和毛细支气管炎保留为 holding 类别。COVID-19 医疗活动指标可以关联 D004，但保留 rate 指标和原单位。

`src/services/global_high_frequency_review.py` 对公开导出进行二次来源口径验证，读取实际查询的 `series_unit` 和 `observation_unit`，验证来源、定义、国家、类别、指标、单位和维度。所有这些类型的合格记录固定使用 `source_observations_only`；不允许病例数曲线投影或删除原维度。

## 接口及验证

Crawler / updater / ingestion connector 分别位于 `src/data/crawlers/`、`src/data/processors/`、`src/services/ingestion/countries/`，模块名为 `who_flunet`、`ca_respiratory_weekly`、`spf_odisse_weekly`。

通用 FluNet 和 SPF updater 需要 `country_code`；CA updater 固定为加拿大。三者均保持 `mapping_notifications_enabled=False`。`collect` 抓取原始文件，`organize` 仅使用原始文件离线重放，校验 SHA-256、完整分页链及元数据一致性。

验证命令：

```bash
venv/bin/python -m pytest -q tests/unit/test_frequency_expansion_sources.py tests/unit/test_my_who_sources.py
```

结果：59 passed。测试覆盖真实源样本、全国与省级区分、原始口径、零/缺失/抑制区分、国家与周历错误、重复/截断输入、原始文件篡改、离线重放、MY 身份保留、单位与映射污染和维度保留。
