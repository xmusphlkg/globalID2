# Reports hosting and cadence-based generation

Country reports are published to the `globalid-reports` R2 bucket at
`reports.globalinfectiousdisease.com`, using the same direct R2 custom-domain
pattern as Papers. The R2 host contains the browseable directory, country
report pages, public report data, `robots.txt`, and a report-only `sitemap.xml`.
The directory is available at `/reports/` as the slash-suffixed object key
`reports/`; it does not rely on an index at the domain root. Reports are served
directly from R2 without a separate Pages project or an edge Worker.

The release splitter moves `/reports/`, `/countries/{country}/reports/**`, and
`/site-data/reports/index.json` plus `/site-data/reports/{id}.json` into
`exports/reports-site`, rewrites shared-site links
to the main origin, and maps directory `index.html` files to slash-suffixed R2
keys, matching the Papers publisher. It writes `reports-manifest.json`, updates
old main-site report URLs to redirect to the reports host, and adds CORS for
shared assets.

Report hydration scripts, lazy ECharts chunks, styles and fonts are copied with
their transitive dependencies to the report bucket. Only referenced assets are
included. The previous asset generation is retained for cached HTML/open tabs;
unchanged files are skipped by the R2 manifest diff. JavaScript and CSS are
served with their browser-required MIME types.

## Country report schedule

The control-plane scheduler checks for new report windows every 15 minutes.
A completed Japan import also checks immediately; the periodic check remains
the fallback after an outage or a concurrent scheduling attempt.
The pilot is restricted to Japan (`JP`) in scheduling, task creation, the report
service, generation pipeline, and public export. Existing other-country drafts
remain in the database but do not enter this pilot's public bundle. It reads public `disease_surveillance_series`
and observations, applies Japan's public-source allowlist, and chooses
the fastest fresh declared cadence. Imports without usable cadence
metadata fall back to the date spacing in `disease_records`, matching the site
exporter's conservative inference rules.

The scheduler computes a complete daily, weekly, monthly, quarterly, or annual
window from the latest observation. It queues a report only when:

- The source's latest observation is within its cadence freshness limit.
- The report period has ended.
- The current report generator has at least one `disease_records` row in that
  exact national reporting window.
- No report or active scheduled task already covers that country, type, and
  period.

Tasks run at low priority, with at most three newly queued and six active
scheduled reports. This allows new data to trigger reports without repeatedly
regenerating old periods or filling the AI queue. Quarterly and annual report
types require the `0025_report_period_types` migration. Each release refreshes
the report index; incremental exports fetch details only for new or changed
reports, and content-addressed R2 publishing skips unchanged JSON objects.

The dashboard entry is **Models → Reports** (`/models/reports`). It always shows
the Japan pilot, independent of the global country selector. The workflow panel
shows the latest complete period, observation date, record count, existing/active
report status, chart catalog, task progress, and a link to incremental publication.
Manual runs can use an explicitly labelled historical period; automatic runs
still require fresh data. Duplicate periods are skipped, and a PostgreSQL
transaction advisory lock prevents scheduler/API processes from concurrently
queuing the same period. Generation does not itself publish to R2.

Scheduled Japan crawls process the latest 12 weeks of the standardized cache
and every newly downloaded weekly artifact, including older missing weeks.
This limits repeated writes of unchanged history; it does not assert that older
cached rows have already been imported. To backfill the complete cache, use the
explicit `local` source or a dedicated history import and verify that it
completes separately. Forced crawls retain their full source selection. The
incremental window is relative to the latest cached week, so running a crawl
cannot make a stale source appear fresh.

## Evidence, AI writing, and peer review

`report_v4` first builds its statistics deterministically from the report-window
records and historical context. The resulting evidence packet includes the
complete disease roster, per-disease trend and attention metrics, data-quality
limits, death-reporting scope, and source labels. Population-based rates and
their denominator provenance are calculated before any model call.

The shared AI peer-review protocol then writes one concise interpretation in
Chinese, English, and French. The report-specific prompt directs the author to
use only the computed evidence and avoid unsupported causes, outbreak claims,
clinical advice, and new numeric claims. A separate reviewer route audits each
locale against the same evidence and deterministic report. Guardrails reject
unsafe output and digit-based numeric claims, while the reviewer checks all
other factual claims. The AI can add the interpretation section and select validated chart candidates; report
metrics, rankings, source metadata, and deterministic sections remain
unchanged. Review protocol, prompt version, model routes,
verdict, rounds, score, and guardrail notes are saved with the report.

