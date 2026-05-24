CREATE INDEX IF NOT EXISTS idx_message_campaigns_trigger_created
  ON message_campaigns(trigger_type, created_at);

CREATE INDEX IF NOT EXISTS idx_message_deliveries_campaign_queue
  ON message_deliveries(campaign_id, status, queued_at);
