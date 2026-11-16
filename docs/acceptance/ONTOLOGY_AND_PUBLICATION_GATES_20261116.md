# 注册表、旧表访问及公开发布门禁补充验收

日期：2026-11-16，Asia/Shanghai。独立子代理补充检查；没有提交、部署、启用来源或写生产数据。生产检查使用 `REPEATABLE READ, READ ONLY` 或 `READ ONLY` 事务，只读取配置、注册元数据、发布标记和公开 read model；没有扫描完整观察事实表。

## 注册表结构及稳定身份

`configs/disease_ontology.json` 的巨大文本 diff 不能等同于新增十二万条业务定义。以 `git HEAD` 为基线，按 ID 比较每个对象的完整内容，并分别验证原始 JSON 与正式 loader 模板展开结果：

| 对象 | HEAD 原始数量 | 初查候选 | 末次捕获候选 |
| --- | ---: | ---: | ---: |
| facets | 10 | 10 | 10 |
| concepts | 216 | 216 | 216 |
| groups | 23 | 23 | 23 |
| relations | 46 | 46 | 46 |
| sources | 62 | 83 | 104 |
| source_series | 2630 | 2690 | 2690 |
| availability | 2632 | 2692 | 2692 |

所有既有对象逐 ID 完整内容比较，**零删除、零修改**；原有注册身份和语义没有被此次重排改写。新增 60 条全部是省级正式月报死亡指标，配套新增 60 条 availability。严格 JSON 解析拒绝重复 object key；正式 loader 验证 ID 唯一、来源/目标存在、日期和枚举有效、无自动汇总以及 availability 与 series 的来源/目标一致。

必须在正式 loader 展开模板后检查身份：省级月报的 49 个基础病例定义由 datacenter 词汇模板展开，不能把原始 JSON 中缺少显式对象误报为悬空配对。展开后 HEAD 为 2761 条 series，候选为 2821 条；既有展开定义同样零删除、零修改。全部 60 条死亡指标的 `paired_case_series_code` 存在，且 source、concept/group、frequency、definition version、validity、reporting basis 和 time basis 均匹配对应病例；没有把报告月死亡配给 onset 数据。60 条均为 `source_observations_only`、`no_auto_rollup`、`missing_is_unknown`，聚合指标保留 `reported_aggregate`。

初查的 21 个新增来源与对应 updater 的 source ID / country 一一匹配，均保持 `public_release_enabled=false`、required registry coverage。后续 18 个 WHO / SPF 来源与 `remaining_country_sources.source_contract` 对应。没有显式静态 series 的新来源会经过 source-category 发现后建立 `disease_id=NULL`、unmapped、not_comparable、non_additive 的 holding series；注册来源不代表自动成为标准疾病或公开支持。

正式 DB payload 可生成：77 taxonomy nodes、55 edges、702 concept assignments、46 relations、2821 surveillance series、2823 availability。相关针对性验证为 **60 passed, 28.86s**：`test_disease_ontology.py`、`test_disease_ontology_models.py`、`test_disease_ontology_sync_service.py`、`test_disease_ontology_export.py`、`test_cn_province_deaths.py`。

本轮没有发现 rc.5 初审保存了该配置文件的历史指纹，因此不把当时测试结果宣称为本次最终配置的逐对象认证。本次重新计算并记录了文件指纹及基线差异。

## 外部并行写入及结论边界

审查期间发现两次外部修改，均已即时报告主执行者：

- 初查：83 sources，SHA-256 `ab5ea4800dd988086ad532def8a3351eb56ab82c31a6ecc8f780bf093d038c32`。
- 中间候选：101 sources，SHA-256 `7b5684a964ff8e08fb7c8fed9967735a7532c6d659fa9680c85a4c0bd46bd308`。
- 12:00:39 捕获：104 sources，SHA-256 `63497c98a7f6abfd4d10f53fc9397e6c8b7ba190c1df15f91ab49e483da72456`。

