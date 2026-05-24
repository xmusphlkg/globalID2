DROP INDEX IF EXISTS idx_subscription_filters_lookup;
DROP INDEX IF EXISTS idx_deliveries_campaign_status;
DROP INDEX IF EXISTS idx_events_subscription_time;

PRAGMA foreign_keys = OFF;

ALTER TABLE subscription_filters RENAME TO subscription_filters_legacy_0003;

CREATE TABLE subscription_filters (
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  filter_type TEXT NOT NULL,
  filter_value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
  UNIQUE (subscription_id, filter_type, filter_value)
);

INSERT OR IGNORE INTO subscription_filters (
  id,
  subscription_id,
  filter_type,
  filter_value,
  created_at
)
SELECT
  id,
  subscription_id,
  filter_type,
  filter_value,
  created_at
FROM subscription_filters_legacy_0003;

DROP TABLE subscription_filters_legacy_0003;

ALTER TABLE message_deliveries RENAME TO message_deliveries_legacy_0003;

CREATE TABLE message_deliveries (
  id TEXT PRIMARY KEY,
  campaign_id TEXT NOT NULL,
  subscription_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  provider TEXT,
  provider_message_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  queued_at TEXT NOT NULL,
  sent_at TEXT,
  delivered_at TEXT,
  failed_at TEXT,
  FOREIGN KEY (campaign_id) REFERENCES message_campaigns(id),
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
  FOREIGN KEY (contact_id) REFERENCES subscriber_contacts(id)
);

INSERT OR IGNORE INTO message_deliveries (
  id,
  campaign_id,
  subscription_id,
  contact_id,
  status,
  provider,
  provider_message_id,
  attempts,
  last_error,
  queued_at,
  sent_at,
  delivered_at,
  failed_at
)
SELECT
  id,
  campaign_id,
  subscription_id,
  contact_id,
  status,
  provider,
  provider_message_id,
  attempts,
  last_error,
  queued_at,
  sent_at,
  delivered_at,
  failed_at
FROM message_deliveries_legacy_0003;

DROP TABLE message_deliveries_legacy_0003;

ALTER TABLE subscription_events RENAME TO subscription_events_legacy_0003;

CREATE TABLE subscription_events (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT,
  subscription_id TEXT,
  event_type TEXT NOT NULL,
  actor_type TEXT NOT NULL DEFAULT 'system',
  ip_hash TEXT,
  user_agent TEXT,
  metadata_json TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
  created_at TEXT NOT NULL,
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

INSERT OR IGNORE INTO subscription_events (
  id,
  subscriber_id,
  subscription_id,
  event_type,
  actor_type,
  ip_hash,
  user_agent,
  metadata_json,
  created_at
)
SELECT
  id,
  subscriber_id,
  subscription_id,
  event_type,
  actor_type,
  ip_hash,
  user_agent,
  metadata_json,
  created_at
FROM subscription_events_legacy_0003;

DROP TABLE subscription_events_legacy_0003;

PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_subscription_filters_lookup
  ON subscription_filters(subscription_id, filter_type, filter_value);

CREATE INDEX IF NOT EXISTS idx_deliveries_campaign_status
  ON message_deliveries(campaign_id, status);

CREATE INDEX IF NOT EXISTS idx_events_subscription_time
  ON subscription_events(subscription_id, created_at);
