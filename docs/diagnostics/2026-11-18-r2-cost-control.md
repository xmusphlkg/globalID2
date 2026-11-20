# R2 publication cost control

The Papers publisher uses the project's shared PostgreSQL database (the
`DATABASE_URL_SYNC` SQLAlchemy connection) to remember confirmed R2 objects and
reject uploads that would exceed the configured bucket budget. Migration
`0034_r2_publication_index` creates the object, reservation and publication-
reference tables. `R2_OBJECT_INDEX_PATH` or `backend="sqlite"` is reserved for
explicit unit-test backends; it is not a production fallback. All publisher
processes and cleanup tools for a bucket must use the same database and lock
directory.

## Object identity and capacity

- Receipts are isolated by the full R2 endpoint and bucket. They contain object
  keys, stored byte counts, ETags, HTTP metadata, confirmation times, and optional
  SHA-256 proofs of decoded immutable content. They contain no credentials.
- Only confirmed successful uploads or verified remote metadata establish object
  existence. A directory listing establishes size and ETag but does not invent
  HTTP metadata. A locally known immutable object can be reused without another
  PUT, LIST, HEAD, or GET when its receipt matches the candidate.
- A decoded content proof permits reuse when gzip output changes but the file
  content and serving metadata are unchanged. The initial proof must come from a
  successful upload or a verified remote read.
- Complete bucket inventory replacements remove stale entries and retain content
  proofs only when the recorded wire size and ETag still match. Saved inventories
  retain their original observation timestamp and cannot overwrite newer local
  observations or upload attempts.
- Uploads reserve their projected growth in a PostgreSQL transaction before issuing
  the PUT. Confirmation atomically records the object and consumes the reservation.
  Reservations survive crashes. An uncertain request is resolved with a successful
  HEAD or a fenced full inventory, never by assuming a timeout means no upload.
- The default budget is **8,000,000,000 stored bytes per bucket**, configured with
  `R2_PAPERS_MAX_TOTAL_BYTES` or the publisher's explicit budget option. An existing
  over-budget bucket blocks further writes; confirmed reuse and cleanup can still
  proceed. This does not reclaim historical objects automatically.

R2's 10 GB-month free storage allowance is based on time-weighted storage and is
shared at the account level. An 8 GB Papers budget leaves some room for other
usage; it does not guarantee a zero account bill. The local database tracks bytes,
not the account's already incurred operation or monthly storage charges.

## Inspect without remote requests

```bash
venv/bin/python scripts/manage_r2_object_index.py status --env-file .env
```

This returns aggregate object count, stored bytes, pending uploads, inventory
freshness and the configured budget. Status does not contact R2 or derive API
credentials. Offline inspection can instead specify `--endpoint`, `--bucket`
and `--index-path` explicitly.

The index deliberately does not count or throttle Class A/B operations. Reads and
deletions remain unrestricted; the database only avoids redundant immutable PUTs
and prevents storage from growing beyond the configured byte budget. Cloudflare's
account usage page remains the source of truth for already incurred operation
charges.

## Reconcile the database

Pause external writers and uninstrumented publishers while reconciling. Updated
local publishers share a file lock that excludes inventory refreshes from their
writes. With all writers controlled, this performs one complete paginated listing:

```bash
venv/bin/python scripts/manage_r2_object_index.py reconcile --env-file .env
```

A previously saved complete inventory can be imported without remote requests:

```bash
venv/bin/python scripts/manage_r2_object_index.py reconcile --env-file .env \
  --inventory exports/r2-cost-audit-2026-11-18/inventory.json
```

The inventory must describe the exact endpoint and bucket, contain a timezone-
qualified `observed_at`, and explicitly mark a complete listing. The neighboring
`gc-inventory.json` is only a prefix subset and must not be used for this command.
`--inventory-sha256 HEX_DIGEST` additionally checks the exact file bytes against a
reviewed digest. A saved snapshot must have been captured while all writers were
controlled and must remain current through import. Prefer a new listing whenever
that cannot be established.

Reconciliation normally refuses to discard pending reservations. After stopping
all publishers, an executable fence checker can authorize explicit recovery:

```bash
venv/bin/python scripts/manage_r2_object_index.py reconcile --env-file .env \
  --recover-reservations --fence-check /absolute/path/to/publisher-fence-check
```

The checker must return zero only while all publishers, including direct commands
and resumable queued releases, remain stopped. It is checked before and after the
remote listing under the local exclusive lock. A failed check leaves reservations
intact. Recovery does not upload or delete R2 objects.

## Storage audit and cleanup

The requested retention policy keeps only the current publication. Its full
dependency closure must be preserved while historical objects are retired. Review
the exact-key cleanup plan and keep the publisher fence in place before applying
remote deletions. Do not replace dependency-aware cleanup with a prefix-wide
deletion or infer liveness from the current local build directory.

Actual bucket totals, cleanup candidates, and any applied cleanup belong in the
companion `exports/r2-cost-audit-2026-11-18` audit artifacts. The final investigation
will record which inventory was imported and whether cleanup was applied.

## Validation

```bash
venv/bin/python -m pytest tests/unit/test_r2_object_index.py \
  tests/unit/test_manage_r2_object_index.py -q
```

Tests cover persistence and target isolation; concurrent capacity reservations;
failed and uncertain writes; immutable content proofs across compression recipes;
inventory replacement; offline status and import; stale or partial inventory
rejection; explicit fenced reservation recovery;
month rollover; and unrestricted reads and deletions when those limits are reached.