The report is not marked approved unless the shared peer review accepts the
interpretation and the report quality gate passes. A failed or unavailable
review therefore leaves the scheduled task retryable instead of publishing an
unreviewed report. Existing reports are immutable snapshots; a later complete
cadence period creates the next report, while unchanged periods are not
regenerated.

## Evidence-bound ECharts selection

`report_v4.figures.1` reuses the existing `ReportFigureSpec` contract with **12**
renderable templates. A report still has at most **two** figures and **one** per
section. The library is a menu, not a checklist; zero selections is valid.

| Template | Question / eligibility | Placement |
| --- | --- | --- |
| `epidemic_curve` 病例曲线 | Within-window trajectory; ≥3 observed periods | Disease Context |
| `period_cases_bar` 分期柱状图 | Discrete counts; ≥3 observed periods; alternative to curve | Disease Context |
| `period_comparison_bar` 相邻期对照 | Two complete, adjacent observations | Disease Context |
| `cumulative_cases_line` 窗口累计 | ≥3 complete contiguous periods, entire window ≤52 points | Disease Context |
| `rolling_average_line` 三期移动均值 | ≥6 complete contiguous periods; raw line retained, first two means null | Disease Context |
| `period_change_bar` 相邻期增减 | ≥4 complete contiguous periods; signed absolute differences, not percentages | Disease Context |
| `case_distribution_boxplot` 分布箱线图 | ≥8 complete contiguous periods; min/max whiskers, linearly interpolated quartiles | Disease Context |
| `case_distribution_histogram` 分布直方图 | ≥10 complete contiguous, varying observations; five equal-width bins | Disease Context |
| `historical_context_line` 重点疾病历史背景 | Discussed priority disease; ≥3 pre-report observations plus a current observation; ≤52 periods | Disease Context |
| `attention_ranking_bar` 关注排序柱图 | ≥2 section-relevant diseases, fixed 0–100 review-priority scale | Signal Evidence |
| `attention_dot` 关注点图 | Alternative view of the same priority scores, never an extra ranking chart | Signal Evidence |
| `missingness_bar` 字段缺失比例 | ≥2 known field missingness rates, at least one nonzero | Data Notes |

Historical context uses the already-loaded analysis history at the same reporting
frequency as the current disease series. Disease Context follows the attention
ranking rather than disease-code order. Only diseases
already discussed in Disease Context receive candidates; the AI must explain
why that signal merits historical review. Selected history is frozen in the
report figure dataset with explicit report-start/report-end markers. The renderer
shades the report window, marks its start even for a single current period, and
labels historical/current observations and their sources in the accessible data
table. Source or reporting-scope changes form separate line segments (at most
three); they are never joined or used for cross-segment comparisons. Overlapping
cohorts are ineligible rather than silently summed or selected. This supports
Japan's NIID/JIHS source transition without asserting statistical comparability.
It never fetches evolving country data. Unknown or conflicting cadence is
ineligible.

Derived values are computed in Python before writing and review. Missing periods
break curves and disqualify cumulative/distribution/smoothing comparisons; zeros
remain real zeros. A truncated window cannot be labelled as a full-window total.
Histograms count **reporting periods**, not people; boxplots are **not confidence
intervals**. Missingness describes field availability, not underreporting or zero
burden. Priority scores are not health-risk estimates. Maps, pies, dual axes,
forecasts, unsupported rates and arbitrary executable chart code remain excluded.

Candidates reference deduplicated `figure_datasets` in the shared AI prompt.
The author and reviewer see the same computed payload without repeating identical
series for alternate chart styles. Selection and review use the existing calls;
there is no extra history or chart-planning model call. Rationale must explain the
analytical question, relevance to the section, and benefit over text/table.
Dataset checksums detect changes after evidence binding. Method `report_v4.6`
and prompt `report-v4-interpretation-peer-review.v3` distinguish this flow from
previous reports; historical reports are not rebuilt automatically.

The model returns selections in the same response as its narrative, for example
(the candidate ID must appear in the supplied candidate list):

```json
{
  "analysis": {"zh": "…", "en": "…", "fr": "…"},
  "figure_plan": [{
    "candidate_id": "epidemic_curve:D001",
    "rationale": "Show change over the report window for the disease discussed in this section."
  }]
}
```

The shared peer reviewer audits narrative, relevance and redundancy together.
Structural guardrails reject unavailable candidates, duplicate placements,
custom options/data/code and excessive figures. After acceptance, Python binds
trusted data and localized captions, assigns figure numbers in section order,
and runs the publication quality gate. No additional AI chart-planning call is
needed. Reuse requires the current method and prompt version plus accepted review.

