ALTER TABLE confirmation_email_outbox ADD COLUMN transaction_delivery_id TEXT
  REFERENCES transactional_email_deliveries(id);
CREATE INDEX idx_confirmation_outbox_transaction ON confirmation_email_outbox(transaction_delivery_id);

-- Only bounded non-personal SES identifiers are retained while an event waits
-- for the SMTP receipt to be written. The signed SNS envelope is never stored.
CREATE TABLE ses_feedback_pending (
  event_id TEXT PRIMARY KEY,
  provider_message_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL DEFAULT '',
  event_type TEXT NOT NULL CHECK (event_type IN ('delivered','deferred','bounced','complained')),
  error_code TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  next_attempt_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_ses_feedback_pending_due ON ses_feedback_pending(next_attempt_at, created_at);
