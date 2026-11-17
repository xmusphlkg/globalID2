# 剩余区域国家数据接入与刷新验收

日期：2026-11-16。验收状态：**通过**。18 个国家和地区、21 条来源渠道、11,024 条观测均已正式入库，并完成实际定时刷新及刷新后的原档重放／逐字段数据库核验。

本轮覆盖既有候选中剩余的 18 个国家和地区，另补三个独立专项源。国家年度基线、国家主来源、区域专项来源分别保存，不把重叠指标相加。所有新增事实进入源原生 holding 系列，公开发布仍关闭；这份记录不等于全部国家疾病、全部历史公报或最新周报均已覆盖。

| 区域／来源 | 国家和地区 | 已验证的观测范围 |
| --- | --- | --- |
| 非洲 WHO 年度基线 | 埃塞俄比亚 ET、肯尼亚 KE、乌干达 UG、加纳 GH、坦桑尼亚 TZ | 2,605 条 |
| 东南亚 WHO 年度基线 | 菲律宾 PH、越南 VN、老挝 LA、缅甸 MM、文莱 BN、东帝汶 TL | 2,823 条 |
| 拉丁美洲 WHO 年度基线 | 巴拉圭 PY、厄瓜多尔 EC、哥斯达黎加 CR、巴拿马 PA | 1,832 条 |
| SPF ODISSÉ 五套年度数据 | 瓜德罗普 GP、马提尼克 MQ、法属圭亚那 GF | 3,589 条；年龄、血清群和定义版本独立 |
| WHO WPRO 登革热专项 | 老挝 LA、越南 VN | 分别 18、7 条；当前公开目录六期，周病例和累计快照独立 |
| 哥斯达黎加卫生部专项 | CR | 388 份目录 PDF 全部归档；完整合同为 150 条，其中 64 条年度、86 条累计快照；身份迁移后 150／150 精确对账通过 |

WHO 基线共 7,260 条：6,109 条有数值、1,151 条明确未知；实际报告年份最早为 1950，最新闭合年度为 2025，各指标的起止年份与缺口不同。SPF 三地共 3,589 条，实际年份范围为 1998–2025。以上条数是独立观测数，不能当作病例合计。

## 刷新机制

- 21 条新增渠道已登记在 `configs/surveillance_channels.json`，控制面板、手动采集和持久定时任务共用来源身份、实际发现入口及来源策略。
- 每日按国家时区错峰检查，`process=true`、`save_raw=true`、重试阈值 3、不填补缺值。年度基线每次重新读取完整公开历史，更新旧年修订；CR 重新发现完整目录，原档缓存验证 SHA256／长度，24 小时到期条件重验证；WPRO 重读当前真实目录及链接原档。
- 保留采集、组织、入库三个持久阶段，任务租约和国家并发锁参与执行。定时验收同时检查 country、source、automation job ID，避免把国家去重返回的其他来源任务误算成功。
- 源行仅按稳定疾病类别、日期、地域、定义、度量与统计口径更新；暂定标记和来源证据不应制造新观测身份。当前抓取遗漏的旧历史保留，不自动删除；缺周、抑制或未知值不补零。
- 渠道配置脚本限定本轮国家，保留其他国家的既有频率及操作员暂停。再次预览本轮 21 条任务全部为 unchanged。

## 实际运行证据

- [年度任务真实调度验收](../../reports/onboarding/remaining_surveillance_runtime.json)：18 个渠道的定时任务及 collect／organize／store 均完成，保留下一次运行时间。
- [专项任务真实调度验收](../../reports/onboarding/remaining_extra_surveillance_runtime.json)：CR 与 LA/VN 三个专项均通过。CR 任务 `017ce96a-5d34-4b43-be45-1dc6e8d3eb71` 于 2026-11-16 04:43:41 UTC 完成，422 个原档、150 条观测；LA 18 条、VN 7 条，三阶段全部完成。
- [刷新后原档重放及数据库逐字段对账](../../reports/onboarding/remaining_refresh_data_acceptance.json)：最终 **21／21 来源、11,024 条观测全部通过**。校验原始文件哈希，在禁止 HTTP、数据库只读事务下重放实际任务原档，比较本批每个源字段、计数、日期及地域；缺失、多余、重复、字段差异及数值／地域异常均为零。本次额外保留历史为零；工具仍单独统计后续当前目录滚动后保留的旧历史，不冒充本批重新验证。
- [控制面板在线检查](../../reports/onboarding/remaining_live_panel.json)：实际认证 API 检查来源选项、修订策略、21 条启用任务和已入库观测。
- [定时配置幂等性](../../reports/onboarding/remaining_jobs_idempotence.json)：21 条本轮任务再次预览均未改变。
- [CR 身份差异审计](../../reports/onboarding/cr_identity_migration_audit.json)：旧 64 条与规范身份记录对应同一原档、同一日期／标签／数值，初次全量验收拒绝了重复记录；原始失败任务保留审计。
- [CR 身份迁移凭证](../../reports/onboarding/cr_identity_migration_receipt.json)：完整备份后仅清理已证明同源同值的 64 条旧副本，五个旧类别退役，失败任务原状保留。再次应用为幂等 no-op；214 条修订账本的内容／ID 哈希不变，原 64 条观测仍有逐条修订证据。
- [CR 完整修复后精确对账](../../reports/onboarding/cr_primary_reconciliation_acceptance.json)：重放全部 422 个原档，150 条源事实与数据库严格一致，缺失、多余、重复及字段差异均为零。

