# 七国官方渠道与动态更新验收

日期：2026-11-16（Asia/Shanghai）。范围：NG、KH、AR、PE、ZA、ID、MX。

## 渠道保存与控制面板

- 持久目录：`configs/surveillance_channels.json`。保留官方机构、官方页、历史目录、实际 discovery URL、HTTP 方法/表单、来源发布周期、修订窗口及调度时区。
- `src/core/source_channels.py` 校验无重复、无凭证的官方 HTTPS 地址；控制面板 API 共用该目录，不另外硬编码七套链接。
- 数据源列表、手动采集表单、自动化计划表单提供“官方更新渠道”，不同历史目录另显示链接。
- `/api/v1/sources/config`、`/api/v1/sources/flow?country_code=XX`、`/api/v1/schedules/ingestion/jobs` 已通过实际认证接口核验。保存完整回执：`exports/priority-country-updates-20261116/live-source-config.json`。
- 实际浏览器逐一打开七国数据源页面，七个链接与目录完全一致，页面脚本错误为 0：`exports/priority-country-updates-20261116/browser-channel-smoke.json`；南非页面截图同目录 `panel-za-official-channel.png`。

## 每日更新规则

每日检查目录中的新发布，并重放近期报告/目录年份的修订。数据仍使用来源原本的周、月、季、年周期；每日检查不产生每日病例数。目录每次重新发现。AR/ZA/MX 日常报告缓存最多复用 1 天，PE 最近两年的下载缓存最多复用 1 小时；NG/KH/ID 实时请求官方原件。缓存保留原始抓取时间，不冒充本次新下载，过期原件请求失败进入审计。下载原件、组织、入库、质量检查、失败记录均使用现有分阶段采集管线。

| 国家 | 持久 job_id | 当地每日检查时间 | 时区 | 默认修订窗口 |
| --- | --- | --- | --- | --- |
| NG | `ng-ncdc-surveillance-daily` | 09:10 | Africa/Lagos | 最近 2 个日历年 |
| KH | `kh-kh-camewarn-weekly-daily` | 09:25 | Asia/Phnom_Penh | 最近 2 个日历年 |
| AR | `ar-ar-ben-weekly-daily` | 09:40 | America/Argentina/Buenos_Aires | 最近 2 个日历年 |
| PE | `pe-pe-cdc-weekly-daily` | 09:55 | America/Lima | 最近 2 个日历年 |
| ZA | `za-za-nicd-nmc-daily` | 10:10 | Africa/Johannesburg | 最近 2 个日历年 |
| ID | `id-id-kemkes-profile-daily` | 10:25 | Asia/Jakarta | 最近 3 个日历年 |
| MX | `mx-mx-dge-annual-daily` | 10:40 | America/Mexico_City | 最近 3 个日历年 |

七个任务已实际写入数据库且启用，均有 `next_run_at`。回执：`exports/priority-country-updates-20261116/automation-applied.json`。统一保留原始文件，处理入库，不补零，不启用公开发布，失败重试阈值 3。再次执行配置脚本保持幂等，并保留运营人员的暂停状态；只有显式 `--enable` 才恢复暂停。

API 的既有字段 `revision_window_months` 对这七个来源按 `revision_window_unit=years` 解读，控制面板按年显示。指定 `start_year` 或 `force` 的历史操作保留其显式范围。未指定起始年份的计划任务按实际当前年份计算窗口，不固定在 2026 年。窗口用于官方报告/目录的年份筛选；印尼年报中明确标注的更早统计年份仍按原年保留，不伪装为报告年或截断。

秘鲁持久历史 supervisor 继续运行。常规更新使用独立下载缓存 `data/cache/pe/routine-downloads` 和解析缓存 `data/cache/pe/routine-parses`，避免与历史下载的 `.part` 和缓存临时文件相互干扰；解析器代码和 supervisor 的已加载指纹保持一致。

配置/核验命令：

```bash
venv/bin/python scripts/configure_priority_country_updates.py --apply \
  --receipt exports/priority-country-updates-20261116/automation-applied.json
venv/bin/python scripts/verify_priority_country_updates.py --require-data
```

## 实际数据核验

| 国家 | 本轮数据库核验 | 范围限制 |
| --- | --- | --- |
| NG | 首批 15,128 条源事实 | 原始下载与复杂表格仍有已记录缺口 |
| KH | 首批 2,775 条，含 1,000 条明确未知 | 原始档案全保存；图表未知值、未核验文件及未发布年份保持未知 |
| AR | 首批 7,326 条，含 128 条明确缺失 | 无受支持数值表的公报与冲突单元格在 source audit 中保留 |
| PE | 历史 supervisor 持续分批入库，接口验收时 2,586 条 | 1,661 个 PDF 的历史回填未完成，不能称全量已下载 |
| ZA | 17,013 条逐条只读对账通过；16,774 有值、239 明确未知；9,106 个明确零 | 39 PDF + 1 DOCX 全部归档；数值提取仍部分覆盖；全国/9 省、月报/季报分别保留 |
| ID | 881 条逐条只读对账通过；879 有值、2 明确缺失；9 个明确零 | 2017–2025 九册全归档；数值提取仍部分覆盖，正文与附表口径独立保留 |
| MX | 224,601 条逐条对账通过：212,971 有值、459 明确缺失、11,171 不适用，43,682 类别/序列、179 原件 | 1984–2025 的 176 PDF 全归档；已解析事实全部入库，复杂页面仍有审计缺口，数值提取仍部分覆盖 |

