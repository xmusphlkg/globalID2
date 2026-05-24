CREATE TABLE IF NOT EXISTS transactional_email_deliveries (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  subscription_id TEXT,
  delivery_type TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'email',
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'smtp',
  provider_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  request_source TEXT,
  error_code TEXT,
  error_message TEXT,
  metadata_json TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
  FOREIGN KEY (contact_id) REFERENCES subscriber_contacts(id),
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

CREATE INDEX IF NOT EXISTS idx_transactional_email_deliveries_contact_time
  ON transactional_email_deliveries(contact_id, created_at);

CREATE INDEX IF NOT EXISTS idx_transactional_email_deliveries_status
  ON transactional_email_deliveries(status, created_at);

