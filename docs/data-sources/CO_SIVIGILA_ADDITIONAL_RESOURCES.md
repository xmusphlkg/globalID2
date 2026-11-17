# INS Sivigila 配套数据与公开文献归档

2026-11-16 从 Sivigila 4.0 门户菜单、公开文档目录及其直接链接的 INS
事件报告页面发现。统一入口为本地 `data/raw/co/INS_ARCHIVE_README.md`，
总文件索引为 `data/raw/co/ins-archive-index.csv`。微观数据原件继续保存在
[`sivigila-microdatos`](CO_SIVIGILA_MICRODATOS_ARCHIVE.md)；本次新增两个目录。

## 门户配套资料

`data/raw/co/sivigila-resources/`：248 个文件，1,915,431,109 字节。
下载和独立核验已完成，状态为 `complete_verified`。

| 类型 | 文件数 | 主要价值 |
| --- | ---: | --- |
| `routine_surveillance` | 54 | 2007–2022 年常规监测病例汇总，以及 EDA、IRA、集体食源性疾病资料 |
| `reference_tables` | 21 | 事件、国家、省市、病原体、检测、职业等编码和关联表 |
| `statistical_operation` | 49 | 事件分析报告、2023–2025 统计技术公报、统计方法与计划 |
| `documents` | 106 | 2026 年 La Guajira／Wayuu、Chocó 报告，质量指标、协议压缩包、会议材料等 |
| `publications` | 13 | 流行病学日历、事件代码对照、指标调整等公开文件 |
| `special_surveillance` | 5 | 烟花伤害等专题监测协议和方法 |

现场检查：`rutinaria_2022.xlsx` 的第一张表有 `COD_EVE`、`SEMANA`、
`ANO`、`COD_DPTO_O`、`COD_MUN_O`、`COD_PAIS_O` 和 `conteo` 等字段，
按事件、周和发生地记录计数。`eventos.xlsx` 含事件代码、名称、通知规则、
版本及状态等 37 列；`municipios.xlsx` 含市级代码、DANE 代码、所属省、
经纬度和状态等。归档不据此将多版本、多来源计数相加。

## INS 事件报告页面关联文献

`data/raw/co/ins-publications/`：完整目录选中 6,185 个公开文件，
8,796,138,988 字节。下载和独立核验已完成，`verification.json` 状态为
`complete_verified`，`manifest.json` 无下载错误。

入口：[Informe de evento y tableros de control](https://www.ins.gov.co/buscador-eventos/Paginas/Info-Evento.aspx)。

| 类型 | 文件数 | 主要价值 |
| --- | ---: | --- |
| `event_reports` | 4,085 | 历史和近期疾病／事件报告，包括 2026 年报告 |
| `weekly_bulletins` | 1,087 | 历年全国流行病学周报，保留目录中的不同修订版本 |
| `surveillance_methods` | 215 | 监测协议、病例报告表、编码与培训资料，含 2026 年版本 |
| `laboratory_surveillance` | 169 | 实验室监测报告、演示文稿及培训资料 |
| `iqen` | 451 | IQEN 流行病学研究刊物 |
| `ren` | 148 | REN 研究刊物，含 2026 年期次 |
| `supporting_documents` | 30 | 监测说明和专题配套文件 |

三个归档共保留 7,655 条官方文件来源记录；同一份 2026 年日历在门户
两个路径收录，本地共用一份原件，因此实际为 7,654 个原件，
20,040,942,797 字节。此次新增实际 6,432 个原件，约 10.71 GB。

上述计数来自公开库逐页枚举后的文件条目。文件夹不作为文件下载；
`Lineamientos` 中的一个 APK 应用安装包和一个 ASPX 交互页面不计入文献
下载计划，选择规则保存在 `discovery/linked-libraries.json`。

## 归档与复查

两个目录均保留：

- `discovery/`：官方菜单、目录及说明页面原始响应，附 URL、时间、大小与 SHA-256。
- `resource-plan.json`：本次目录快照中的完整下载计划。
- `files/<类型>/<内容摘要>/<原始文件名>`：按类别保存的原件，更新内容保留不同版本。
- `manifest.json`：进度、原件索引、验证类型及错误记录。
- `verification.json`：对照计划逐项核对本地文件大小、来源版本与 SHA-256。
- `inventory.csv`、`SHA256SUMS`：全部核验通过后生成，便于查找和离线校验。
- `archive-members.csv`：压缩包内部成员目录，门户资料 72 条，INS 文献 67 条，不展开覆盖原件。

下载强制使用显式 `CO_INS_HTTP_PROXY` 或 `CO_INS_SOCKS_PROXY`，且只访问
官方 INS HTTPS 主机，不在代理失败后切换直连。两个 systemd 服务均设置：

```ini
Wants=globalid-co-ins-proxy.service
After=globalid-co-ins-proxy.service
```

服务为 `globalid-co-sivigila-resources.service` 和
`globalid-co-ins-event-reports.service`。脚本使用八个并发任务，各线程复用
HTTPS 连接，逐文件重试、检查官方字节数和格式后保存。ZIP／XLSX／Office
压缩包检查成员 CRC；RAR 使用本机 `unrar t`，文件名编码不兼容时改用
`lsar -t -j`，要求每个非目录成员测试通过。PDF 首先使用 pdfminer 检查
结构和页对象；若失败，则串行使用 PDFium 尝试恢复读取全部页对象，
明确记录 `pdfium_recovered_structure_ok`，保留官方原始字节，不覆盖修复。
旧 Office、图片和视频分别记录相应格式检查结果。RAR 兼容后端需要
系统软件包 `unar`；PDF 后端使用项目已有的 `pypdfium2`。

其中官方 `MONKEY POX PE V 2026.pdf` 的完整响应与目录长度均为 943,912
字节，但其交叉引用表不完整，pdfminer 无法正常解析，PDFium 可恢复读取
6 页。`VIH Circulares resolucion.rar` 的 853,017 字节原件中两个 PDF
成员均经 `lsar` CRC 检查通过；旧 `unrar-free` 无法解析其中的 Unicode
文件名。这两个文件的诊断原件、来源和哈希另保存在 `source-issues/`。
另有两个库分别收录相同的 2019 年第 011 号通函 PDF（每份 1,953,687
字节），pdfminer 未读出页对象，PDFium 可读取 5 页；同样保留原件并记录
兼容验证方式。这类跨库重复条目按官方来源分别保存，不视为独立病例数据。

重跑服务会按照本次保存的目录快照校验并补齐文件，不会自动重新发现整个
网站。已有原件通过哈希复查后复用，复用进度最多每五秒写入一次清单；
每个新原件或错误仍立即保存清单，避免续传时重复重写数千次完整目录。
更新发布目录时应先保存新的目录版本，再建立新的完整下载计划。

离线复查：

```bash
venv/bin/python scripts/imports/collect_co_sivigila_resources.py --verify-only
venv/bin/python scripts/imports/collect_co_sivigila_resources.py \
  --verify-only --output data/raw/co/ins-publications
# 也可在各归档目录执行：sha256sum -c SHA256SUMS
```

交互式 Power BI 看板和地区站点目录保留来源链接；页面快照不等于看板底层
数据已经离线保存。两个旧 `sivigilareportes.ins.gov.co` 历史日历和发病率
页面在本次通过 INS 代理访问时发生 TLS 错误，其可访问性单独记录。

所有原件保存在受 `/data/` 忽略规则管理的本地目录中。本次操作不导入
数据库、不生成病例观察、不将原始数据或文件发布到网站。

实现验证：`venv/bin/python -m pytest tests/unit/test_co_sivigila_resources.py -q`。
