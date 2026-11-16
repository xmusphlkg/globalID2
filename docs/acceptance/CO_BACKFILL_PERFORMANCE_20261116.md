# CO historical backfill performance and revision check

Date: 2026-11-16. The INS SIVIGILA historical controller remains in progress;
this note verifies bounded pages, not completion or public release.

## Single-page timing

The original 2012 10,000-row pages at offsets 0, 10,000, 20,000 and 30,000
spent 3.39/2.93/3.13/4.67 seconds in collect, 0.52/0.40/0.38/0.94 seconds
in organize, and 18.57/27.41/34.83/42.09 seconds in store, respectively
(`run_stages` timestamps). Database storage dominated the page time.

CO now opts into two source-series storage optimizations; other countries keep
the previous paths:

1. Quality history reads begin at the earliest incoming period's immediate
   predecessor, bounded by the configured 20,000-day lookback. The quality
   rules use history for same-identity revisions, the incoming time window,
   and the adjacent prior period of an all-zero tail.
2. The observation upsert returns accepted database rows. Revision comparison
   reuses those rows instead of selecting the same current identities again.
   The pre-upsert seed and latest-revision queries remain. Rejected upserts do
   not generate revisions, and duplicate accepted identities use the final
   returned state.

A read-only replay of applied 2012 offset 30,000 compared the quality input
against the database: the earlier query read 117,945 rows in 3.22 seconds and
evaluated them in 2.42 seconds; the bounded query read 28,417 rows in 0.97
seconds and evaluated them in 1.34 seconds. The issue and blocking results
were identical (both empty). Timings are single measurements, not a controlled
benchmark.

For 2013, store durations for offsets 10,000 through 40,000 before the
upsert-returning change were 26.79, 27.91, 30.36 and 41.15 seconds. Offsets
50,000 through 80,000 after the change took 18.92, 15.82, 12.39 and 13.50
seconds. Each page reported 10,000 accepted observations. System load and
page contents differ, so these figures demonstrate live improvement without
isolating a precise per-query speedup.

## Independent page reconciliation

The captured 2013 offset 70,000 page was parsed offline from its immutable
manifest: 10,000 source rows, with a `Cases` sum of 21,638. A separate read
of `disease_series_observations` for that exact source-page URL found 10,000
current rows and the same value sum, with zero differences from each row's
captured `Cases`. All 10,000 current identities had exactly one revision;
there were zero missing revisions and zero differences in latest revision
value, unit, suppression, quality status or dimensions.

Focused store, quality-window and CO adapter tests: 80 passed. Existing tests
also cover conflicting duplicate natural identities. The CO writer retains
the country-level transaction advisory lock: every page updates shared
source-category occurrence counts and aliases, and cross-year quality checks
can depend on a committed previous-year period. Allowing concurrent database
writers merely by changing the lock key would weaken those guarantees.
