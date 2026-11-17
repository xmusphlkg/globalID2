# Sri Lanka WER source evidence

`official-archive-bandwidth-error.html` is the actual response captured from
the supplied official archive URL during the 2026-11-17 onboarding check.
`retrieval.json` records its URL, HTTP status and SHA-256. The server returned
HTTP 500 with a `509 Bandwidth Limit Exceeded` error document. Other current
and historical official paths returned HTTP 403.

This fixture records the live-source unavailability response. Link-topology
examples in the unit tests are explicitly synthetic and contain no numerical
disease data.

Historical recovery is represented by `wayback-cdx.json` and two real Internet
Archive replays: `wayback-2007-vol34-no05.pdf` and
`wayback-2007-vol34-no06.pdf`. The parser verifies their legacy Table 5
source-defined week, districts, A current-week and B year-cumulative WRCD
notification columns. Archive provenance and reuse terms remain under review;
these fixtures do not enable public release.
