# Remaining national source access checks

Checked 2026-11-16 using actual official HTTP responses. This records remaining
national-primary gaps alongside the separately implemented WHO country-reported
annual baseline. A reachable institution home page is not evidence of a
continuous numerical surveillance feed.

| Country | Official primary source | Verified result and next step |
| --- | --- | --- |
| CR | Ministry of Health individual notifications and epidemiological bulletins | Actual PDFs and collective workbook downloaded successfully. Reviewed national annual Table 1 and dengue/chikungunya cumulative publication snapshots have a dedicated primary connector; see [Costa Rica contract](CR_MOH_NOTIFICATIONS.md). Other tables remain gaps. |
| PA | [MINSA weekly bulletin archive](https://www.minsa.gob.pa/boletines-semanales) and [2026 list](https://www.minsa.gob.pa/publicacion/boletin-epidemiologico-semanal-3) | Directory HTTP 200. Actual official PDF links return HTTP 301 with `Location` identical to the requested CDN URL, causing an infinite redirect loop. The original Ministry-host path redirects to the same CDN. No national PDF numerical contract is claimed. |
| PY | [DGVS epidemiological weekly reports](https://dgvs.mspbs.gov.py/boletin-epidemiologico-semanal/) | TLS unexpected EOF. No national report bytes obtained; retry source access separately from WHO data refresh. |
| EC | [MSP epidemiological gazettes](https://www.salud.gob.ec/gacetas-epidemiologicas/) | TLS unexpected EOF. No national report bytes obtained. |
| ET | [EPHI downloads](https://ephi.gov.et/publications/latest-downloads/) | HTTP 200 and the weekly bulletin section is listed. Public multisectoral health security PDF links exist, but a continuous weekly numerical archive remains unverified; earlier section inspection indicated a login requirement. |
| KE | [Ministry of Health](https://www.health.go.ke/) | HTTP 200, with a weekly news link. No continuous epidemiological numerical download contract established. |
| UG | [Ministry bulletins](https://health.go.ug/bulletins) | HTTP 200; four listed products include promotional and mortality/NTLP bulletins. This does not establish broad national infectious-disease weekly coverage. |
| GH | [Ghana Health Service](https://ghs.gov.gh/) | HTTP 200; no reviewed continuous numerical surveillance download established from this page. |
| TZ | [Ministry epidemiological report service](https://www.moh.go.tz/services/27) | One request returned HTTP 200; a follow-up disconnected without a response. No stable report download verified. |

Exact source access receipts are under
`data/current/remaining_national_source_audit.json`. Panama's directory/CDN
responses and the identical self-redirect destinations are recorded in
`data/current/pa/national_source_audit.json`. These checks do not create empty
national ingestion jobs or convert access failures into successful refreshes.
