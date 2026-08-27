ALTER TABLE transactional_email_deliveries ADD COLUMN delivered_at TEXT;
ALTER TABLE transactional_email_deliveries ADD COLUMN failed_at TEXT;

CREATE TABLE IF NOT EXISTS email_delivery_events (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  provider_message_id TEXT,
  correlation_id TEXT,
  event_type TEXT NOT NULL CHECK (event_type IN ('delivered', 'deferred', 'bounced', 'complained')),
  error_code TEXT,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_email_delivery_events_message
  ON email_delivery_events(provider_message_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_email_delivery_events_correlation
  ON email_delivery_events(correlation_id, occurred_at);
