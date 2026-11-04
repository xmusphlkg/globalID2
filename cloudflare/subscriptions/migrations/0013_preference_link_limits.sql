ALTER TABLE subscriptions ADD COLUMN preference_revision TEXT;

CREATE TABLE IF NOT EXISTS preference_link_limits (
  bucket TEXT PRIMARY KEY,
  attempts INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preference_link_limits_expiry
  ON preference_link_limits(expires_at);
