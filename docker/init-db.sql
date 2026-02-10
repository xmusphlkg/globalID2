-- GlobalID V2 数据库初始化脚本

-- 启用必要的扩展
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 创建 schema
CREATE SCHEMA IF NOT EXISTS globalid;

-- 设置默认 schema
SET search_path TO globalid, public;

-- 创建枚举类型
CREATE TYPE validation_status AS ENUM ('pending', 'approved', 'rejected', 'needs_review');
CREATE TYPE report_status AS ENUM ('draft', 'reviewing', 'approved', 'published', 'archived');
CREATE TYPE ai_model_type AS ENUM ('gpt-4', 'gpt-4-turbo', 'gpt-3.5-turbo', 'claude-3', 'claude-3-opus', 'other');

-- ============================================================================
-- 1. 国家表
-- ============================================================================
CREATE TABLE countries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(3) UNIQUE NOT NULL,
    name_en VARCHAR(100) NOT NULL,
    name_local VARCHAR(100),
    is_active BOOLEAN DEFAULT true,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE countries IS '国家基础信息表';
COMMENT ON COLUMN countries.config IS '国家特定配置，如爬虫类名、语言、时区等';

-- ============================================================================
-- 2. 疾病表
-- ============================================================================
CREATE TABLE diseases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name_en VARCHAR(200) NOT NULL,
    name_local JSONB DEFAULT '{}',
    aliases TEXT[] DEFAULT '{}',
    category VARCHAR(50),
    icd_code VARCHAR(20),
    embedding vector(1536),
    metadata JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE diseases IS '疾病注册表，支持多语言和向量搜索';
COMMENT ON COLUMN diseases.embedding IS 'OpenAI embedding 向量，用于语义搜索';
COMMENT ON COLUMN diseases.name_local IS '本地化名称，格式：{"zh": "新冠肺炎", "es": "COVID-19"}';

-- ============================================================================
-- 3. 疾病记录表（时序数据）
-- ============================================================================
CREATE TABLE disease_records (
    id UUID DEFAULT uuid_generate_v4(),
    country_id UUID REFERENCES countries(id),
    disease_id UUID REFERENCES diseases(id),
    record_date DATE NOT NULL,
    cases INTEGER,
    deaths INTEGER,
    location VARCHAR(200),
    source_url TEXT,
    raw_data JSONB,
    processed_data JSONB,
    confidence_score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, record_date)
);

COMMENT ON TABLE disease_records IS '疾病记录时序数据';

-- 转换为时序表
SELECT create_hypertable('disease_records', 'record_date');

-- 设置自动压缩策略（90天后压缩）
SELECT add_compression_policy('disease_records', INTERVAL '90 days');

-- ============================================================================
-- 4. 报告表
-- ============================================================================
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    country_id UUID REFERENCES countries(id),
    report_month DATE NOT NULL,
    status report_status DEFAULT 'draft',
    metadata JSONB DEFAULT '{}',
    generated_at TIMESTAMP,
    published_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(country_id, report_month)
);

COMMENT ON TABLE reports IS '月度报告主表';

-- ============================================================================
-- 5. 报告章节表
-- ============================================================================
CREATE TABLE report_sections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id UUID REFERENCES reports(id) ON DELETE CASCADE,
    disease_id UUID REFERENCES diseases(id),
    section_type VARCHAR(50) NOT NULL,
    content TEXT,
    metadata JSONB DEFAULT '{}',
    quality_score FLOAT,
    iterations INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE report_sections IS '报告章节内容';
COMMENT ON COLUMN report_sections.iterations IS 'AI 生成迭代次数';

-- ============================================================================
-- 6. AI 交互记录表
-- ============================================================================
CREATE TABLE ai_interactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_hash VARCHAR(64) UNIQUE,
    model ai_model_type NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost DECIMAL(10, 6),
    response_time FLOAT,
    cached BOOLEAN DEFAULT false,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE ai_interactions IS 'AI API 调用记录，用于成本追踪';

-- ============================================================================
-- 7. 验证结果表
-- ============================================================================
CREATE TABLE validation_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID REFERENCES report_sections(id),
    validator_type VARCHAR(50) NOT NULL,
    status validation_status DEFAULT 'pending',
    score FLOAT,
    issues JSONB DEFAULT '[]',
    suggestions JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE validation_results IS '内容验证结果';

