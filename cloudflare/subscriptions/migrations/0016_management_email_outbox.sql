CREATE TABLE management_email_outbox (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
  contact_id TEXT NOT NULL REFERENCES subscriber_contacts(id),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'sending', 'retry', 'sent', 'unknown', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  claim_token TEXT,
  claim_expires_at TEXT,
  transaction_delivery_id TEXT REFERENCES transactional_email_deliveries(id),
  last_error TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_management_outbox_due ON management_email_outbox(status, next_attempt_at, created_at);
CREATE INDEX idx_management_outbox_transaction ON management_email_outbox(transaction_delivery_id);
