CREATE TABLE IF NOT EXISTS subscribers (
  id TEXT PRIMARY KEY,
  display_name TEXT,
  locale TEXT NOT NULL DEFAULT 'en',
  timezone TEXT NOT NULL DEFAULT 'UTC',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS subscriber_contacts (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  address TEXT NOT NULL,
  address_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  verified_at TEXT,
  metadata_json TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
  UNIQUE (channel, address_hash)
);

CREATE TABLE IF NOT EXISTS subscription_lists (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT,
  default_frequency TEXT NOT NULL DEFAULT 'weekly',
  is_public INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  subscriber_id TEXT NOT NULL,
  contact_id TEXT NOT NULL,
  list_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  frequency TEXT NOT NULL DEFAULT 'weekly',
  source TEXT,
  confirmed_at TEXT,
  paused_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
  FOREIGN KEY (contact_id) REFERENCES subscriber_contacts(id),
  FOREIGN KEY (list_id) REFERENCES subscription_lists(id),
  UNIQUE (subscriber_id, contact_id, list_id)
);

CREATE TABLE IF NOT EXISTS subscription_filters (
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL,
  filter_type TEXT NOT NULL,
  filter_value TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
  UNIQUE (subscription_id, filter_type, filter_value)
);

CREATE TABLE IF NOT EXISTS message_campaigns (
  id TEXT PRIMARY KEY,
  list_id TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  subject TEXT NOT NULL,
  content_ref TEXT,
  metadata_json TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  scheduled_at TEXT,
  sent_at TEXT,
  FOREIGN KEY (list_id) REFERENCES subscription_lists(id)
);

CREATE TABLE IF NOT EXISTS message_deliveries (
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

CREATE TABLE IF NOT EXISTS subscription_events (
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

CREATE INDEX IF NOT EXISTS idx_subscriber_contacts_hash
  ON subscriber_contacts(channel, address_hash);

CREATE INDEX IF NOT EXISTS idx_subscriptions_contact
  ON subscriptions(contact_id, status);

CREATE INDEX IF NOT EXISTS idx_subscriptions_list_status
  ON subscriptions(list_id, status, frequency);

CREATE INDEX IF NOT EXISTS idx_subscription_filters_lookup
  ON subscription_filters(subscription_id, filter_type, filter_value);

CREATE INDEX IF NOT EXISTS idx_deliveries_campaign_status
  ON message_deliveries(campaign_id, status);

CREATE INDEX IF NOT EXISTS idx_events_subscription_time
  ON subscription_events(subscription_id, created_at);

INSERT INTO subscription_lists (
  id,
  code,
  name,
  description,
  default_frequency,
  is_public,
  created_at
) VALUES
  (
    'list_reports',
    'reports',
    'Report updates',
    'New disease surveillance reports and country updates.',
    'weekly',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  ),
  (
    'list_alerts',
    'alerts',
    'Priority alerts',
    'High-signal outbreak, anomaly, and urgent surveillance alerts.',
    'instant',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  ),
  (
    'list_weekly_digest',
    'weekly_digest',
    'Weekly digest',
    'A weekly digest of new GIDS reports and notable changes.',
    'weekly',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  )
ON CONFLICT(code) DO NOTHING;
