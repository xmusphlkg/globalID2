# GlobalID 分块公开数据协议

## 首页摘要与分阶段读取

首页通过 `/site-data/bootstrap.json` 读取小型入口，只包含首页摘要、背景曲线和
更新信息的对象描述，以及完整清单的不可变地址。`home-summary.json` 从同一次
监测导出生成，仅含首页使用的覆盖计数、三个近期地区和六个常用地区。
Situation 使用独立的 `/site-data/situation/bootstrap.json` 和 `summary.json`，
统计口径与地图默认的近 30 天筛选及地点去重一致。

首屏先显示标题、指标标签和稳定的数字占位，背景曲线在空闲时加载，下方态势区
在进入视口时加载，页脚接近视口后才读取更新时间。首页无需下载完整 `meta.json`
或 `map-data.json`。国家、疾病和地图页面需要详细数据时，才读取 bootstrap
指向的 `manifests/<sha256>.json`，校验字节数、哈希及发布版本；同一页面不会
因后台切换 `current.json` 而混用不同版本。

原有 `current.json` 和逻辑数据地址继续供旧页面及外部客户端使用。新入口通过
路径受限的 Pages Functions 同源路由提供，也可部署为独立 Worker；数据发布仍先写入
不可变对象和完整清单，再以条件写入切换当前版本。

## 文件结构

新的下载仓库把每个文件打到国家、疾病和时间窗的交叉粒度。更新一个国家只重写
这个国家自己的格子，疾病目录引用同一批文件，不再把其他国家合并进一份疾病大文件。

```text
manifest.json
cells/<country>/<disease>/<window>.csv
cells/<country>/<disease>/<window>.json
cells/<country>/<disease>/<window>.xlsx
```

D007、中国、当前窗示例：

```text
cells/cn/d007/2026-2029.csv
cells/cn/d007/2026-2029.json
cells/cn/d007/2026-2029.xlsx
```

`manifest.json` 使用 schema version 5。`partitioning.grain` 为
`country_disease_window`。国家和疾病条目都保留时间窗 `parts`，但 `files`
指向同一组格子。国家页的分块标签带疾病编号，疾病页的分块标签带国家代码。
同一相对路径可以出现在两边，字节数和 SHA-256 必须一致。

为满足 GitHub 对清单本身的 100 MiB 限制，磁盘上的 v5 清单使用
`cell_metadata: "country_parts_v1"` 编码。国家 `parts` 保存完整单元格元数据；
疾病 `parts` 只保存 `{country_code, id}`，通过本条目的 `disease_id` 引用国家
单元格。格式对象保存 `bytes` 和 `sha256`，默认路径由国家代码、疾病编号、
时间窗及格式组成；URL 为 `download_url_base + "/" + relative_path`，文件名为
`globalid-<country>-<disease>-<window>.<format>`。国家和疾病路径代码均转小写。
`content_sha256` 默认取 JSON 文件的 SHA-256。显式非默认字段仍保留。

`src/generation/direct_download/manifest.py` 的 `expand_manifest()` 将上述编码
还原成原有的完整国家、疾病下载条目；网页摘要继续收到完整 URL、文件名和
文件描述，无需浏览器解析原始大清单。旧的完整 v4/v5 清单仍可读取。

已经发布的 schema 4 清单仍使用原来的整国文件和整病文件：

```text
countries/<country>/<window>.csv
diseases/<disease>/<window>.csv
```

生成器看到 schema 4 时继续按这个粒度增量更新，不会在一次国家更新里把仓库
改写成格子。空目录和已经是 schema 5 的清单默认写格子。显式切换使用
`layout="cells"` 或 `GIDS_DOWNLOAD_LAYOUT=cells`，这是一次性迁移，不是日常
增量。迁移完成前不要设置这个环境变量。

### 从现有 v4 文件迁移

无需重新查询数据库，迁移器读取并验证现有国家 JSON，保留全部行和来源字段，
按国家、疾病重新生成三种格式，并逐疾病核对记录数与来源观测数：

```bash
venv/bin/python scripts/migrate_download_cells.py --workers 4 --activate
```

候选默认生成到 `exports/site-downloads-cells/`。每完成一个国家就保存断点；
中断后重跑同一命令会校验并复用已完成国家。源清单或生成代码改变时必须换一个
候选目录。迁移和切换都使用生产发布锁；锁被占用时立即失败，不无限等待。

`--activate` 在全量校验通过后切换本地 `exports/site-downloads/`，旧目录保留在
`exports/.site-downloads.v4-<源清单哈希前缀>/`。不传该参数只生成候选。
切换后正常生产调用会根据 v5 清单自动选择 cells，无需设置环境变量，也无需
在每次增量发布中强制全量转换。发布器拒绝旧 v4 冻结候选覆盖已迁移的 v5 基线。

此命令不推送 GitHub，也不部署网站。上线时先部署支持单元格标识的下载页面，
再通过 surveillance 发布流程同步下载文件及网页下载清单；避免只推送新目录而
让线上清单仍指向已删除的旧文件。旧备份在确认线上发布成功前保留。

