CREATE TABLE IF NOT EXISTS situation_alert_events (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  report_id TEXT NOT NULL,
  signal_id TEXT NOT NULL,
  verification_basis TEXT NOT NULL,
  verification_policy_version TEXT,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  status TEXT NOT NULL DEFAULT 'received',
  queued_count INTEGER NOT NULL DEFAULT 0,
  sent_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  received_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS situation_alert_deliveries (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  subscription_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  claim_token TEXT,
  claim_expires_at TEXT,
  transaction_delivery_id TEXT,
  last_error TEXT,
  queued_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  skipped_at TEXT,
  failed_at TEXT,
  FOREIGN KEY (event_id) REFERENCES situation_alert_events(id),
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
  FOREIGN KEY (contact_id) REFERENCES subscriber_contacts(id),
  FOREIGN KEY (transaction_delivery_id) REFERENCES transactional_email_deliveries(id),
  UNIQUE (event_id, subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_situation_alert_events_status_time
  ON situation_alert_events(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_situation_alert_deliveries_due
  ON situation_alert_deliveries(status, next_attempt_at, queued_at);

CREATE INDEX IF NOT EXISTS idx_situation_alert_deliveries_subscription
  ON situation_alert_deliveries(subscription_id, status);
