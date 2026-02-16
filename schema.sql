-- Generated schema from SQLAlchemy metadata
-- Command: Base.metadata.create_all()
-- NOTE: The file below contains deterministic DDL (types, tables, indexes) as produced by SQLAlchemy.
-- Extensions (TimescaleDB / pgvector) and hypertable conversion are NOT applied automatically.
-- Suggested extension and hypertable commands (commented examples are included below):
--   -- CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
--   -- SELECT create_hypertable('disease_records', 'time', chunk_time_interval => INTERVAL '1 month');
--   -- CREATE EXTENSION IF NOT EXISTS pgvector;

-- == Metadata-based DDL (deterministic) ==
CREATE TYPE reporttype AS ENUM ('DAILY', 'WEEKLY', 'MONTHLY', 'SPECIAL');
CREATE TYPE reportstatus AS ENUM ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED', 'REVIEWING', 'PUBLISHED');

CREATE TABLE countries (
	code VARCHAR(10) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	name_en VARCHAR(100) NOT NULL, 
	name_local VARCHAR(100), 
	language VARCHAR(10) NOT NULL, 
	timezone VARCHAR(50) NOT NULL, 
	data_source_url VARCHAR(500), 
	data_source_type VARCHAR(50), 
	api_key TEXT, 
	crawler_config JSON NOT NULL, 
	parser_config JSON NOT NULL, 
	disease_mapping_rules JSON NOT NULL, 
	report_config JSON NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	last_crawl_time VARCHAR(50), 
	metadata JSON NOT NULL, 
	notes TEXT, 
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
)

;
CREATE INDEX idx_country_active ON countries (is_active);
CREATE INDEX idx_country_code ON countries (code);

CREATE TABLE diseases (
	name VARCHAR(200) NOT NULL, 
	name_en VARCHAR(200), 
	category VARCHAR(100) NOT NULL, 
	icd_10 VARCHAR(10), 
	icd_11 VARCHAR(20), 
	aliases JSON NOT NULL, 
	keywords JSON NOT NULL, 
	description TEXT, 
	symptoms TEXT, 
	transmission TEXT, 
	embedding JSON, 
	metadata JSON NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

;
CREATE INDEX idx_disease_active ON diseases (is_active);
CREATE INDEX idx_disease_category ON diseases (category);
CREATE INDEX idx_disease_icd10 ON diseases (icd_10);
CREATE INDEX idx_disease_name ON diseases (name);

CREATE TABLE disease_records (
	time TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	disease_id INTEGER NOT NULL, 
	country_id INTEGER NOT NULL, 
	cases INTEGER, 
	deaths INTEGER, 
	recoveries INTEGER, 
	active_cases INTEGER, 
	new_cases INTEGER, 
	new_deaths INTEGER, 
	new_recoveries INTEGER, 
	incidence_rate FLOAT, 
	mortality_rate FLOAT, 
	recovery_rate FLOAT, 
	region VARCHAR(100), 
	city VARCHAR(100), 
	data_source VARCHAR(200), 
	data_quality VARCHAR(20), 
	confidence_score FLOAT, 
	metadata JSON NOT NULL, 
	raw_data JSON, 
	PRIMARY KEY (time, disease_id, country_id), 
	FOREIGN KEY(disease_id) REFERENCES diseases (id) ON DELETE CASCADE, 
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_record_country ON disease_records (country_id);
CREATE INDEX idx_record_disease ON disease_records (disease_id);
CREATE INDEX idx_record_region ON disease_records (region);
CREATE INDEX idx_record_time ON disease_records (time);
CREATE INDEX idx_record_time_disease_country ON disease_records (time, disease_id, country_id);

CREATE TABLE reports (
	title VARCHAR(500) NOT NULL, 
	report_type reporttype NOT NULL, 
	status reportstatus NOT NULL, 
	country_id INTEGER NOT NULL, 
	period_start TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	period_end TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	summary TEXT, 
	key_findings JSON NOT NULL, 
	recommendations JSON NOT NULL, 
	generation_config JSON NOT NULL, 
	ai_model_used VARCHAR(100), 
	generation_time FLOAT, 
	token_usage JSON, 
	quality_score FLOAT, 
	reviewed_by VARCHAR(100), 
	reviewed_at TIMESTAMP WITHOUT TIME ZONE, 
	published_at TIMESTAMP WITHOUT TIME ZONE, 
	published_url VARCHAR(500), 
	html_path VARCHAR(500), 
	pdf_path VARCHAR(500), 
	markdown_path VARCHAR(500), 
	metadata JSON NOT NULL, 
	error_message TEXT, 
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_report_country ON reports (country_id);
CREATE INDEX idx_report_country_period ON reports (country_id, period_start, period_end);
CREATE INDEX idx_report_period ON reports (period_start, period_end);
CREATE INDEX idx_report_status ON reports (status);
CREATE INDEX idx_report_type ON reports (report_type);

CREATE TABLE report_sections (
	report_id INTEGER NOT NULL, 
	section_type VARCHAR(50) NOT NULL, 
	section_order INTEGER NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	content TEXT NOT NULL, 
	content_html TEXT, 
	prompt_used TEXT, 
	ai_model VARCHAR(100), 
	generation_time FLOAT, 
	token_count INTEGER, 
	data_sources JSON NOT NULL, 
	charts JSON NOT NULL, 
	tables JSON NOT NULL, 
	is_verified BOOLEAN NOT NULL, 
	verification_notes TEXT, 
	metadata JSON NOT NULL, 
	id SERIAL NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_section_order ON report_sections (report_id, section_order);
CREATE INDEX idx_section_report ON report_sections (report_id);
CREATE INDEX idx_section_type ON report_sections (section_type);

CREATE TABLE crawl_runs (
	id SERIAL NOT NULL,
	country_code VARCHAR(10) NOT NULL,
	source VARCHAR(50) NOT NULL,
	status VARCHAR(20) NOT NULL,
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITHOUT TIME ZONE,
	new_reports INTEGER,
	processed_reports INTEGER,
	total_records INTEGER,
	raw_dir VARCHAR(500),
	metadata JSON NOT NULL,
	error_message TEXT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;
CREATE INDEX idx_crawl_run_country ON crawl_runs (country_code);
CREATE INDEX idx_crawl_run_status ON crawl_runs (status);
CREATE INDEX idx_crawl_run_started_at ON crawl_runs (started_at);

CREATE TABLE crawl_raw_pages (
	id SERIAL NOT NULL,
	run_id INTEGER NOT NULL,
	url VARCHAR(1000) NOT NULL,
	title VARCHAR(500),
	content_path VARCHAR(500) NOT NULL,
	content_hash VARCHAR(64),
	content_type VARCHAR(50),
	fetched_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	source VARCHAR(50),
	metadata JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE,
	UNIQUE (run_id, url)
)

;
CREATE INDEX idx_crawl_raw_page_run ON crawl_raw_pages (run_id);
CREATE INDEX idx_crawl_raw_page_url ON crawl_raw_pages (url);
CREATE INDEX idx_crawl_raw_page_hash ON crawl_raw_pages (content_hash);
