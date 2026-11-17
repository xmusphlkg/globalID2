# 中国省级法定传染病数据接入

本接入管理 31 个省级行政区来源数据，不改写中国国家级数据。省级行政区使用
ISO 3166-2 风格代码（例如北京 `CN-BJ`），事实使用
`country:CN-BJ:national`；`country:CN:national` 仍只属于中国国家级序列。

## 来源与覆盖规则

- `SRC_CN_PROV_DATACENTER`：公共卫生科学数据中心，按发病月份统计，每年检查
  一次可用年份；原始抓取协议参考 CNIDS 的 `Script/DatacenterReport`，但本项目
  独立实现请求、SpreadsheetML 解析、校验和失败关闭。
- `SRC_CN_PROV_MONTHLY_REPORT`：各省卫生健康主管部门发布的法定传染病月报，
  每周检查新发布月报，并回看最近三个不同的完整报告月份；同月的多个修订链接均保留。
- 两个来源分别保存在 `disease_series_observations`，绝不互相覆盖。生成网站时按
  月进行 `priority_overlay`：数据中心优先级 200，省级月报优先级 100。数据中心
  没有该月份时才使用月报。低优先级原始观测仍保留供下载和审计。
- 空白、缺表、未发布和解析失败都不是零。只有来源明确发布的 `0` 才写入零。

历史工作簿接入 PHSM `ProvinceReport` 中全部 49 个非合计疾病类别；其中
`ProvinceCenter` 当前历史文件实际覆盖 24 类，两个工作表分别写入上述两个来源。
病毒性肝炎总类、各型肝炎以及炭疽/皮肤炭疽各自保留为独立、不可相加的来源序列，
不自动生成父子汇总。`Total` 和甲乙丙类合计行不进入省级数据。重复的相同观测会
去重，重复但值冲突会终止导入。

每次历史预检都会输出逐行审计：源行数、导入数、重复数、空值数、合计行数和未映射
标签数。除明确合计和空值外，任何新疾病或未知省份标签都会失败关闭；各省现行月报
同样不会再静默跳过新病种。

### 省级月报新增病种与定义断点（2026-09-26 核对）

专项搜索各省卫生健康部门现行月报后，已将下列非 49 个数据中心标准标签注册为独立
省级月报系列：人感染新亚型流感（`D016`）、发热伴血小板减少综合征（`D057`）、
基孔肯雅热（`D052`）、中东呼吸综合征（`D049`）、人感染猪链球菌病（`D056`）、
埃博拉出血热（`D050`）、寨卡病毒病（`D051`）、拉沙热（`D053`）、肝吸虫病
（`D055`）和 AFP（`D178`）。猪链球菌限定为人感染；肝吸虫限定为华支睾吸虫感染；
AFP 是监测综合征而非脊灰确诊病例。病例定义以国家卫健委/疾控部门诊断方案或疾病
说明为准，省级事实仍以各省月报原页为数值来源。

定义断点单独编码：新版法定传染病目录自 2025-09-01 将旧人感染高致病性禽流感和
H7N9 类别合并为“人感染新亚型流感”；基孔肯雅热和 SFTS 自 2026-04-01 纳入乙类
传染病管理。此前专项监测值不得自动拼接进新版法定报告序列。各序列按官方发布月份
保留，缺行保持未知，不按零处理。

截至 2026-11-16 的最新历史补采结果及剩余缺月见
[历史补采验收](../acceptance/CN_PROVINCE_HISTORICAL_BACKFILL_20261116.md)；
此前逐省修复和首轮回填见[修复验收](../acceptance/CN_PROVINCE_REPAIR_20261116.md)。
[2026-11-15 审计](../audits/CN_PROVINCE_DATA_AUDIT_20261115.md)保留为修复前快照，
不能作为当前覆盖率。只有明确发布的病种/月/指标可以补入；“最新发现月”、
“最新有数值月”和“最新表格月”分别记录，正文零值不会使全省被误标为完整覆盖。

### 死亡指标

