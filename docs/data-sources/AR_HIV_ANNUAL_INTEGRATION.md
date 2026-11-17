# 阿根廷卫生部 HIV 年度通知数据

来源合同审查日期：2026-11-16。此来源直接使用卫生部原件给出的全国年度 HIV 通知数，与 BEN 累计快照独立；不通过省级求和、性别相加或累计差分生成全国曲线。接入与验收结果在本文末尾记录，部署状态单独列明。

## 来源与指标

- 来源 ID：`SRC_AR_MOH_HIV_ANNUAL`；scope：`ar_moh_hiv_annual`。
- 原生代码：`hiv`；定义：`ar_moh_hiv_annual_notifications_v1`；映射：`D162`（HIV）。
- 指标：`case_notifications`；报告口径：`annual_hiv_notifications`；频率：`annual`。
- [官方目录](https://datos.gob.ar/api/3/action/package_show?id=ed365e37-bac7-580f-911e-17c90ef73324)，卫生部原始数据集 ID：`1f70534d-f5f0-41f7-bc9a-b9809c39a505`。
- 对照原件：[CSV](https://datos.salud.gob.ar/dataset/1f70534d-f5f0-41f7-bc9a-b9809c39a505/resource/747d2078-9d51-40d4-ae59-ced43225459f/download/casos_prov_vih_2014-2023_1.csv)、[XLSX](https://datos.salud.gob.ar/dataset/1f70534d-f5f0-41f7-bc9a-b9809c39a505/resource/cdc2fdf3-9637-4fbc-9cf0-d05bdaa00e3f/download/casos_prov_vih_2014-2023_3.xlsx)。
- 目录资源级许可标记为 `CC-BY-4.0`，署名 `Ministerio de Salud de la Nación Argentina`；保留原始来源、许可链接及变更说明。本展示不暗示发布机构背书。

该指标表示来源报告的年度 HIV 通知数，不能解释为年度新感染人数、患病存量、感染发生率或 AIDS。资源覆盖 2014–2023 年，尚无该资源的 2024–2026 年数值；不得将其称为实时周监测。目录顶层旧时间文字不覆盖所选资源的实际年份。

## 精确选择与时间合同

CSV 是 CP850 编码、分号分隔。列名依次为 `id_sexo;sexo;id_jurisdiccion;jurisdiccion;anio;casos_vih`，原件内容中的地理名称与编码列名容易误解：实际 `id_jurisdiccion=ARG`、`jurisdiccion=200`，不得凭列名猜测交换。

仅选 `id_sexo=3`、`sexo=Ambos sexos`、`id_jurisdiccion=ARG`、`jurisdiccion=200` 的来源原始行；各省、男／女分项不加入全国行。CSV 与 XLSX 的全部 750 行应逐字段一致，每个全国年份恰有一行。来源缺失值保留未知，明确零值保留零，不以缺行填零。

年度事实以 `YYYY-01-01` 为存储锚点，原始期间保持 `PeriodStart=YYYY-01-01`、`PeriodEnd=YYYY-12-31`。它不是 1 月 1 日当天发生的病例。`GeographyKey=country:AR:national`；原始 dimensions 精确保留：

```json
{
  "dataset": "casos_prov_vih",
  "sex": "all",
  "source_sex_id": "3",
  "source_sex_label": "Ambos sexos",
  "source_jurisdiction_label": "ARG",
  "source_jurisdiction_code": "200",
  "date_basis": "calendar_year"
}
```

本轮核实的 10 个全国年度数：2014 年 6,654；2015 年 6,747；2016 年 6,375；2017 年 5,218；2018 年 6,027；2019 年 6,311；2020 年 3,991；2021 年 5,772；2022 年 6,168；2023 年 6,588。死亡未由此资源提供，保持未知。

## 采集、原件与修订

采集应保留官方目录响应、CSV、XLSX 及各自 URL／SHA-256；组织阶段使用经过哈希验证的原件，核对 CSV／XLSX 一致后再选取全国行，不在组织阶段重新请求上游。入库使用既有 collect → organize → store 流程，保留正常任务、阶段和原件收据。

年度资料可历史修订。相同来源、原生代码、定义、年度、地域与维度的更新覆盖该自然键的当前值，并保留修订台账；重复导入不得新增第二份当前观测。缺少年份不授权删除旧记录或改成零。BEN 的累计通知、临床及实验室事实继续保留独立身份，不能因新源进入全国曲线而被重写或相加。

卫生部站点本轮遗漏中间证书，详见[替代来源调查的 TLS 证据](AR_ALTERNATIVE_SOURCES_20261116.md#下载tls-与可复核证据)。应补全可验证的中间证书链并锚定现有受信任根，保持主机名和 TLS 证书校验；不得通过关闭证书校验完成采集。

```bash
# 获取官方目录、CSV、XLSX、已核实中间证书及采集计划；不写数据库。
venv/bin/python scripts/imports/collect_ar_hiv.py

# 从 source_audit 记录的不可变 raw manifest 重放，不访问上游。
venv/bin/python scripts/imports/collect_ar_hiv.py --offline-organize

# 默认只预览；正式入库需加 --apply，使用同一完整 raw manifest。
venv/bin/python scripts/imports/import_surveillance_archive.py --country AR \
  --source ar_moh_hiv_annual --start-year 2014 \
  --manifest data/raw/ar/hiv_annual/20261116T084630514981Z/artifacts_manifest.json \
  --receipt-path data/current/ar/hiv_annual_import_receipt.json
```

`data/current/ar/hiv_annual/artifacts_manifest.json` 是便于查阅的副本；正式导入的 `--manifest` 应使用 `source_audit.json` 中记录的 `data/raw/ar/` 不可变路径。新一轮采集生成新的路径，不能把上例时间戳当作通用最新版。

## 独立复核

`scripts/verify_ar_hiv_annual.py` 直接读取 CSV／XLSX，不调用摄取解析器，以单独实现核对全部源行、10 个全国数、源身份、年度日期、完整维度、物理数据库值、重复、当前修订台账及任务原件哈希。数据库阶段使用只读可重复读事务，不改任何事实或门禁。

```bash
venv/bin/python scripts/verify_ar_hiv_annual.py --source-only \
  --output reports/onboarding/ar-hiv-20261116/source-crosscheck.json

venv/bin/python scripts/verify_ar_hiv_annual.py --task-uuid TASK_UUID \
  --output reports/onboarding/ar-hiv-20261116/database-acceptance.json

venv/bin/python scripts/verify_unpublished_surveillance.py --country AR \
  --output reports/onboarding/ar-hiv-20261116
```

原件存储位置不同时，显式传 `--csv`、`--xlsx`。验证脚本针对本次已核实的 2014–2023 资源；原件发生修订时，应先审查新版本，再更新基准，而非静默跳过差异。

## 接入验收状态

本轮实际采集保留 5 个原件工件，并经正常三阶段流程提交 10 个全国年度事实，任务 `cb92baa1-da8e-4156-8c39-24e62c97fae3`、crawl run `32317`。独立只读复核通过：CSV／XLSX 全 750 行一致；10 个年度源值与数据库物理值、日期、源定义、全国／全性别维度、原 CSV 行号及原字段一致；10 条当前修订台账与当前观测一致；5 个任务工件大小与 SHA-256 均通过。没有缺失、多余或重复观测。

审核映射已正式提交并激活为 `3948 / DMR-UNPUBLISHED-20261116T084926038595Z`，只增加此新源的 1 个类别，保留 11,073 条范围外断言；回滚演练收据独立保留。AR 常规公开导出只允许此 HIV 来源，`D162` 快照含 10 个年度点，年度通知数合计 59,851，最近报告年度 2023 年为 6,588，死亡全部未知。BEN 的 7,326 行存量事实全内容指纹不变；原 31 主体范围现有 30 个允许导出，CO 维持关闭。

真实冻结快照在隔离目录完整构建 118 页，样式、翻译、SEO 和性能门禁全部通过；最大路由资源 393,271 gzip 字节，低于既有 395,000 字节预算。AR 英文、中文手机、法文共 3／3 页面通过，核对 9 次 summary／full／source-series 响应哈希、10 个原始值及年度日期、仅 HIV、死亡未知、来源署名与 CC BY 4.0 三语说明、表格及手机无横向溢出。源快照和 462 个前端／共享源码文件与隔离副本一致。

集中回归 222 项通过；后续完整历史刷新任务说明专项 55 项通过，两组有重叠，不累加。末轮仅修正任务说明及其对应测试，不改变投影或快照。首轮旧 channel 期望失败日志保留。

完整证据见[验收索引](../acceptance/evidence/20261116_onboarding/ar-hiv/page-acceptance.json)、[独立原件／数据库复核](../acceptance/evidence/20261116_onboarding/ar-hiv/database-acceptance.json)、[映射事务](../acceptance/evidence/20261116_onboarding/ar-hiv/mapping-applied.json)及[常规公开准入／BEN 不变复核](../acceptance/evidence/20261116_onboarding/ar-hiv/public-country-admission.json)。上述是正式数据接入和本地公开候选验收，**未部署网站、未切换线上公开指针**。
