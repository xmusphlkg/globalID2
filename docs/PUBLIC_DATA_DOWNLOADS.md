# GlobalID 分块公开数据协议

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

`manifest.json` 使用 schema version 3。每个数据集包含 `parts`，每个时间块
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
external-data/globalID2_data_download/
```

该目录是被父仓库忽略的独立 Git checkout。首次发布自动 clone，后续只执行
`fetch`、`pull --ff-only`、同步变化文件、提交和推送。日常操作：

```bash
make site-data
make site-download-sync
```

发布器会验证 schema、所有文件路径、SHA-256、文件大小和远端 Git blob SHA。
数据推送成功后，正式发布流程才构建和部署 Astro 网站。
