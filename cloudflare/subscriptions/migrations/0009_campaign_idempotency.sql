-- Machine-created campaigns use content_ref as an opaque, replay-safe key.
-- Existing dashboard campaigns retain their metadata_json.contents reference.
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_campaigns_admin_idempotency
ON message_campaigns(trigger_type, content_ref)
WHERE trigger_type = 'admin_notification'
  AND content_ref LIKE 'idempotency:%';
