# 前端构建门禁修复与隔离复验

2026-11-16。修复 non-unit 全验收最后两项前端失败，未提交、部署或操作后台发布进程。

## 问题与最终行为

1. `test_missing_only_ci_fixture_supports_clean_build_and_performance_gate` 的 Astro 已成功构建，随后 SEO 把三语订阅页的 `/site-data/situation/rss.xml?lang=...` 错判为静态死链。该路径由独立 Surveillance Worker 的 RSS handler 实现，不会生成到静态 dist。SEO 审计现在只精确接受这个已声明路径；不会放行 `/site-data/` 或 Situation 前缀。回归比对 Worker 的 `SITUATION_FEED_PATH`、handler 分发和 Pages route 声明，并验证拼错 XML、尾斜线、未知 JSON、未知页面仍产生 broken-link 错误，串行与并行审计结果一致。原有重定向、sitemap 和普通静态链接审计不变。
2. `test_alert_export_builds_situation_pages_and_passes_the_release_gate` 原先删除并备份共享 `src/data/situation/international`，清空共享 `dist` / `.astro`，然后构建开发环境的数 GB 数据，最终被 CI 的 300 秒限制终止。现在两个 E2E 都使用 `copy_clean_site`，仅复制源码、静态资源及两份 TS 数据源，重新生成有界 fixture。Situation 保留两条显式 human / people 通报、两国、一个 outbreak、一周，仍执行完整真实 Astro build、三语 Situation 页面、周页、canonical、map JSON、sitemap 和 release validator。

独立副本排除 `src/data`、`public/site-data`、`dist`、`.astro`、`.vite`、`.wrangler` 与测试输出，构建缓存位于副本父目录。仅依赖目录使用现有 `node_modules` 的符号链接。构建环境剔除继承的生产数据根、计划及发布变量，避免把构建引向外部目录；无需备份或恢复共享生成文件。

Situation 不再使用漂移重试，也不再使用 `large-local-fixture` 性能 profile。两份测试都执行默认性能、SEO、i18n 等门禁，子进程上限为 180 秒（原 Situation 为 300 秒）；未提高超时或放宽预算。

## 复验结果

| 命令 / 范围 | 结果 |
| --- | --- |
| `CI=1 venv/bin/python -m pytest -q tests/e2e/test_ci_site_fixture_build.py tests/e2e/test_situation_static_build.py --basetemp=/tmp/globalid-fixes-20261116/frontend-build-isolation` | **2 passed，41.65 秒** |
| clean CI fixture | **112 pages**；render-and-restore 17.6 秒；默认 performance、SEO、i18n PASS |
| Situation fixture | **120 pages**；render-and-restore 15.9 秒；默认 performance、SEO、i18n、Situation release validator PASS |
| `node --test scripts/check-seo-output.test.mjs`（astro-site） | **13 passed，0 failed / skipped** |
| `npm run test:seo`（astro-site） | **50 passed，0 failed / skipped**；其中包含前述 13 项，不能相加 |
| `git diff --check`（本次修改） | PASS |

五份原始日志共 44,066 bytes，逐字节归档在 [`evidence/20261116_fixes/frontend-build-isolation/`](evidence/20261116_fixes/frontend-build-isolation/manifest.json)。
[`manifest.json`](evidence/20261116_fixes/frontend-build-isolation/manifest.json) 记录日志原件路径、字节数、SHA-256 和本次五个源码文件的 SHA；
日志可用 [`SHA256SUMS`](evidence/20261116_fixes/frontend-build-isolation/SHA256SUMS) 验证。manifest SHA-256：
`20ef4b449a0220e40b3a46e647aaa93e95d8e145e5fa115c238a919bd3fc35dc`。

原始失败仍在 `/tmp/globalid-fixes-20261116/nonunit-postgres.log`，本次只定向解决并重跑以上两个节点，不能据此把原 non-unit 全套记为全部通过。此次构建是本地 fixture 门禁；生产源数据一致性、实际发布收据及线上检查仍由各自后续验收确认。