月报明确列出的死亡数使用独立 `SER_CN_PROV_REPORT_DEATHS_*` 系列，
与病例系列通过 `paired_case_series_code` 配对。只在同来源、同地区、同月份、
同定义和同时间口径时附加到病例曲线；缺失死亡列保持未知。死亡数遵循发布者口径，
例如陕西艾滋病死亡为当月报告的既往艾滋病患者全死因死亡，不可解释为死因归属。
图片回填保留原图 SHA、逐行位置、脚注、独立模型读数和分类合计交叉检查，
只补本次核实的月份。定时采集遇到图片或扫描件会保存原件并尝试独立模型投票；
未达到固定多数门槛的月份保持 `needs_review`，不写入未经核验的数值。

## 执行

全新部署时同步 ontology 注册表。已有运行库的定向修复使用 `scripts/register_cn_province_deaths.py`，保留已激活映射断言；
它默认预览，`--apply --receipt PATH` 才提交。完整初始化命令如下：

```bash
venv/bin/python scripts/sync_disease_ontology.py --apply
```

历史数据预检与导入：

```bash
venv/bin/python scripts/update_cn_provinces.py history \
  --workbook /path/to/code_PHSM/data/nation_and_provinces.xlsx
venv/bin/python scripts/update_cn_provinces.py history \
  --workbook /path/to/code_PHSM/data/nation_and_provinces.xlsx --apply
```

数据中心年度检查（当前可用年份由官网端点返回，不假定年份）：

```bash
venv/bin/python scripts/update_cn_provinces.py datacenter
venv/bin/python scripts/update_cn_provinces.py datacenter --year 2020 --apply
```

数据中心默认刷新最新可用年份的全部已结束月份，不只刷新末三个月。
数据中心命令默认使用 6 个受限工作线程；可通过 `--workers 1` 恢复完全串行，或按
上游承载能力在 1–16 范围内调整。每个工作线程使用独立 HTTP 会话，避免跨线程共享
连接状态。

省级月报更新：

```bash
venv/bin/python scripts/update_cn_provinces.py monthly --province CN-LN
venv/bin/python scripts/update_cn_provinces.py monthly --province CN-LN --apply
venv/bin/python scripts/update_cn_provinces.py monthly --all-provinces --apply
```

`--all-provinces` 会刷新全部已配置省份，并逐省输出 `parsed`、`narrative_sparse`、`partial`、
`discovery_pending`、`no_parsed_rows` 或 `failed_closed` 状态。单个官网失败不会阻止
其他省份导入，但失败省份不会用空值或推测值覆盖已有数据。

无 `--apply` 时只抓取、解析并输出覆盖摘要，不写数据库。月报每周逐省检查，
每轮回看最近三个不同的完整报告月份，并从已发现但缺少病例数的历史月份中最多补采三个；
失败月份经过 180 天冷却后可重试。同月的修订链接一起检查。首次运行错峰安排，
避免 31 个省同时请求官网。数据中心共用一次下载任务，按季度检查可用年份，
不创建 31 个重复下载作业。
当前 31 个省级作业中 25 个启用、6 个保持停用；新作业默认停用，
可用 `scripts/configure_cn_province_jobs.py` 预览和配置。来源失败与任务状态分开保留，
健康状态用最新明确病例月判断滞后，死亡数较新的月份不会掩盖病例缺口。
单省旧附件失败不影响其他省份，未知病种、冲突修订和疑似复制上月的整表先隔离，
不降低校验标准。

## 省级网页机制

省份来源不再集中写入一个巨型脚本或 JSON。每个省拥有一个独立适配模块，位于
`src/data/crawlers/cn_province_adapters/`（例如 `shanghai.py`、`xinjiang.py`）；
`registry.py` 只负责显式注册和校验 31 个模块。通用 HTTP、附件归档、Word/PDF/Excel
转换和表格解析继续由 `cn_provinces.py` 复用，避免 31 份重复且难以修复的底层代码。
`configs/cn_province_sources.json` 仅保留两类共享来源参数和疾病映射。

各省适配模块当前声明以下机制：

- `html_table`：辽宁、上海、河南、重庆、四川、浙江；优先解析正文表格，栏目
  历史月份改用 Excel/Word 附件时自动进入附件解析。
