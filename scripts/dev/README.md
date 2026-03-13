## Dev utilities (临时调试/运维脚本)

这里放的是**开发/排障用的临时脚本**，不属于核心业务流程。

### `insert_country.py`
- 用途：当数据库里缺少 `countries` 的 `CN/中国` 记录时，手动插入（`ON CONFLICT DO NOTHING`）。
- 何时需要：你重建/初始化数据库后发现 dashboard 侧边栏国家为空，或 `countries` 表没有 CN。

### `test_query.py`
- 用途：快速验证数据库连接与数据存在性（例如打印 `DiseaseRecord.time` 的最大值）。
- 何时需要：排查 “dashboard 显示 no data / 连接问题” 时做最小验证。

> 这些脚本可以随时删除；如果你不再需要手动运维/调试，删掉也不会影响主程序。