-- ============================================================================
-- 8. 人工审查队列
-- ============================================================================
CREATE TABLE human_review_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_type VARCHAR(50) NOT NULL,
    item_id UUID NOT NULL,
    priority INTEGER DEFAULT 0,
    reason TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    assigned_to VARCHAR(100),
    reviewed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE human_review_queue IS '需要人工审查的项目队列';

-- ============================================================================
-- 索引
-- ============================================================================

-- 疾病表索引
CREATE INDEX idx_diseases_embedding ON diseases USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_diseases_category ON diseases(category) WHERE is_active = true;

-- 疾病记录索引
CREATE INDEX idx_disease_records_country ON disease_records(country_id);
CREATE INDEX idx_disease_records_disease ON disease_records(disease_id);
CREATE INDEX idx_disease_records_date ON disease_records(record_date DESC);
CREATE INDEX idx_disease_records_location ON disease_records(location);

-- 报告索引
CREATE INDEX idx_reports_country_month ON reports(country_id, report_month DESC);
CREATE INDEX idx_reports_status ON reports(status);

-- AI 交互索引
CREATE INDEX idx_ai_interactions_hash ON ai_interactions(request_hash);
CREATE INDEX idx_ai_interactions_created ON ai_interactions(created_at DESC);
CREATE INDEX idx_ai_interactions_cached ON ai_interactions(cached);

-- 验证结果索引
CREATE INDEX idx_validation_results_section ON validation_results(section_id);
CREATE INDEX idx_validation_results_status ON validation_results(status);

-- ============================================================================
-- 触发器：自动更新 updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_countries_updated_at
    BEFORE UPDATE ON countries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_diseases_updated_at
    BEFORE UPDATE ON diseases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_reports_updated_at
    BEFORE UPDATE ON reports
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_report_sections_updated_at
    BEFORE UPDATE ON report_sections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- 视图：AI 成本统计
-- ============================================================================

CREATE OR REPLACE VIEW v_ai_cost_summary AS
SELECT
    DATE_TRUNC('day', created_at) as date,
    model,
    COUNT(*) as requests,
    SUM(CASE WHEN cached THEN 1 ELSE 0 END) as cache_hits,
    ROUND(100.0 * SUM(CASE WHEN cached THEN 1 ELSE 0 END) / COUNT(*), 2) as cache_hit_rate,
    SUM(prompt_tokens) as total_prompt_tokens,
    SUM(completion_tokens) as total_completion_tokens,
    SUM(cost) as total_cost,
    AVG(response_time) as avg_response_time
FROM ai_interactions
GROUP BY DATE_TRUNC('day', created_at), model
ORDER BY date DESC, total_cost DESC;

-- ============================================================================
-- 插入初始数据
-- ============================================================================

-- 中国
INSERT INTO countries (code, name_en, name_local, config) VALUES
('CHN', 'China', '中国', '{
    "crawler": "CNCDCCrawler",
    "language": "zh",
    "timezone": "Asia/Shanghai",
    "data_sources": ["cdc.gov.cn", "nhc.gov.cn"]
}'::jsonb);

-- 初始疾病列表
INSERT INTO diseases (name_en, name_local, category, aliases) VALUES
('COVID-19', '{"zh": "新冠肺炎"}'::jsonb, 'respiratory', ARRAY['新冠', 'SARS-CoV-2', 'Coronavirus Disease 2019']),
('Influenza', '{"zh": "流感"}'::jsonb, 'respiratory', ARRAY['流行性感冒', 'Flu']),
('H5N1', '{"zh": "禽流感H5N1"}'::jsonb, 'respiratory', ARRAY['禽流感', 'Bird Flu']),
('Tuberculosis', '{"zh": "结核病"}'::jsonb, 'respiratory', ARRAY['TB', '肺结核']),
('Dengue', '{"zh": "登革热"}'::jsonb, 'vector-borne', ARRAY['登革热病毒']),
('Malaria', '{"zh": "疟疾"}'::jsonb, 'vector-borne', ARRAY['疟原虫病']);

-- ============================================================================
-- 数据库注释
-- ============================================================================

COMMENT ON DATABASE globalid IS 'GlobalID v2.0 - Intelligent Disease Surveillance System';

-- 完成
\echo 'Database initialization completed successfully!'
\echo 'Schema: globalid'
\echo 'Extensions: timescaledb, vector, uuid-ossp'
\echo 'Tables: 8'
\echo 'Views: 1'
\echo 'Initial data: 1 country, 6 diseases'
