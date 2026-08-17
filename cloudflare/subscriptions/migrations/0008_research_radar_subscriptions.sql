INSERT INTO subscription_lists (
  id, code, name, description, default_frequency, is_public, created_at,
  name_zh, description_zh, sort_order
) VALUES (
  'list_research_digest',
  'research_digest',
  'Research Radar digest',
  'New peer-reviewed research, approved preprints, and evidence-gap updates.',
  'weekly',
  1,
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
  'Research Radar 研究摘要',
  '新发表的同行评议研究、已审核预印本与证据缺口更新。',
  40
)
ON CONFLICT(code) DO UPDATE SET
  name = excluded.name,
  description = excluded.description,
  default_frequency = excluded.default_frequency,
  is_public = excluded.is_public,
  name_zh = excluded.name_zh,
  description_zh = excluded.description_zh,
  sort_order = excluded.sort_order;

INSERT INTO subscription_filter_options (
  id, filter_type, filter_value, label_en, label_zh,
  description_en, description_zh, sort_order, is_public, created_at, updated_at
) VALUES
  ('research_topic_surveillance', 'research_topic', 'surveillance', 'Surveillance', '监测', '', '', 10, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_outbreak_investigation', 'research_topic', 'outbreak-investigation', 'Outbreak investigation', '暴发调查', '', '', 20, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_transmission_dynamics', 'research_topic', 'transmission-dynamics', 'Transmission dynamics', '传播动力学', '', '', 30, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_vaccination', 'research_topic', 'vaccination', 'Vaccination', '疫苗接种', '', '', 40, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_vaccine_effectiveness', 'research_topic', 'vaccine-effectiveness', 'Vaccine effectiveness', '疫苗有效性', '', '', 50, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_amr', 'research_topic', 'antimicrobial-resistance', 'Antimicrobial resistance', '抗微生物药物耐药性', '', '', 60, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_genomic', 'research_topic', 'genomic-epidemiology', 'Genomic epidemiology', '基因组流行病学', '', '', 70, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_diagnostics', 'research_topic', 'diagnostics', 'Diagnostics', '诊断', '', '', 80, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_treatment', 'research_topic', 'treatment', 'Treatment', '治疗', '', '', 90, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_climate', 'research_topic', 'climate-and-environment', 'Climate and environment', '气候与环境', '', '', 100, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_travel', 'research_topic', 'travel-medicine', 'Travel medicine', '旅行医学', '', '', 110, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_one_health', 'research_topic', 'one-health', 'One Health', '同一健康', '', '', 120, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('research_topic_policy', 'research_topic', 'health-policy', 'Health policy', '卫生政策', '', '', 130, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_systematic_review', 'study_type', 'systematic-review', 'Systematic review', '系统综述', '', '', 10, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_meta_analysis', 'study_type', 'meta-analysis', 'Meta-analysis', '荟萃分析', '', '', 20, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_guideline', 'study_type', 'guideline', 'Guideline', '指南', '', '', 30, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_trial', 'study_type', 'randomised-controlled-trial', 'Randomised controlled trial', '随机对照试验', '', '', 40, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_cohort', 'study_type', 'cohort-study', 'Cohort study', '队列研究', '', '', 50, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_case_control', 'study_type', 'case-control-study', 'Case-control study', '病例对照研究', '', '', 60, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_cross_sectional', 'study_type', 'cross-sectional-study', 'Cross-sectional study', '横断面研究', '', '', 70, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_modelling', 'study_type', 'mathematical-modelling', 'Mathematical modelling', '数学建模', '', '', 80, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_genomic', 'study_type', 'genomic-study', 'Genomic study', '基因组研究', '', '', 90, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_outbreak', 'study_type', 'outbreak-investigation', 'Outbreak investigation', '暴发调查', '', '', 100, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('study_type_commentary', 'study_type', 'commentary', 'Commentary', '评论', '', '', 110, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('peer_reviewed', 'peer_review_status', 'peer-reviewed', 'Peer reviewed', '同行评议', '', '', 10, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('peer_review_preprint', 'peer_review_status', 'preprint', 'Approved preprints', '已审核预印本', '', '', 20, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
ON CONFLICT(filter_type, filter_value) DO UPDATE SET
  label_en = excluded.label_en,
  label_zh = excluded.label_zh,
  description_en = excluded.description_en,
  description_zh = excluded.description_zh,
  sort_order = excluded.sort_order,
  is_public = excluded.is_public,
  updated_at = excluded.updated_at;
