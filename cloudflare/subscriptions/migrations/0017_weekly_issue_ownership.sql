-- One automated weekly-digest issue per ISO week and delivery mode, across
-- the legacy report-only producer and the integrated producer. The expression
-- index applies to old as well as new Worker code, including concurrent writes.
-- If conflicting historical issues already exist, the migration must fail for
-- operator reconciliation rather than silently choosing one.
-- This covers only the two controlled producers' canonical idempotency keys.
-- It cannot infer the business week of manual or custom-key campaigns, or
-- prevent an administrator from deliberately targeting overlapping lists.
CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_digest_issue_owner
ON message_campaigns (
  CASE
    WHEN substr(content_ref, 1, 27) = 'idempotency:reports-weekly:'
      AND substr(content_ref, 28, 8) GLOB '????-W??'
      AND substr(content_ref, 36) GLOB ':weekly_digest:r[0-9]*'
      THEN 'full:' || substr(content_ref, 28, 8)
    WHEN substr(content_ref, 1, 33) = 'idempotency:pilot:reports-weekly:'
      AND substr(content_ref, 34, 8) GLOB '????-W??'
      AND substr(content_ref, 42) GLOB ':weekly_digest:r[0-9]*'
      THEN 'pilot:' || substr(content_ref, 34, 8)
    WHEN substr(content_ref, 1, 26) = 'idempotency:weekly-digest:'
      AND substr(content_ref, 27, 8) GLOB '????-W??'
      AND substr(content_ref, 35) GLOB ':r[0-9]*'
      THEN 'full:' || substr(content_ref, 27, 8)
    WHEN substr(content_ref, 1, 32) = 'idempotency:pilot:weekly-digest:'
      AND substr(content_ref, 33, 8) GLOB '????-W??'
      AND substr(content_ref, 41) GLOB ':r[0-9]*'
      THEN 'pilot:' || substr(content_ref, 33, 8)
    ELSE NULL
  END
)
WHERE trigger_type = 'admin_notification';
