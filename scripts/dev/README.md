## Dev utilities (临时调试/运维脚本)

这里放的是**开发/排障用的临时脚本**，不属于核心业务流程。

### `insert_country.py`
- 用途：当数据库里缺少 `countries` 的 `CN/中国` 记录时，手动插入（`ON CONFLICT DO NOTHING`）。
- 何时需要：你重建/初始化数据库后发现 dashboard 侧边栏国家为空，或 `countries` 表没有 CN。

### `test_query.py`
- 用途：快速验证数据库连接与数据存在性（例如打印 `DiseaseRecord.time` 的最大值）。
- 何时需要：排查 “dashboard 显示 no data / 连接问题” 时做最小验证。

### `commit_data_refresh.sh`
- 用途：开发环境里把数据刷新产物单独打成一个 Git commit，避免 `astro-site/src/data`、`data/current`、`data/raw` 长期污染工作区。
- 说明：这里的 `scripts/dev/commit_data_refresh.sh` 是兼容入口，实际逻辑在通用脚本 `scripts/commit_data_refresh.sh`，生产 Data Release 也可以通过环境变量调用它。
- 直接提交当前已刷新的数据：
  ```bash
  scripts/dev/commit_data_refresh.sh
  ```
- 包住一次刷新命令，命令成功后自动提交：
  ```bash
  scripts/dev/commit_data_refresh.sh -- make site-data
  scripts/dev/commit_data_refresh.sh -- python main.py crawl --country AU --source all --process --save-raw
  ```
- 只会暂存默认数据路径；如果 index 里已经有 staged 改动，脚本会先退出，防止把代码改动混进数据快照。
- 查看会提交什么但不真正提交：
  ```bash
  scripts/dev/commit_data_refresh.sh --dry-run
  ```

> 这些脚本可以随时删除；如果你不再需要手动运维/调试，删掉也不会影响主程序。
