# 中国省级法定传染病数据接入

本接入只新增 31 个省级行政区数据，不改写中国国家级数据。省级行政区使用
ISO 3166-2 风格代码（例如北京 `CN-BJ`），事实使用
`country:CN-BJ:national`；`country:CN:national` 仍只属于中国国家级序列。

## 来源与覆盖规则

- `SRC_CN_PROV_DATACENTER`：公共卫生科学数据中心，按发病月份统计，每年检查
  一次可用年份；原始抓取协议参考 CNIDS 的 `Script/DatacenterReport`，但本项目
  独立实现请求、SpreadsheetML 解析、校验和失败关闭。
- `SRC_CN_PROV_MONTHLY_REPORT`：各省卫生健康主管部门发布的法定传染病月报，
  每月更新并回看最近三个月。
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

## 执行

部署新 ontology 后先同步注册表：

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

数据中心命令默认使用 6 个受限工作线程；可通过 `--workers 1` 恢复完全串行，或按
上游承载能力在 1–16 范围内调整。每个工作线程使用独立 HTTP 会话，避免跨线程共享
连接状态。

省级月报更新：

```bash
venv/bin/python scripts/update_cn_provinces.py monthly --province CN-LN
venv/bin/python scripts/update_cn_provinces.py monthly --province CN-LN --apply
venv/bin/python scripts/update_cn_provinces.py monthly --all-provinces --apply
```

`--all-provinces` 会刷新全部已配置省份，并逐省输出 `parsed`、`narrative_only`、
`discovery_pending`、`no_parsed_rows` 或 `failed_closed` 状态。单个官网失败不会阻止
其他省份导入，但失败省份不会用空值或推测值覆盖已有数据。

无 `--apply` 时只抓取、解析并输出覆盖摘要，不写数据库。建议调度：月报每月运行，
逐省执行；数据中心每年运行一次。生产调度应保存 stdout/stderr，并对空抓取、页面
结构变化或未注册疾病报警。

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
- `attachment_auto`：新疆；从详情页选择受支持附件。
- `narrative_only`：官网只明确发布总数或文字摘要时，只发现并监测页面，不推导
  疾病明细。
- `discovery_pending`：尚未确认稳定现行栏目或格式的省份，失败关闭，不使用搜索
  摘要或 OCR 猜值。

旧版 `.doc` 先用 `antiword` 读取表格；表格不完整或 WPS 文件不兼容时，再用
LibreOffice Writer 转为 `.docx`。转换在一次性临时目录和独立 LibreOffice 配置中
执行，单次上限 60 秒，只读取表格，不执行宏；两种工具都不可用或转换失败时明确
失败，不会静默改用正文数字。Debian/Ubuntu 部署依赖：

```bash
apt-get install --no-install-recommends antiword libreoffice-writer-nogui
```

维护某省时只修改对应省份模块；只有页面结构确实特殊时才在该模块增加定制钩子，
并把可复用的文档/表格能力下沉到共享解析器。

## 控制面板与公开站点

- 控制面板 `/countries` 接口返回 `location_type` 和 `parent_code`，地区选择器按国家与
  省级地区分组，并直接以省级 geography 的 Series-first 数据生成概览。
- 公开数据生成器只导出启用中的地区，`meta.json` 和省级 JSON 保留地区层级；每个
  有数据的 `CN-XX` 仍生成独立详情页和下载入口，但默认集中在中国详情页的折叠式
  分省目录中，不平铺到国家目录和下载目录。
- 首页指标、国家排行、疾病全球曲线和汇总值只使用国家级地区。疾病 JSON 使用
  `aggregation_scope=national_jurisdictions_only` 标明口径，并保留被排除于汇总之外的
  省级代码，防止 `CN` 与 `CN-XX` 重复计算。
- 导入完成后必须重新运行 `scripts/generate_site_data.py`，否则数据库中的新省份不会
  自动出现在静态站点构建产物中。

## 来源与许可

- 历史来源：[code_PHSM](https://github.com/xmusphlkg/code_PHSM)
- 数据中心机制参考：[CNIDS DatacenterReport](https://github.com/xmusphlkg/CNIDS/tree/master/Script/DatacenterReport)
- 数据中心目录：[公共卫生科学数据中心](https://www.phsciencedata.cn/Share/ky_sjml.jsp)

引入或再分发 PHSM 文件前，应保留其 GPL-3.0 许可、仓库版本、论文引用和来源链接，
并由项目维护者确认与本项目发布方式的兼容性。运行时抓取的官网原件也应记录最终
URL、获取时间和 SHA-256。
