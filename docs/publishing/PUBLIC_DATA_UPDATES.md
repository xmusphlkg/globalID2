# Public data update feed

The public `/data-updates/` page combines version notes read from `/site-data/updates/changelog.json` with the data feed. Version notes have date precision; data timestamps represent GIDS publication time. Coverage numbers come from the current published surveillance summary. Curve points are the sum of per-jurisdiction record_count values for jurisdictions with data; geographic levels can overlap and these are not counts of distinct cases. The data feed index contains counts of retained publication batches, not observation counts. The page reads `site-data/updates/latest.json` and monthly UTC files from the existing Pages data Worker and R2 bucket. The feed reconciler projects only `publish_dirty` markers acknowledged by completed export tasks. Surveillance, Situation, literature and knowledge are independent event types. A marker denotes publication of a scope, not the number of observations or source publication time. Missing historical markers cannot be reconstructed as precise events. Only approved scope identifiers and publication times are public; raw task inputs, logs and errors are never included.

`scripts/publish_public_updates.py` reads the database and regenerates the feed. It uploads changed monthly files before the index; conditional writes detect overlapping writes, and a local lock serializes runs on the publisher host. Run the reconciler on one host only; a local lock does not coordinate multiple hosts. A later run retries after an error. Reconciliation runs separately from publication tasks, groups duplicate scopes in the database, uses a read-only transaction, and limits each PostgreSQL statement to five seconds; a failed or slow reconciliation retries on the next timer run. It does not delay the completion of publication tasks. Browser reads are limited to the monthly archives intersecting the selected date range and the existing published surveillance summary; the page does not load the research index. `globalid-public-updates.timer` schedules this every minute once the included systemd units are installed and enabled by the normal installer. The page checks once a minute while open. JSON is served with `no-store`.

Deploying requires the updated Pages Worker and site templates, an R2 binding to the existing data bucket, and the reconciler timer. The installer substitutes the same project/user placeholders used by the other units. Check the initial reconciliation and public JSON responses before enabling the timer. The changes here do not themselves deploy the site, upload a feed, install units, or enable a timer.

The page filters an inclusive UTC date range and draws one bar per day for the records that match the selected type. It still reads only the monthly archives that intersect that range. Event times are displayed in the viewer's local timezone. Changing the range hides the previous records while a newly requested archive is loading. All event dates are normalized to UTC before validation, filtering and counting. The range is limited to 24 months. Missing required archives hide the daily chart instead of inventing zero activity. Index and archive failures are tracked separately, previously loaded records remain visible during a failed refresh, and requests time out after 15 seconds. An index retry starts 60 seconds after the previous request completes, avoiding overlapping responses.

For an existing installation, render units with `scripts/install_systemd_services.sh --dry-run`, install only `globalid-public-updates.service` and `globalid-public-updates.timer` using the existing project and service user, then run `systemctl daemon-reload`. Start `globalid-public-updates.service` once and inspect its journal and the public index/month responses before `systemctl enable --now globalid-public-updates.timer`. The oneshot service loads the latest Python code on each run; this feature does not require restarting the scheduler, publication worker or database. Standalone script invocations need the same database and R2 environment supplied by the service's `EnvironmentFile`.

Production review and deployment evidence: [2026-11-14 acceptance](../acceptance/DATA_UPDATES_20261114.md). The chart counts retained publication records; it does not count individual observations or notification deliveries. Selecting all types also includes dated website changelog entries.


## Version records without template rebuilds

`/changelog/` and `/data-updates/` now fetch the same multilingual version history at runtime in English, Chinese and French. The authoring source remains `astro-site/src/data/changelog.ts`; types and validation live in `astro-site/src/lib/changelog.ts`. `scripts/export-changelog.mjs` validates and serializes the records with Node only. It does not run Astro or Vite. The existing `scripts/publish_public_updates.py` reconciler publishes the changed JSON alongside the monthly data update feed. Website history and database history are attempted independently, so a failure of one does not suppress publication of the other. Invalid website records preserve the last successful object.

Deploy the updated Worker route and page templates once, and verify `/site-data/updates/changelog.json` before exposing the new templates. Thereafter, changing version numbers, release notes or recorded data publications needs only the feed reconciliation. It requires zero HTML rendering, zero asset compilation and zero Pages/Worker deployment. The existing timer and browser refresh each run at one-minute intervals; this is eventual visibility, not an instantaneous push. `npm run dev` exports a local copy of the history for development.

The header always includes Situation regardless of local snapshot availability or a leftover `GIDS_SITUATION_NAV_VISIBLE` environment variable. The map retains its full-viewport layout and hidden footer. Other pages share the same footer with a stable localized changelog link; build-time version numbers are no longer frozen into individual pages. Actual header/footer markup or script changes still require template publication across the main site, Research and Reports.

Template dependency graphs, validated build reuse, country template fingerprints and publication source discovery no longer treat the authored changelog as a template input. `CHANGELOG.md` likewise does not invalidate compiled artifacts; the standalone exporter still checks that every documented engineering release exists in the multilingual catalogue. Tests cover independent JSON changes, cache reuse, cross-page navigation and live browser refresh without page reload.

This separation covers public version records and the data publication history. It does not change the existing rendering rules for new routes, report bodies, knowledge pages or other content outside these feeds.

## 2026-11-14：持久发布收据

每分钟 reconciler 默认使用私有 `exports/.public-updates-receipt.json`。收据绑定 R2 endpoint、account、bucket 和 prefix 的摘要，不保存凭据；通过权限 0600 的临时文件、fsync 和原子替换持久化。输入文件摘要与收据一致且未到核对周期时，该文件不执行 GET 或 PUT。

默认每个文件至少每 24 小时与远端比较一次，跳过和失败不会刷新核对时间。缺失、损坏、目标变化、未来时间戳或过期收据都重新比较；强制核对前先持久撤销该文件的旧确认，避免修复失败后下次误跳。月份文件成功后才写 latest 索引，网站变更历史和数据 feed 仍各自尝试。没有数据库迁移，不需要修改既有 timer。

```bash
# 强制核对所有文件并按现有 CAS 契约修复差异（会实际写远端）
venv/bin/python scripts/publish_public_updates.py --verify-remote
# 临时恢复每次远端比较的旧行为（会实际写远端）
venv/bin/python scripts/publish_public_updates.py --verify-interval-seconds 0
```

日志增加 `remote_reads`、`remote_writes`、`receipt_skips`，统计接口调用次数。2026-11-14 21:44:13 和 21:46:18（Asia/Shanghai）现有 timer 已观察到 `remote_reads=0, remote_writes=0, receipt_skips=4`；21:45:18 有真实新数据时为 2 次读、2 次写、2 项跳过。该优化已经由现有定时入口加载。

数据库历史聚合仍使用完整 `project_updates`，未实施可选的 P7b 事件/月增量投影，避免提前增加投影表和迁移。隔离故障与收据测试详情见 [整体实施记录](../audits/WEBSITE_DATA_UPDATE_REVIEW_2026-11-14.md)。
