DROP INDEX IF EXISTS idx_subscriptions_contact;
DROP INDEX IF EXISTS idx_subscriptions_list_status;

PRAGMA foreign_keys = OFF;
PRAGMA legacy_alter_table = ON;

ALTER TABLE subscriptions RENAME TO subscriptions_legacy_0002;

CREATE TABLE subscriptions (
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

INSERT INTO subscriptions (
  id,
  subscriber_id,
  contact_id,
  list_id,
  status,
  frequency,
  confirmed_at,
  paused_until,
  created_at,
  updated_at
)
SELECT
  id,
  subscriber_id,
  contact_id,
  list_id,
  status,
  frequency,
  confirmed_at,
  paused_until,
  created_at,
  updated_at
FROM subscriptions_legacy_0002;

DROP TABLE subscriptions_legacy_0002;

PRAGMA legacy_alter_table = OFF;
PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_subscriptions_contact
  ON subscriptions(contact_id, status);

CREATE INDEX IF NOT EXISTS idx_subscriptions_list_status
  ON subscriptions(list_id, status, frequency);
