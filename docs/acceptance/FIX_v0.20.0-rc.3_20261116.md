# v0.20.0-rc.3 地图性能修复与复验

日期：2026-11-16（Asia/Shanghai）。对应 [rc.3 审查](REVIEW_v0.20.0-rc.3_20261116.md) 的 P2 主线程重复全历史扫描问题。本 subagent 完成代码修复及本地复验，未自行 commit、部署或执行生产写入；版本提交与后续发布由主 agent 统一进行。

## 修复

- 病种 JSON 到达前端时，通过 `prepareMapDataset` 一次性准备不可变发布对象：每地区的报告期、按日期索引、无效期标记、共同来源日历、完整日期边界。同一快照中，各地区重复使用的日期只校验一次，仍执行完整合法日期检查。
- 年份、月份、比较范围与趋势聚合复用索引，只遍历请求范围的完整报告期；不再反复解析全部历史点。日期/指标范围结果和跨年色阶各最多保留 64 项，过期项淘汰。dataset/catalogue/population 通过弱引用对象身份隔离，旧代次可回收，新人口或病例发布自动使用新缓存。
- 显式准备的输入递归冻结，防止原地修改造成静默陈旧结果；未准备的纯函数调用继续支持调用间修改输入。分母修订必须替换人口对象，病例修订替换病种对象，符合当前不可变分片读取流程。
- React 的地图总数和色阶依赖只包含日期、指标及比较日期，趋势 `view` 改动只重新计算趋势。色阶缓存也排除 view，并按实际跨年窗口集合复用。

完整变更文件清单：

1. `astro-site/src/components/charts/SubdivisionIncidenceMap.tsx`
2. `astro-site/src/components/charts/subdivision-map/model.ts`
3. `astro-site/src/components/charts/subdivision-map/model.test.ts`
4. `astro-site/scripts/benchmark-subdivision-map.mjs`
5. 本报告 `docs/acceptance/FIX_v0.20.0-rc.3_20261116.md`

未修改其他组件或后端更新链路。

## 同输入基准

使用本机已存在的 US D094 分片：56 地区、23,693 点、13 个年份。修复前实现复制到 `/tmp/globalid-fixes-20261116/rc3-model-before.ts`，其 SHA-256 为 `246d1319f1b84e11897083caa6b94de60c5d618c134fa544bc2bae11d8074942`。相同脚本分别加载前后实现，各运行 3 个样本；文件读取和 JSON 解析不计入两边耗时。

| 阶段 | 修复前中位数 ms | 修复后中位数 ms |
| --- | ---: | ---: |
| 首次色阶，含完整索引准备与输入冻结 | 2636.694 | 97.203 |
| 同一范围再次计算色阶 | 2430.184 | 0.184 |
| 只改变趋势聚合周期 | 2489.834 | 0.131 |
| 首次改为一季度范围 | 2439.322 | 21.621 |
| 已计算色阶后获取默认范围（热缓存） | 2805.494 | 0.576 |

首次色阶减少 **96.31%**，新日期范围减少 **99.11%**。没有通过放宽性能门槛改变验收结果。冷计算仍有一次同步准备成本；上述数字是 Node 本地计算基准，不是浏览器 INP，也不包含网络、JSON 解析或 ECharts 绘制。

前后输入的三个 JSON 摘要完全相同；全年、Q1 地区结果序列化 SHA-256、色阶与默认范围也逐样本完全相同。全年结果 SHA：`d7f9cf5e8dbf651b3107f7a7e43fc2ba69b0e6bb56388ad555f8f4773cd873d7`；Q1：`3dd44df3e82cd1c567fb145a913c00a5005b0c7dbbc6abc024346b0585f2d8ef`。

命令（仓库根目录）：

```sh
astro-site/node_modules/.bin/node --experimental-strip-types \
  astro-site/scripts/benchmark-subdivision-map.mjs \
  --model /tmp/globalid-fixes-20261116/rc3-model-before.ts \
  > /tmp/globalid-fixes-20261116/rc3-before.json

astro-site/node_modules/.bin/node --experimental-strip-types \
  astro-site/scripts/benchmark-subdivision-map.mjs \
  > /tmp/globalid-fixes-20261116/rc3-after.json
```

## 回归结果

```sh
astro-site/node_modules/.bin/node --experimental-strip-types --test \
  astro-site/src/components/charts/subdivision-map/model.test.ts \
  astro-site/src/lib/subdivision-map-points.test.ts

# astro-site 目录
npm run check
```

- 地图及坐标模型 **22 passed、0 failed、0 skipped**。新增回归对比准备前后结果，涵盖月/周/季/年、跨年周报告年、跨年分母、完整期裁剪、缺报、抑制、暂定、不完整、非法期、重叠与缺人口；新病例和人口发布对象使旧值失效。另通过实际读取次数断言，跨年份、日期和 view 交互（包括范围缓存淘汰后）不再扫描来源报告期，未使用不稳定的时间阈值断言。
- Astro 检查 **523 files、0 errors、0 warnings、14 个现有 hints**；样式边界 **PASS**。
- 前后基准输入和结果摘要比较通过。

日志：`/tmp/globalid-fixes-20261116/rc3-node.log`、`rc3-astro-check.log`、`rc3-before.json`、`rc3-after.json`。本轮未重新运行浏览器 CSV/PNG/全屏测试；这些交互未改动，本次验证聚焦计算与 React 依赖。原审查中的数据来源及线上验收限制仍适用。

结论：此 P2 已在本地修复并通过针对性复验，可进入主 agent 的分批版本提交与整体验证流程。
