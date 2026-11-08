-- A lease prevents concurrent workers from sending the same campaign row.
-- Expired in-flight sends are treated as unknown: SMTP may have accepted them.
ALTER TABLE message_deliveries ADD COLUMN claim_token TEXT;
ALTER TABLE message_deliveries ADD COLUMN claim_expires_at TEXT;
ALTER TABLE message_deliveries ADD COLUMN next_attempt_at TEXT;
ALTER TABLE message_deliveries ADD COLUMN transaction_delivery_id TEXT;
ALTER TABLE message_campaigns ADD COLUMN processing_started_at TEXT;

CREATE INDEX IF NOT EXISTS idx_campaign_delivery_due
  ON message_deliveries(campaign_id, status, next_attempt_at, queued_at);