- `docx_attachment` / `doc_attachment` / `pdf_attachment`：广东、江苏、湖南、
  安徽等；先归档附件，再按确定格式解析；同栏目临时改为正文表格也能兼容。
- `attachment_auto`：新疆等；从详情页选择受支持附件。优先原发布机关，转载缺附件时回到原始疾控局栏目。
- `narrative_only`：官网没有完整病种表时，只收录文字中明确列出的疾病病例数，
  以及明确写出的无病例病种作为零；不按排名、占比、病种总数或上下文推导其他数值。
- `discovery_pending`：尚未确认稳定现行栏目或格式的省份，失败关闭，不使用搜索
  摘要或未经核验的 OCR 数值。

旧版 `.doc` 先用 `antiword` 读取表格；表格不完整或 WPS 文件不兼容时，再用
LibreOffice Writer 转为 `.docx`。转换在一次性临时目录和独立 LibreOffice 配置中
执行，单次上限 60 秒，只读取表格，不执行宏；两种工具都不可用或转换失败时明确
失败，不会静默改用正文数字。Debian/Ubuntu 部署依赖：

```bash
apt-get install --no-install-recommends antiword libreoffice-writer-nogui
```

维护某省时只修改对应省份模块；只有页面结构确实特殊时才在该模块增加定制钩子，
并把可复用的文档/表格能力下沉到共享解析器。

每省模块可配置官方主入口、历史入口、同栏目分页模板/预算、标题限定，
以及 POST JSON 列表或 JSON 内 HTML 等发现方式。请求保持有界，记录入口失败、
分页截断、附件错误、图片待解析和稀疏正文。WPS 按内容魔数识别，不只看扩展名。
病名使用最长非重叠别名，病例/死亡列明确区分本月、累计和同期；定义起始日前的
新版类别不自动拼接，正文年度累计、往年数和排名不作为当月数值。

定时提交在同一 CN 事务锁下核对候选：如果普通刷新与已有 `revised`/`final`
病例或死亡数不同而无权覆盖，只隔离该观测，将新旧值、来源 URL、SHA 和原文
保存在 `Tasks.metadata.province_ingestion.revision_candidates`。任务/采集审计和
自动健康状态显示 `needs_review`；其他新月份仍可入库。旧月表格成功而新月只有正文
稀疏值时显示 `partial`，不把整次任务标为完整成功。

### 图片与扫描件：不同 AI 模型独立识别、专家投票

正文图片、图片附件和扫描 PDF 均归档原件，直接交给模型中心的不同 AI 模型识别。
省级图片链路不调用 OCR，不生成或依赖 OCR 候选。HTML、Excel、Word 和图片混排时逐个
处理，不因第一张表成功而跳过后续原件；混合 PDF 中的扫描页也必须纳入。

每位专家收到同一组完整原件、官方正文和严格结构 schema，不会看到其他专家的答案。
图片只做无文字层的 PDF 包装，原件和包装件分别保存 SHA；不先把图转成识别文字。
所有调用复用 `ModelCenterCompleter`、持久工作队列与模型中心调度器，按实际模型身份
去重，同一模型不同供应商不算两票；文本降级、调用失败、不确定读数、缺页和结构校验
失败均记弃权。

默认固定 3 个专家席位，整份报告须有至少 2 个不同模型投出相同的语义结果才通过；
可配置 5 或 7 席。无法出席的席位仍计入分母，不按成功调用数重新计算多数。
投票按完整报告进行，不逐个单元格拼凑结果。每个有效方案先检查地区、月份、逐页完整性、
病例/死亡列、病种定义、父子项、印刷合计与脚注；甲乙合并列示时保留原表分组。
总数不反推明细，空白不补零。未达多数为 `needs_review`。

全部逐模型回答、有效票/弃权原因、分歧和原件来源保存在整理/任务审计中；观测只保留
本行证据及带 SHA 的投票回执。同月来源冲突隔离该月，人工 `revised`/`final` 数据仍受
原有提交保护。来源整理严格从既有不可变 HTTP 归档重放，模型调用通过统一调度完成。

可单独复核归档文件（只生成审计，不导入业务观测）：

