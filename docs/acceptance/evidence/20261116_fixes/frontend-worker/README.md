# 前端 / Worker 最终证据

32 个原始文件从 `/tmp/globalid-fixes-20261116/` 逐字节复制，总计 347,275 bytes；
未重新编码日志或压缩截图。`original-evidence-manifest.json` 记录原件绝对路径、
字节数及原始 SHA-256，`SHA256SUMS` 可直接验证这 32 个文件：

```bash
cd docs/acceptance/evidence/20261116_fixes/frontend-worker
sha256sum -c SHA256SUMS
```

详细结果与计数边界见 `docs/acceptance/FINAL_FRONTEND_WORKER_20261116.md`。
首轮默认 E2E 及重点复验的原始日志都保留，不能把首轮失败记作通过。
`map-ui-smoke` 使用真实本地数据；`rss-ui-smoke` 仅浏览器 XML 响应使用 fixture，
真实 R2 / gzip / RSS 由 Worker runtime 测试覆盖。两张地图截图是必要人工证据，
未归档庞大的 trace、首轮错误截图全集或 HTML 快照。

完整本地 Playwright 产物仍位于：

- `/tmp/globalid-fixes-20261116/first-e2e-test-results/`
- `/tmp/globalid-fixes-20261116/focused-fixed-e2e-results/`
- `/tmp/globalid-fixes-20261116/situation-final-recheck-results/`
- `/tmp/globalid-fixes-20261116/country-public-final-e2e-results/`

这些 `/tmp` 路径只说明本次执行环境，不代表已进入版本库的可持久文件。
旧产物 IE 73 个身份差异保留为失败证据。后来独立 full 导出也被源漂移 guard
拒绝；两者均不能作为正式发布输入，通过状态需以冻结重生成的最终候选为准。

## 发布后检查准备

`postpublish-smoke.mjs` **仅已准备，尚未对线上执行**。脚本没有 mock 或部署操作，
只读访问指定 origin，执行前后都要求网站、监测数据与 Situation release 身份精确一致。
会校验监测首页摘要、行政区索引、Situation feed 的不可变对象 SHA / bytes；
检查 en / zh / fr × 390 / 1280 的首页、dengue、态势、订阅与 US 地图、CSV 下载、
横向溢出及 JS 错误；RSS 使用真实表单提交与真实 XML、语言和 release 响应头。
结果与各页面截图输出到指定目录，失败保留中间结果并返回非零。

收到主代理提供的最终收据身份后，从仓库根执行以下模板；替换占位值后才可运行：

```bash
GIDS_SMOKE_ORIGIN='https://globalinfectiousdisease.com' \
GIDS_EXPECTED_SITE_RELEASE='<site release_id from final receipt>' \
GIDS_EXPECTED_DATA_RELEASE='<surveillance release_id from final receipt>' \
GIDS_EXPECTED_SITUATION_RELEASE='<situation release_id from final receipt>' \
GIDS_EXPECTED_RESEARCH_RELEASE='<research release_id from final receipt>' \
GIDS_SMOKE_OUTPUT='/tmp/globalid-fixes-20261116/postpublish-live' \
astro-site/node_modules/.bin/node docs/acceptance/evidence/20261116_fixes/frontend-worker/postpublish-smoke.mjs
```

Research 身份可选；提供时也会校验。准备阶段只执行 `node --check` 做语法检查，
未提供线上身份，未发起任何真实 smoke 请求。准备文件的 SHA 见 `PREPARED_SHA256SUMS`。