最后增加的三个来源为 `SRC_CR_MOH_NOTIFICATIONS`、`SRC_LA_WHO_WPRO_DENGUE`、`SRC_VN_WHO_WPRO_DENGUE`。最后候选通过本次结构与稳定身份对比，但这三个新 connector 的业务逻辑和端到端准入不在已完成的 101 来源对应检查范围内。**本报告不是对继续变化的工作树的发布认证。** 机器证据：`/tmp/globalid-fixes-20261116/ontology-final-audit.json`。HEAD 配置指纹为 `84e918a2f2a28e78113dd9282704a5fe33418c4efb8e45b9737873ff0989e2cf`。

## 旧事实表访问门禁修复

完整 unit 新失败来自 `scripts/verify_za_nicd_nmc.py`：新增直接查询旧事实表，为南非全国家计数并断言无旧投影，超出已审白名单。没有扩大 baseline，也没有把相同 SQL 伪装转移到新 helper。

现移除脚本的旧表直接查询和相应认证字段，显式输出 `legacy_projection_checked=false`；保留原件 hash、逐 source-cell identity/value/provenance、动态 unmapped holding、非公开标志及源序列契约的全部校验。不能再把该脚本的通过结果解释为额外执行了全国家旧表投影检查。`test_legacy_disease_access_guard.py` 与 `test_za_nicd_nmc.py` 合计 **22 passed, 23.44s**。本次没有运行该 verifier 对生产验收文件做重写。

## 生产 blocked_pending 解释

原监测列出的 13 个 scope 中，AR、CL、CO、ID、KH、MW、MX、MY、NG、PE、TH、ZA 共 12 个国家均存在且 active，bootstrap 明确 `public_release_enabled=false`；这 12 国的 `public_disease_series` 和 `country_publish_state` 均没有记录。实际 source series 全部是 holding、`disease_id=NULL`，不是已经验收的标准疾病公开曲线。配置注释及既有接入验收明确要求保留内部采集，等待来源复用/映射/汇总范围/公开快照验收；CO 虽有 CC BY-SA 4.0，仍因地理完整性和 national rollup 待审而保持关闭，MY 同样还需按来源区分指标与公开快照。

BQ 的机制不同：数据库没有 BQ country 注册，bootstrap 也没有显式 false；其 dirty id 129520 来自已完成的 NL crawl `9bb612a6-f3d2-46cd-a3c5-e1eaf31814b0`，包含两个 `HOLD_NL_*` 来源序列在 `country:BQ:national` 的 2016–2025 年共 20 项 upsert。它属于 RIVM 加勒比单列地域事实，尚未配置可发布辖区及来源准入；不能为清空 warning 直接建立 active BQ 或绕过门禁。现有规划表也仍将 BQ 列为待扩展管线覆盖。

11:58:32 UTC+8 的本次实际快照（UTC 03:58:32）已因外部采集增加到 **104 blocked / 25 scopes**，另有 ET、GF、GH、GP、KE、LA、MM、MQ、PH、TZ、UG、VN；可发布积压仍为 **206 / 191 scopes**。blocked 代表公开发布保护，不能将其当作采集停止或用解除全部门禁的方式修复可发布积压。监测 `classify_pending`、JSON `validate_data_selection` 和公开 `fetch_countries` 使用相同 country active + bootstrap gate，未发现这 12 国的配置漂移。公开 website 代码发布与来源事实准入是不同边界；现有发布事务仅确认选中的可发布 dirty IDs，受保护来源继续待审。

机器证据：`/tmp/globalid-fixes-20261116/blocked-scopes-readonly.json`。本次未更改生产 gate、country、read model 或 dirty acknowledgement。

## 迁移核验

源码 Alembic 唯一 head 为 `0033_not_applicable_observations`，生产 `alembic_version` 同为 0033。相对 HEAD 的 ORM 变化只有 Situation 四个宿主字段和观察 missing-value check，分别由 0032 / 0033 覆盖；生产已实际具备四个宿主字段及允许 `source_missing` / `not_applicable` 的 check。新增来源、死亡指标、动态 holding 使用既有注册/事实表，无需新增 DDL migration；数据注册不应被误作数据库 schema 迁移。此结论限本轮已审的 schema 差异。