```bash
venv/bin/python scripts/review_cn_province_images.py \
  --province CN-QH --month 2026-10 \
  --document ORIGINAL_IMAGE_OR_PDF --source-url ORIGINAL_URL \
  --article-file CAPTURED_ARTICLE_TEXT --output REVIEW_JSON --live --panel-size 3
```

多张图片按顺序重复 `--document` / `--source-url`。不加 `--live` 只输出原件清单；
加上后创建可追溯父任务，使用既有持久调度器的原生文件专用工作器完成投票。
`--timeout` 控制等待预算；未达多数/超时退出码为 2，通过为 0，不自动回填或发布。

### 持续巡检与可复核回填

先采集归档，再离线检查与计划，最后提交；修复基线、候选、SHA、计划、
逐省提交回执和入库复核结果都存放在同一批次目录。

```bash
venv/bin/python scripts/repair_cn_province_data.py snapshot --output BATCH_DIR
venv/bin/python scripts/repair_cn_province_data.py collect --output BATCH_DIR --workers 4
venv/bin/python scripts/check_cn_province_health.py --input BATCH_DIR \
  --stale-months 3 --window-months 12
venv/bin/python scripts/repair_cn_province_data.py plan --output BATCH_DIR
venv/bin/python scripts/repair_cn_province_data.py apply --output BATCH_DIR
venv/bin/python scripts/verify_cn_province_repair.py --output BATCH_DIR
```

`BATCH_DIR` 是包含 `provinces/` 和可选 `image_review/` 的采集批次目录；
新批次用 repair 脚本参数 `--output BATCH_DIR` 指定。巡检只读归档，生成
JSON/Markdown，分开报告最新发现/解析/表格/整表核验月份、缺月、病种与死亡列
覆盖、图片和失败报告、配置与解析语义指纹以及下一步动作。采集时未保存的旧指纹
标为未知，不补写成“未变化”。

基线不可覆盖；计划检查疾病映射、日期/地区/来源、重复和冲突，提交前再次核对
原值及更新时间，避免覆盖并发更新。纠正的原始记录进入不可变历史；
误识别且来源不能支持的零值改为 `source_missing`，不删除，也不制造替代数值。
巡检输出用于定位下一步动作；新鲜度阈值属于可调运维预算，不代表官方发布承诺。

## 控制面板与公开站点

- 控制面板 `/countries` 接口返回 `location_type` 和 `parent_code`，地区选择器按国家与
  省级地区分组，并直接以省级 geography 的 Series-first 数据生成概览。
- 公开数据生成器只导出启用中的地区，`meta.json` 和省级 JSON 保留地区层级；每个
  有数据的 `CN-XX` 仍生成独立详情页和下载入口，但默认集中在中国详情页的折叠式
  分省目录中，不平铺到国家目录和下载目录。
- 首页指标、国家排行、疾病全球曲线和汇总值只使用国家级地区。疾病 JSON 使用
  `aggregation_scope=national_jurisdictions_only` 标明口径，并保留被排除于汇总之外的
  省级代码，防止 `CN` 与 `CN-XX` 重复计算。
- 导入后由正式数据发布流程生成、验证并发布，成功后才提交公开投影和确认 dirty。
  本地 `scripts/export_cn_province_review.py` 使用正式省级投影生成验收 JSON/CSV，
  不发布、不修改公开投影、不清理 dirty。生成公开 JSON 的增量模式要求已验证完成
  的基线，不可复制/伪造完成回执绕过发布检查。

## 来源与许可

- 历史来源：[code_PHSM](https://github.com/xmusphlkg/code_PHSM)
- 数据中心机制参考：[CNIDS DatacenterReport](https://github.com/xmusphlkg/CNIDS/tree/master/Script/DatacenterReport)
- 数据中心目录：[公共卫生科学数据中心](https://www.phsciencedata.cn/Share/ky_sjml.jsp)

引入或再分发 PHSM 文件前，应保留其 GPL-3.0 许可、仓库版本、论文引用和来源链接，
并由项目维护者确认与本项目发布方式的兼容性。运行时抓取的官网原件也应记录最终
URL、获取时间和 SHA-256。