ZA：`data/current/za/database_import_receipt.json`、`database_acceptance.json`；44 个 artifacts（40 份原报告，加目录/计划证据），17,013 条数据库事实与 CSV 逐条一致。

ID：`data/current/id/database_import_receipt.json`、`database_acceptance.json`；23 个 artifacts（九册原报告及官方目录/下载解析链证据），881 条数据库事实与 CSV 逐条一致。

MX 最终回执：`data/current/mx/database_import_receipt.json`、`database_acceptance_independent.json`，224,601 个 source cell identity、数值、原件证据、缺失 metadata 和 holding 私有口径逐条匹配，差异均为 0。第一阶段 213,430 条的历史回执独立保留在 `database_numeric_import_receipt.json` 和 `database_acceptance_numeric_phase.json`。

共用存储层及数据库迁移 `0033_not_applicable_observations` 已支持显式 N.A. 空值：`source_missing` 与 `not_applicable` 都可保留 null，metadata 保持各自原始状态；无状态的普通空白仍不创建事实，明确零仍为零。修复覆盖已注册及 holding 路径。约束升级是旧规则的逻辑超集，使用 `NOT VALID` 避免锁表扫描全档案，仍逐条检查新写入。最终重放使用 SHA-256 校验的冻结 CSV、原件 manifest 和已完成 organize 审计，属于离线回放。超过 32,767 个序列的历史读取已按批查询，保留原事务和完整质量检查。

## 验证与发布状态

- 七国渠道、计划配置、窗口选择、API 源配置及存储回归：98 passed；秘鲁下载/解析缓存隔离和近期窗口回归 55 passed；数据库模型同步后存储回归 54 passed；此前完整首批国家+存储回归 182 passed。
- MX 真实样本、解析及日更缓存回归 32 passed，ID 23 passed，ZA 17 passed。
- 控制面板渠道链接及手动采集交互 5 passed；lint、生产 build 均通过。
- 10:07:48 CST 运行验收通过：worker PID 666560 active/running，scheduler PID 666561 active/running；`/health/ready` HTTP 200、status ok，DB、worker、ai-worker、scheduler 均 ok。7 个计划均 enabled 且 next_run_at 已排定。AR/ID/KH/ZA 真实近期检查 completed/healthy；MX/NG/PE 首次每日计划尚未运行，保留 idle/never_run，不冒称已执行。回执 `runtime-ready.json` status passed、errors=[]。原有大事务自然提交后正常恢复，未终止其他国家任务。临时排空宽限文件已于 10:07:26 CST 由专用 watcher 自动移除，回执 `runtime-grace-restored.json`。
- 七国事实仍在内部 source-native holding；`public_release_enabled=false`。公开 `/countries/` 清单和地图继续保留已发布的 Scheduled 状态，待标准疾病映射及真实公开快照验收后统一转为 Supported。

## 实时近期窗口检查

- ID：`eae6c9de-27c7-4fa6-b2fc-a96a29d5ddc7`，按保存的计划读取实时目录与 2024–2025 两册已发布年报，67 条事实完成入库，quality issues=0。
- KH：`68267a21-3451-41a4-ac6e-7e6db99eaadf`，按最近两年窗口完成实时目录/原件检查及 210 条事实入库，quality issues=0。
- AR：`0eebfb08-dce8-4f3c-912d-0b246c85e994`，近期窗口 6,498 条完成入库，quality issues=0；旧归档 TLS 失败保留在 source audit。
- ZA：`83801886-2f94-452d-a966-dc1f57da4d44`，近期窗口 5,965 条完成入库，quality issues=0；Q1 原件实时 TLS 失败审计保留，之前核验的历史事实未被删去。随后以当日完整原档及原始抓取时间预热管线配置的 24 小时校验缓存，回执 `za-verified-cache-warm.json`。
- 以上检查使用保存的 automation job 选项、同一 CrawlService、fenced foreground lease 和正常 collect/organize/store 阶段；其来源是人工发起的计划验收，不冒充 scheduler 自动触发。逐次回执保存在 `exports/priority-country-updates-20261116/*-live-publication-check.json`。

## 最终数据库验收（10:24 CST）

- 数据库迁移 `0033_not_applicable_observations` 已成功应用；原在途大事务自然提交，其他国家任务未被终止。
- MX 完整回放任务 `1a8e0ae4-806d-439e-aeb8-1c825b2e6053` 的 collect、organize、store 均 completed，224,601 条已解析事实全部入库，包含 11,171 个独立 not_applicable 状态。
- 独立 `REPEATABLE READ, READ ONLY` 对账已通过：179 个原件、43,682 个源类别及 holding 序列符合合同；缺少/额外身份、数值及溯源差异、缺失状态 metadata 差异均为 0。回执 `data/current/mx/database_acceptance_independent.json` status passed。
- 全量回放走正常原件登记、组织和存储阶段及 country mutation lock，保持质量检查；与第一阶段串行。原始 PDF 的复杂页面解析缺口继续保留在审计中。
- 七国最终状态及 API 复核：`exports/priority-country-updates-20261116/finalization-state.json`、`live-source-config.json`。秘鲁历史 supervisor 独立继续。
