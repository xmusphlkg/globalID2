CREATE TABLE confirmation_email_outbox (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
  contact_id TEXT NOT NULL REFERENCES subscriber_contacts(id),
  locale TEXT NOT NULL,
  source TEXT NOT NULL,
  request_ids_json TEXT NOT NULL CHECK (json_valid(request_ids_json)),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'sending', 'retry', 'sent', 'unknown', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  claim_token TEXT,
  claim_expires_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_confirmation_outbox_due
  ON confirmation_email_outbox(status, next_attempt_at, created_at);
