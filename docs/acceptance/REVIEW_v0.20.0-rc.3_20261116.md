# v0.20.0-rc.3 行政区地图与人口自动化独立验收

日期：2026-11-16（Asia/Shanghai）。基线：`b86708d`，验收当前未提交工作区。此编号只标识验收批次，不改变软件版本或创建 Git tag。本轮只读审查与离线测试，没有部署、发布、抓取、生产数据库写入或 systemd 修改。

## 结论

数据口径和本地分片核验通过；发现 1 项 P2 交互性能问题，建议修正后复验再批准此批次。未发现已确认的 P1。单元测试通过不代表真实浏览器交互性能通过。

## 范围

- `SubdivisionIncidenceMap.tsx`、`subdivision-map/model.ts`、共享图表动作及国家地图地区点。
- `site_subdivision_maps.py`、地图导出接入、病例率 SQL 准入与人口精确年度匹配。
- CN/AU/BR/US/JP/SE 官方人口获取、导入、增量脏标记、失败隔离、发布重试及人口 systemd 配置。
- geoBoundaries 固定版本、地理代码、摘要与本地实际生成分片。

## 问题

### P2：每次调整地图日期或指标，会同步重建全部年份的全部地区索引

定位：`/home/likangguo/globalID/globalID2/astro-site/src/components/charts/subdivision-map/model.ts:198`（`sharedCeiling`，逐年调用 `resultsFor`）；同文件 `:137`（每个地区每次重新解析完整历史 periods、创建 lookup）；`/home/likangguo/globalID/globalID2/astro-site/src/components/charts/SubdivisionIncidenceMap.tsx:143`（React render 内同步执行，依赖整个 `window`，调整趋势聚合周期也会重新计算色阶）。

触发：打开美国地图选择本地已生成的 D094，或在该地图切换日期、年份、指标、比较范围。对本地实际 US D094 数据（56 地区、23,693 点、13 个年份）单独运行 `sharedCeiling` 三次分别耗时 **2,684 / 2,667 / 2,501 ms**，尚不包含请求、默认期间计算及 ECharts 绘制。因为该函数直接在 React render 的 `useMemo` 中执行，浏览器主线程会在计算期间无法处理输入和绘制反馈；`useMemo` 不能消除每次新范围的重算。综合 `defaultWindow + resultsFor + sharedCeiling` 的一次独立采样，美国约 6,005 ms、日本约 4,055 ms；不同设备和并发负载会影响绝对时间，不能把这些采样当作正式浏览器 INP 数值。

建议：按 dataset/case_version 预先解析并缓存每个地区的 period lookup，避免每个年份重复解析全部历史数据；色阶缓存使用真正相关的病种/指标/日期形状/比较范围参数，排除仅影响趋势展示的 view；如剩余计算仍长，将其移出主线程。保留跨年同色阶、缺报和人口口径合同。

复现（不修改仓库数据）：

```sh
astro-site/node_modules/.bin/node --experimental-strip-types docs/acceptance/evidence/20261116_review/rc3-model-performance.mjs
```

脚本及结果：`docs/acceptance/evidence/20261116_review/rc3-model-performance.mjs`、`rc3-model-performance.log`；六国综合采样：`rc3-model-benchmark.log`。

## 验证证据

```sh
SKIP_NETWORK_TESTS=1 venv/bin/python -m pytest -q \
  tests/unit/test_official_population_refresh.py \
  tests/unit/test_population_automation.py \
  tests/unit/test_population_source_eligibility.py \
  tests/unit/test_population_incremental_store.py \
  tests/unit/test_subdivision_population.py \
  tests/unit/test_jp_population_source.py \
  tests/unit/test_se_population_source.py \
  tests/unit/test_us_population_source.py \
  tests/unit/test_site_subdivision_maps.py

astro-site/node_modules/.bin/node --experimental-strip-types --test \
  astro-site/src/components/charts/subdivision-map/model.test.ts \
  astro-site/src/lib/subdivision-map-points.test.ts
```

结果：Python **126 passed**（7.43s）；Node **19 passed，0 failed，0 skipped**。日志 `docs/acceptance/evidence/20261116_review/rc3-pytest.log`、`rc3-node.log`。Python 包含临时 SQLite 的事务回滚、精确脏标记和零变化检查，未连接生产数据库执行写入。

另重新执行现有只读独立核验脚本 `output/subdivision-expansion/population-audit.py`，将输出位置重定向到 `docs/acceptance/evidence/20261116_review/rc3-artifact-audit.json`，未覆盖既有验收产物。核验 **6 国、190 地区、344 国别病种、9,060 条地区病种序列、363 个 JSON、4,395 条人口、3,132,180 组病例点/报告期**；包括 38,940 个跨日历年周、19,627 个报告年不同于周开始年，**error_count=0**。核验对照本机已存在的官方成功快照，并重新计算输入摘要检测期间变化；没有重新访问官方网站，故不证明远端今日内容或生产已发布代次。

## 未覆盖

本轮未重做真实浏览器六国语言/移动布局/全屏/CSV/PNG 验收，未验证生产部署与 systemd 实际启用状态，未执行官方在线发现，也未重新运行既有旧曲线 `incidence_sources` 检查。旧验收文档所述旧曲线出处字段缺失仍作为已知限制，不能据本次地图数据核验宣称该检查通过。