Both the dashboard preview and public report use
`shared/report-figure-options.ts` to construct ECharts options from the same
bounded payload. Public charts load on demand and expose a data table. Figure
data is embedded in the report JSON; rendering does not query R2 or fetch live
country data. Report-directory miniature curves also use the frozen report
snapshot. Shared renderer changes participate in the site build-cache fingerprint.
The server-only report disease template is tracked per report disease bundle,
so editing its titles does not invalidate cached paper articles or social cards.
Durable HTML, Markdown and PDF exports retain selected figures as captioned data
tables, so they remain readable without an ECharts runtime or a network request.

Full Pages releases retain a copy of the verified deployable artifact under
`exports/verified-pages-snapshot`. Before publishing shared assets, the release
compares its release ID and content digest with the live `release.json` and
recomputes the local digest. If no matching snapshot exists, the compatibility
deployment stops: a normal local `astro-site/dist` can contain extra pages or
different data and must not be treated as the deployed site. To bootstrap an
installation without a snapshot, restore the exact previously deployed, split
Pages artifact (including `_headers`, `_redirects`, and `release.json`) to that
path. The same checks apply to the restored copy. A successful verified full
release refreshes the snapshot automatically.

Renderer usage in a report section:

```tsx
<ReportFigureList
  figures={section.figures}
  figureData={document.figure_data}
  lang="zh"
  placement="after_content"
/>
```

Defaults can be changed in `.env`:

```text
REPORT_SCHEDULE_ENABLED=true
REPORT_SCHEDULE_POLL_INTERVAL_SECONDS=900
REPORT_SCHEDULE_MAX_TASKS_PER_TICK=3
REPORT_SCHEDULE_MAX_ACTIVE_TASKS=6
REPORT_SCHEDULE_MIN_RECORDS=1
DATA_RELEASE__R2_REPORTS_ENABLED=true
R2_REPORTS_BUCKET=globalid-reports
R2_REPORTS_PUBLIC_BASE_URL=https://reports.globalinfectiousdisease.com
```

## Cloudflare setup

Create the R2 bucket once, using the Cloudflare account configured for the
release service:

```sh
npx wrangler r2 bucket create globalid-reports
```

Connect `reports.globalinfectiousdisease.com` to the bucket under R2 → bucket
settings → Custom Domains, then enable public access. The domain must belong to
a zone in the same Cloudflare account. Cloudflare creates the DNS record and
certificate during domain connection. The release service needs R2 object read
and write access for the bucket; the account API token is also used for release
and project preflight checks.

R2 cache metadata mirrors the main site: HTML revalidates, JSON caches briefly,
and static assets cache for one day. R2 custom domains ignore Pages `_headers`;
set the same response security headers as the Papers host through Cloudflare's
response-header rules. Cloudflare documents [R2 public buckets and custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/) and the [R2 custom-domain API](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/domains/subresources/custom/methods/create/).

## Local checks

```sh
venv/bin/python scripts/split_reports_bundle.py --skip-budget
venv/bin/python scripts/generate_site_data.py --reports-only
venv/bin/python -m pytest tests/unit/test_reports_bundle.py tests/unit/test_report_schedule_service.py
venv/bin/python -m pytest tests/unit/test_report_v4_peer_review.py
venv/bin/python scripts/verify_reports_host.py --source-dir exports/reports-site --reports-origin https://reports.globalinfectiousdisease.com
```

The release pipeline verifies the R2 directory, public sitemap, robots file,
and a sample country report, plus hashes and MIME types of its current asset
dependencies. `--reports-only` refreshes the report index and changed details
without regenerating surveillance downloads, literature or Situation exports.
Main-site links can point directly to the R2 host
without putting report pages on Pages.

The control plane's **Publish reports only** action queues the same exclusive
release lane as a normal publication. It refreshes report data, builds just
the report routes in an isolated staging directory, splits the R2 bundle,
uploads changed objects and verifies the public host. It does not acknowledge
surveillance or other pending publication markers. The full site and its
Pages deployment are not rebuilt by this action. A failed attempt restores the
local report data and bundle; an R2 upload that already started is reconciled
against the remote manifest on the next attempt. A nonempty report index may
not be replaced by an empty one through this path.

Set `DATA_RELEASE__REPORTS_AUTO_PUBLISH_ENABLED=true` after the isolated path
passes production verification to enable scheduler reconciliation. The
scheduler compares the latest completed Japan report task with the start of
the latest successfully verified Reports R2 release. A busy release queue or
process restart leaves the report pending for a later scheduler tick. Failed
reports-only releases observe `DATA_RELEASE__AUTO_FAILURE_COOLDOWN_MINUTES`;
ordinary transient automatic releases use the existing bounded retry policy.