[服务重载记录](../../reports/onboarding/remaining_service_reload.json)：API、scheduler 已加载本轮配置，general worker 新 PID 1127858，服务均正常。旧 worker 停止领取新任务后等待已有任务结束，12 个既有采集全部完成；当时正在执行的站点发布另收到 “Cancelled by user” 取消请求，因此不将该发布记为成功。AI worker、CO/PE 前台回填未触碰。本轮 CR 最终定时任务在新进程恢复后才启动并成功。

此前接入的波多黎各 US-PR、美属维尔京群岛 US-VI 继续沿用美国 NNDSS 每 30 分钟父级任务，未新建重复任务。[只读核验](../../reports/onboarding/us_caribbean_existing_coverage.json)确认父级定时入库成功。PR 最新数值日期为 2026-11-07；VI 最新数值为 2026-06-20，其后的未知记录不能作为零病例。网站公开快照与内部数据刷新独立，核验时公开快照仍有滞后。

荷属加勒比 BQ 已补齐 Country 与 canonical scope，维持 NL RIVM 源归属；20 条年度观测不变，不启用 BQ 独立采集或公开发布。NL 原有四个 30 分钟任务已错峰，防止国家去重持续跳过年度源。[实际父级刷新验收](../../reports/onboarding/nl_bq_scheduled_refresh.json)验证了正确年度任务、三阶段、原始 HTML 哈希及全部 20 条 BQ 数据。

## 验证与复验

最终定向测试 **250 项通过**，见 `reports/onboarding/remaining_surveillance_final_tests.log`；覆盖全部新增来源、国家派发、修订／保留历史、BQ 父级和错峰、以及既有 WHO／国家接入回归。前端类型检查通过，见 `remaining_dashboard_types.log`。前台导入的真实 PostgreSQL 并发及租约验证见 `foreground_import_atomicity_regression.json`；该回归只使用临时隔离 schema。

```bash
# 默认只读；检查实际任务及下一次调度。
venv/bin/python scripts/commission_remaining_surveillance.py --require-complete
venv/bin/python scripts/commission_remaining_surveillance.py --extras --require-complete \
  --receipt reports/onboarding/remaining_extra_surveillance_runtime.json

# 禁止网络的原档重放和逐字段数据库核验。
venv/bin/python scripts/verify_remaining_refresh_data.py
```

## 覆盖边界

WHO 的 18 个已审核年度指标是国家报告计数基线，不能替代完整国家法定传染病目录，也不能宣称当前周已覆盖。SPF 合同限定五套年度数据。WPRO 限定当前目录六期及可验证的明确区间，早期 IRIS 历史和图表点仍未覆盖。CR 原档完整归档，但数值解析仅覆盖已审核全国年度表与登革热／基孔肯雅累计表；311 份拒绝、4 份布局不支持等原因逐报告记录，不猜测数值。

巴拿马国家公报 PDF CDN 自重定向、巴拉圭／厄瓜多尔源站 TLS，以及部分非洲国家缺少已核验连续主来源等问题见[国家来源访问审计](../data-sources/REMAINING_NATIONAL_SOURCE_AUDIT.md)。这些国家已有本轮 WHO 年度基线，未创建空壳国家周报任务。既有 CO、PE 全历史回填仍按各自持久任务继续，本轮不重置其进度或启用尚未验收的 CO 日更任务。
