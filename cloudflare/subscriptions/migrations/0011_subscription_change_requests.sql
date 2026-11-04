-- Confirmation applies this immutable snapshot only after mailbox ownership is
-- demonstrated. Existing active preferences are never changed by a request.
ALTER TABLE subscriptions ADD COLUMN management_generation TEXT NOT NULL DEFAULT '';
UPDATE subscriptions SET management_generation = lower(hex(randomblob(16))) WHERE management_generation = '';

CREATE TABLE subscription_change_requests (
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
  contact_id TEXT NOT NULL REFERENCES subscriber_contacts(id),
  subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
  locale TEXT NOT NULL,
  timezone TEXT NOT NULL,
  frequency TEXT NOT NULL,
  source TEXT NOT NULL,
  filters_json TEXT NOT NULL CHECK (json_valid(filters_json)),
  consent_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'confirmed', 'invalidated', 'expired')),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  confirmed_at TEXT,
  consume_nonce TEXT
);

CREATE INDEX idx_subscription_change_requests_subscription
  ON subscription_change_requests(subscription_id, status, expires_at);
