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
Pages Advanced Mode 同源路由提供，也可部署为独立 Worker；数据发布仍先写入
不可变对象和完整清单，再以条件写入切换当前版本。

## 文件结构

公开数据仓库使用 `main` 分支和稳定的时间窗口文件名：

```text
manifest.json
countries/<country>/<window>.csv
countries/<country>/<window>.json
countries/<country>/<window>.xlsx
diseases/<disease>/<window>.csv
diseases/<disease>/<window>.json
diseases/<disease>/<window>.xlsx
```

D007 示例：

```text
diseases/d007/2015-2019.csv
diseases/d007/2020-2025.json
diseases/d007/2026-2029.xlsx
```

`manifest.json` 使用 schema version 4。每个数据集包含 `parts`，每个时间块
声明实际数据范围、记录数、是否为当前更新块，以及三种格式的 Raw URL、文件名、
字节数和 SHA-256。

## 时间窗口

- 常规窗口以年份尾数 `0/5` 为起点，每五年一个块，例如 `2010–2014`、
  `2015–2019`、`2030–2034`。
- 按既有数据边界保留 `2020–2025` 过渡块。
- 2030 年之前，当前块的固定文件名是 `2026-2029`，网站显示 `2026–now`。
- 出现 2030 年数据后，`2026–2029` 自动冻结，新增 `2030-2034`，网站显示
  `2030–now`。
- 如果任一格式接近 100 MiB，生成器会在稳定日期边界继续二分，目标上限为
  90 MiB。

历史分块文件不包含每次发布都会变化的生成时间，因此普通增量更新不会重写历史
文件。历史数据被修订时，只有对应历史块会更新。

## 浏览器下载

下载弹窗在视口水平、垂直居中，按时间块展示 CSV、JSON、XLSX。按钮通过浏览器
读取 GitHub Raw 文件并创建 Blob 下载，从而避免 CSV/JSON 只在新标签页中打开；
如果自动下载失败，会退回到 Raw 链接。

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
2. 按所选国家和受影响疾病增量检查输入指纹；未变化数据集不生成文件，变化
   数据集只替换内容变化的时间分块。CSV、JSON、XLSX 均保持确定性输出。
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
