ALTER TABLE subscription_lists ADD COLUMN name_zh TEXT;
ALTER TABLE subscription_lists ADD COLUMN description_zh TEXT;
ALTER TABLE subscription_lists ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS subscription_filter_options (
  id TEXT PRIMARY KEY,
  filter_type TEXT NOT NULL,
  filter_value TEXT NOT NULL,
  label_en TEXT NOT NULL,
  label_zh TEXT,
  description_en TEXT,
  description_zh TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  is_public INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (filter_type, filter_value)
);

CREATE INDEX IF NOT EXISTS idx_subscription_filter_options_public
  ON subscription_filter_options(filter_type, is_public, sort_order, label_en);

UPDATE subscription_lists
SET
  name_zh = '报告更新',
  description_zh = '新的国家和疾病监测报告。',
  sort_order = 10
WHERE code = 'reports';

UPDATE subscription_lists
SET
  name_zh = '重点提醒',
  description_zh = '重要疫情、异常变化和高优先级监测提醒。',
  sort_order = 20
WHERE code = 'alerts';

UPDATE subscription_lists
SET
  name_zh = '每周摘要',
  description_zh = '每周汇总新的 GIDS 报告和重点变化。',
  sort_order = 30
WHERE code = 'weekly_digest';