迁移开始后，可在另一个终端同步上传已完成国家的文件：

```bash
venv/bin/python scripts/stream_download_cells.py --publish
```

上传器读取国家断点，逐文件验证 SHA-256，并默认以 512 MiB 为一批推送到
`globalid-upload/cells-<源清单哈希>` 分支；生成和网络上传可重叠进行。
中断后重跑会根据远端预上传分支跳过已收到的文件。全部文件完成且本地 v5
清单已校验、激活后，`--publish` 才切换公开主分支并抽样核验远端文件。
最终提交承接预上传历史，因此不会再次上传整批数据；正常 fast-forward
检查仍会阻止覆盖其他发布者的新提交。成功后自动删除预上传分支。

展开后每个 `part` 仍声明实际数据范围、记录数、是否为当前更新块，以及每种格式的
Raw URL、文件名、字节数和 SHA-256。下载按钮按一个 URL 取一个文件。

## 时间窗口

- 常规窗口以年份尾数 `0/5` 为起点，每五年一个块，例如 `2010–2014`、
  `2015–2019`、`2030–2034`。
- 按既有数据边界保留 `2020–2025` 过渡块。
- 2026–2029 年，当前块的固定文件名是 `2026-2029`，下载中心的筛选框和文件标题
  显示 `2026–至今`（英文 `2026–now`，法文 `2026–aujourd’hui`）。
- 展示按浏览器当前年份自动切换；进入 2030 年后，旧块显示 `2026–2029`。
  出现 2030 年数据并发布下载文件后，生成器自动新增 `2030-2034`，网站显示
  `2030–至今`（英文 `2030–now`）；2035 年及以后以此类推。
- “至今”指当前日历分段，不代表数据已更新至今天；实际覆盖日期以文件卡片中的
  数据范围为准。展示名称变化不会改变文件名、下载 URL 或 `window` 筛选参数。
- 如果任一格式接近 100 MiB，生成器会在稳定日期边界继续二分，目标上限为
  90 MiB。

历史分块文件不包含每次发布都会变化的生成时间，因此普通增量更新不会重写历史
文件。历史数据被修订时，只有对应历史块会更新。

## 浏览器下载

所有数据下载统一进入三语 `/downloads/` 下载中心。国家、疾病、报告与图表入口
只生成中心链接，可携带 `country`、`disease`、`window`、`format` 查询参数。
其他页面不再维护文件列表或下载弹窗。

中心先读取已发布的 compact index，再按所选国家或疾病读取一份 summary；
国家、疾病、时间窗、格式独立筛选，每页展示 24 个文件。国家与疾病入口在相同
组合下引用完全相同的 cell 文件。没有有效发布清单时显示错误，不回退到旧链接。
实际文件读取、Blob 保存和失败后的 Raw 链接都由中心的 `DownloadFileCard` 处理。

旧 `/site-data/downloads/<kind>/<id>.json` 入口只返回 schema 2 的 `download_center`
导航地址，不再发布可能过期的文件清单。报告站需单独执行 reports-only 模板发布；
维护发布设置 `suppress_subscription_dispatch=True`，不发送订阅通知。

## 本地 Git 工作区与发布

专用数据仓库持久保存在：

```text
exports/site-downloads/
```

该目录是被父仓库忽略的独立 Git 工作区，Git 元数据位于
`exports/.site-downloads.git/`。复用持久工作区，只提交变化文件并进行普通
fast-forward 推送，不重复 clone 或重置本地导出结果。手动操作：

```bash
make site-data
make site-download-sync
```

## 随 surveillance 更新发布

启用任务的 `include_git_push` 后，每次 surveillance 数据发布都会同时生成下载
文件，不使用独立的六小时下载调度，也不依赖静态站点构建。

1. 从同一数据库快照投影一次数据，生成网页 JSON 和下载文件。
2. 按所选国家和受影响疾病增量检查输入指纹；未变化的格子不生成文件。Schema 5
   只重写变化国家自己的格子，疾病条目改为指向这些格子。Schema 4 仍替换整份
   疾病时间块。CSV、JSON、XLSX 均保持确定性输出。
3. 用硬链接冻结本次下载候选，避免复制全部历史数据；后续写入使用原子替换。
4. 发布 surveillance JSON，然后把相同快照的下载文件推送 GitHub；两者成功
   后才确认本批 dirty IDs。网页摘要携带本次下载清单。
5. GitHub 瞬态失败沿用原任务重试，复用冻结文件，不重新读库和导出。
   GitHub 已成功而数据库确认失败时，不重复推送。确认完成后删除候选资产
   硬链接，保留小型清单和发布回执。

首次接通时补齐旧下载基线；之后正常更新只处理变化范围。`content_only`、
文献和 Situation 发布不会触发下载导出。

发布器验证 schema、路径、文件大小和变化文件的 SHA-256，并复用 Git 中
未变化文件的校验结果。默认以 Git push 成功为发布依据；需要远端抽样审计时
使用 `--verify-remote`，需要重算所有文件哈希时使用 `--verify-checksums`。
