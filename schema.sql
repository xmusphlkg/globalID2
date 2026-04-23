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
CREATE TYPE reportstatus AS ENUM ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED', 'REVIEWING', 'APPROVED', 'PUBLISHED');
CREATE TYPE tasktype AS ENUM ('CRAWL_DATA', 'PROCESS_DATA', 'GENERATE_REPORT', 'GENERATE_SECTION', 'REVIEW_SECTION', 'EXPORT_DATA', 'SEND_EMAIL');
CREATE TYPE taskstatus AS ENUM ('PENDING', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'RETRYING');
CREATE TYPE taskpriority AS ENUM ('LOW', 'NORMAL', 'HIGH', 'URGENT');
CREATE TYPE reportsectionrunstatus AS ENUM ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');

CREATE TABLE ai_provider_configs (
	provider_key VARCHAR(120) NOT NULL,
	provider_name VARCHAR(80) NOT NULL,
	display_name VARCHAR(200) NOT NULL,
	api_style VARCHAR(50) NOT NULL,
	base_url VARCHAR(500),
	api_key TEXT,
	organization VARCHAR(200),
	extra_headers JSON NOT NULL,
	extra_config JSON NOT NULL,
	is_active BOOLEAN NOT NULL,
	priority INTEGER NOT NULL,
	last_check_status VARCHAR(30) NOT NULL,
	last_check_message TEXT,
	last_checked_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (provider_key)
)

;
CREATE INDEX idx_ai_provider_active ON ai_provider_configs (is_active);
CREATE INDEX idx_ai_provider_name ON ai_provider_configs (provider_name);
CREATE INDEX idx_ai_provider_priority ON ai_provider_configs (priority);

CREATE TABLE automation_jobs (
	job_id VARCHAR(100) NOT NULL,
	name VARCHAR(255) NOT NULL,
	country_code VARCHAR(2) NOT NULL,
	source VARCHAR(50) NOT NULL,
	enabled BOOLEAN NOT NULL,
	priority VARCHAR(20) NOT NULL,
	process BOOLEAN NOT NULL,
	save_raw BOOLEAN NOT NULL,
	fill_missing BOOLEAN NOT NULL,
	force BOOLEAN NOT NULL,
	retry_threshold INTEGER NOT NULL,
	interval_minutes INTEGER,
	daily_time VARCHAR(5),
	timezone VARCHAR(100),
	notes TEXT,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (job_id)
)

;
CREATE INDEX idx_automation_jobs_country ON automation_jobs (country_code);
CREATE INDEX idx_automation_jobs_enabled ON automation_jobs (enabled);

CREATE TABLE countries (
	code VARCHAR(10) NOT NULL,
	name VARCHAR(100) NOT NULL,
	name_en VARCHAR(100) NOT NULL,
	name_local VARCHAR(100),
	language VARCHAR(20) NOT NULL,
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
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (code)
)

;
CREATE INDEX idx_country_active ON countries (is_active);
CREATE INDEX idx_country_code ON countries (code);

CREATE TABLE crawl_runs (
	country_code VARCHAR(10) NOT NULL,
	source VARCHAR(50) NOT NULL,
	status VARCHAR(20) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	new_reports INTEGER,
	processed_reports INTEGER,
	total_records INTEGER,
	raw_dir VARCHAR(500),
	metadata JSON NOT NULL,
	error_message TEXT,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;
CREATE INDEX idx_crawl_run_country ON crawl_runs (country_code);
CREATE INDEX idx_crawl_run_started_at ON crawl_runs (started_at);
CREATE INDEX idx_crawl_run_status ON crawl_runs (status);

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
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
)

;
CREATE INDEX idx_disease_active ON diseases (is_active);
CREATE INDEX idx_disease_category ON diseases (category);
CREATE INDEX idx_disease_icd10 ON diseases (icd_10);
CREATE INDEX idx_disease_name ON diseases (name);

CREATE TABLE standard_diseases (
	disease_id VARCHAR(100) NOT NULL,
	standard_name_en VARCHAR(200) NOT NULL,
	standard_name_zh VARCHAR(200),
	category VARCHAR(100),
	icd_10 VARCHAR(20),
	icd_11 VARCHAR(20),
	description TEXT,
	source VARCHAR(100) NOT NULL,
	metadata JSON NOT NULL,
	is_active BOOLEAN NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (disease_id)
)

;
CREATE INDEX idx_std_disease_category ON standard_diseases (category);
CREATE INDEX idx_std_disease_id ON standard_diseases (disease_id);
CREATE INDEX idx_std_disease_name_en ON standard_diseases (standard_name_en);

CREATE TABLE country_briefs (
	country_code VARCHAR(10) NOT NULL,
	language VARCHAR(20) NOT NULL,
	brief TEXT,
	surveillance_system TEXT,
	coverage_interpretation TEXT,
	reporting_cadence TEXT,
	data_limitations TEXT,
	source_summary TEXT,
	status VARCHAR(30) NOT NULL,
	quality_score FLOAT,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_country_brief UNIQUE (country_code, language)
)

;
CREATE INDEX idx_country_brief_country ON country_briefs (country_code);
CREATE INDEX idx_country_brief_language ON country_briefs (language);
CREATE INDEX idx_country_brief_status ON country_briefs (status);

CREATE TABLE disease_knowledge_briefs (
	disease_id VARCHAR(100) NOT NULL,
	language VARCHAR(20) NOT NULL,
	brief TEXT NOT NULL,
	clinical_summary TEXT,
	transmission TEXT,
	prevention TEXT,
	risk_groups TEXT,
	source_ids JSON NOT NULL,
	source_attribution JSON NOT NULL,
	disclaimer TEXT,
	model VARCHAR(120),
	status VARCHAR(30) NOT NULL,
	source_confidence VARCHAR(30) NOT NULL,
	quality_score FLOAT,
	review_notes TEXT,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_knowledge_brief UNIQUE (disease_id, language)
)

;
CREATE INDEX idx_knowledge_brief_disease ON disease_knowledge_briefs (disease_id);
CREATE INDEX idx_knowledge_brief_language ON disease_knowledge_briefs (language);
CREATE INDEX idx_knowledge_brief_status ON disease_knowledge_briefs (status);

CREATE TABLE disease_knowledge_sources (
	disease_id VARCHAR(100) NOT NULL,
	source_type VARCHAR(40) NOT NULL,
	source_name VARCHAR(120) NOT NULL,
	url VARCHAR(1000) NOT NULL,
	title VARCHAR(500),
	license VARCHAR(200),
	status VARCHAR(30) NOT NULL,
	language VARCHAR(20) NOT NULL,
	raw_excerpt TEXT,
	raw_excerpt_hash VARCHAR(64),
	fetched_at TIMESTAMP WITH TIME ZONE,
	review_status VARCHAR(30) NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_knowledge_source UNIQUE (disease_id, source_type, url)
)

;
CREATE INDEX idx_knowledge_source_disease ON disease_knowledge_sources (disease_id);
CREATE INDEX idx_knowledge_source_review ON disease_knowledge_sources (review_status);
CREATE INDEX idx_knowledge_source_type ON disease_knowledge_sources (source_type);

CREATE TABLE ai_models (
	provider_id INTEGER NOT NULL,
	model_key VARCHAR(200) NOT NULL,
	model_name VARCHAR(120) NOT NULL,
	display_name VARCHAR(200) NOT NULL,
	model_type VARCHAR(50) NOT NULL,
	api_style VARCHAR(50),
	temperature FLOAT,
	max_tokens INTEGER,
	extra_params JSON NOT NULL,
	is_enabled BOOLEAN NOT NULL,
	is_default BOOLEAN NOT NULL,
	priority INTEGER NOT NULL,
	last_check_status VARCHAR(30) NOT NULL,
	last_check_message TEXT,
	last_checked_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(provider_id) REFERENCES ai_provider_configs (id) ON DELETE CASCADE,
	UNIQUE (model_key)
)

;
CREATE INDEX idx_ai_model_enabled ON ai_models (is_enabled);
CREATE INDEX idx_ai_model_priority ON ai_models (priority);
CREATE INDEX idx_ai_model_provider ON ai_models (provider_id);

CREATE TABLE country_scopes (
	scope_code VARCHAR(20) NOT NULL,
	country_code VARCHAR(10) NOT NULL,
	scope_type VARCHAR(30) NOT NULL,
	language_code VARCHAR(20),
	display_name VARCHAR(120),
	is_default BOOLEAN NOT NULL,
	is_active BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (scope_code),
	FOREIGN KEY(country_code) REFERENCES countries (code) ON DELETE CASCADE
)

;
CREATE INDEX idx_country_scope_active ON country_scopes (is_active);
CREATE INDEX idx_country_scope_code ON country_scopes (scope_code);
CREATE INDEX idx_country_scope_country ON country_scopes (country_code);
CREATE INDEX idx_country_scope_type ON country_scopes (scope_type);

CREATE TABLE crawl_raw_pages (
	run_id INTEGER NOT NULL,
	url VARCHAR(1000) NOT NULL,
	title VARCHAR(500),
	content_path VARCHAR(500) NOT NULL,
	content_hash VARCHAR(64),
	content_type VARCHAR(50),
	fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
	source VARCHAR(100),
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_crawl_raw_pages_run_url UNIQUE (run_id, url),
	FOREIGN KEY(run_id) REFERENCES crawl_runs (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_crawl_raw_page_hash ON crawl_raw_pages (content_hash);
CREATE INDEX idx_crawl_raw_page_run ON crawl_raw_pages (run_id);
CREATE INDEX idx_crawl_raw_page_url ON crawl_raw_pages (url);

CREATE TABLE disease_learning_suggestions (
	country_code VARCHAR(10) NOT NULL,
	local_name VARCHAR(500) NOT NULL,
	source_url TEXT,
	context TEXT,
	occurrence_count INTEGER NOT NULL,
	suggested_disease_id VARCHAR(100),
	suggested_standard_name VARCHAR(200),
	ai_confidence FLOAT,
	ai_reasoning TEXT,
	status VARCHAR(20) NOT NULL,
	reviewed_by VARCHAR(100),
	review_notes TEXT,
	final_disease_id VARCHAR(100),
	final_mapping_id INTEGER,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(country_code) REFERENCES countries (code) ON DELETE CASCADE
)

;
CREATE INDEX idx_learning_confidence ON disease_learning_suggestions (ai_confidence);
CREATE INDEX idx_learning_country ON disease_learning_suggestions (country_code);
CREATE INDEX idx_learning_occurrence ON disease_learning_suggestions (occurrence_count);
CREATE INDEX idx_learning_status ON disease_learning_suggestions (status);
CREATE UNIQUE INDEX idx_learning_unique_country_local ON disease_learning_suggestions (country_code, local_name);

CREATE TABLE disease_records (
	time TIMESTAMP WITH TIME ZONE NOT NULL,
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

CREATE TABLE population_records (
	country_id INTEGER NOT NULL,
	year INTEGER NOT NULL,
	population FLOAT NOT NULL,
	source VARCHAR(100) NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_population_country_year UNIQUE (country_id, year),
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_population_country ON population_records (country_id);
CREATE INDEX idx_population_country_year ON population_records (country_id, year);
CREATE INDEX idx_population_year ON population_records (year);

CREATE TABLE reports (
	report_uuid UUID NOT NULL,
	title VARCHAR(500) NOT NULL,
	report_type reporttype NOT NULL,
	status reportstatus NOT NULL,
	country_id INTEGER NOT NULL,
	period_start TIMESTAMP WITH TIME ZONE NOT NULL,
	period_end TIMESTAMP WITH TIME ZONE NOT NULL,
	summary TEXT,
	key_findings JSON NOT NULL,
	recommendations JSON NOT NULL,
	generation_config JSON NOT NULL,
	ai_model_used VARCHAR(100),
	generation_time FLOAT,
	token_usage JSON,
	quality_score FLOAT,
	reviewed_by VARCHAR(100),
	reviewed_at TIMESTAMP WITH TIME ZONE,
	published_at TIMESTAMP WITH TIME ZONE,
	published_url VARCHAR(500),
	html_path VARCHAR(500),
	pdf_path VARCHAR(500),
	markdown_path VARCHAR(500),
	metadata JSON NOT NULL,
	error_message TEXT,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (report_uuid),
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_report_country ON reports (country_id);
CREATE INDEX idx_report_country_period ON reports (country_id, period_start, period_end);
CREATE INDEX idx_report_period ON reports (period_start, period_end);
CREATE INDEX idx_report_status ON reports (status);
CREATE INDEX idx_report_type ON reports (report_type);

CREATE TABLE disease_mappings (
	disease_id VARCHAR(200) NOT NULL,
	country_code VARCHAR(20) NOT NULL,
	local_name VARCHAR(500) NOT NULL,
	is_primary BOOLEAN NOT NULL,
	is_alias BOOLEAN NOT NULL,
	priority INTEGER NOT NULL,
	usage_count INTEGER NOT NULL,
	confidence_score FLOAT NOT NULL,
	category VARCHAR(100),
	source VARCHAR(100),
	metadata JSON NOT NULL,
	is_active BOOLEAN NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(country_code) REFERENCES country_scopes (scope_code) ON DELETE CASCADE
)

;
CREATE INDEX idx_mapping_active ON disease_mappings (is_active);
CREATE INDEX idx_mapping_lookup ON disease_mappings (country_code, local_name);
CREATE INDEX idx_mapping_target ON disease_mappings (disease_id);
CREATE UNIQUE INDEX idx_mapping_unique ON disease_mappings (disease_id, country_code, local_name);

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
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_section_order ON report_sections (report_id, section_order);
CREATE INDEX idx_section_report ON report_sections (report_id);
CREATE INDEX idx_section_type ON report_sections (section_type);

CREATE TABLE tasks (
	task_uuid VARCHAR(36) NOT NULL,
	task_type tasktype NOT NULL,
	task_name VARCHAR(500) NOT NULL,
	description TEXT,
	status taskstatus NOT NULL,
	priority taskpriority NOT NULL,
	country_id INTEGER,
	report_id INTEGER,
	parent_task_id INTEGER,
	progress INTEGER NOT NULL,
	total_steps INTEGER NOT NULL,
	completed_steps INTEGER NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	estimated_duration INTEGER,
	actual_duration INTEGER,
	retry_count INTEGER NOT NULL,
	max_retries INTEGER NOT NULL,
	last_error TEXT,
	input_data JSON NOT NULL,
	output_data JSON NOT NULL,
	tags JSON NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (task_uuid),
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE SET NULL,
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE SET NULL,
	FOREIGN KEY(parent_task_id) REFERENCES tasks (id) ON DELETE SET NULL
)

;
CREATE INDEX idx_task_country ON tasks (country_id);
CREATE INDEX idx_task_created ON tasks (created_at);
CREATE INDEX idx_task_parent ON tasks (parent_task_id);
CREATE INDEX idx_task_report ON tasks (report_id);
CREATE INDEX idx_task_status ON tasks (status);
CREATE INDEX idx_task_type ON tasks (task_type);
CREATE INDEX idx_task_uuid ON tasks (task_uuid);

CREATE TABLE report_section_runs (
	run_uuid UUID NOT NULL,
	report_id INTEGER NOT NULL,
	section_id INTEGER,
	section_type VARCHAR(50) NOT NULL,
	disease_name VARCHAR(200),
	status reportsectionrunstatus NOT NULL,
	provider VARCHAR(100),
	model VARCHAR(100),
	temperature FLOAT,
	max_tokens INTEGER,
	token_usage JSON NOT NULL,
	quality_scores JSON NOT NULL,
	revision_count INTEGER NOT NULL,
	error_message TEXT,
	started_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_uuid),
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE,
	FOREIGN KEY(section_id) REFERENCES report_sections (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_section_run_report ON report_section_runs (report_id);
CREATE INDEX idx_section_run_section ON report_section_runs (section_id);
CREATE INDEX idx_section_run_status ON report_section_runs (status);

CREATE TABLE task_dependencies (
	task_id INTEGER NOT NULL,
	depends_on_task_id INTEGER NOT NULL,
	dependency_type VARCHAR(50) NOT NULL,
	is_required BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE,
	FOREIGN KEY(depends_on_task_id) REFERENCES tasks (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_dependency_depends ON task_dependencies (depends_on_task_id);
CREATE INDEX idx_dependency_task ON task_dependencies (task_id);

CREATE TABLE task_workbook (
	task_id INTEGER NOT NULL,
	entry_uuid VARCHAR(36) NOT NULL,
	entry_type VARCHAR(50) NOT NULL,
	title VARCHAR(500) NOT NULL,
	content TEXT NOT NULL,
	content_type VARCHAR(50) NOT NULL,
	prompt TEXT,
	response TEXT,
	model_used VARCHAR(100),
	tokens_used INTEGER,
	cost FLOAT,
	duration FLOAT,
	success BOOLEAN NOT NULL,
	error_message TEXT,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE,
	UNIQUE (entry_uuid)
)

;
CREATE INDEX idx_workbook_created ON task_workbook (created_at);
CREATE INDEX idx_workbook_task ON task_workbook (task_id);
CREATE INDEX idx_workbook_type ON task_workbook (entry_type);
CREATE INDEX idx_workbook_uuid ON task_workbook (entry_uuid);

CREATE TABLE ai_conversations (
	run_id INTEGER NOT NULL,
	report_id INTEGER NOT NULL,
	section_id INTEGER,
	agent VARCHAR(50) NOT NULL,
	role VARCHAR(50),
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
	prompt TEXT,
	system_prompt TEXT,
	response TEXT,
	model VARCHAR(100),
	provider VARCHAR(100),
	tokens JSON NOT NULL,
	duration FLOAT,
	temperature FLOAT,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES report_section_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(report_id) REFERENCES reports (id) ON DELETE CASCADE,
	FOREIGN KEY(section_id) REFERENCES report_sections (id) ON DELETE CASCADE
)

;
CREATE INDEX idx_ai_conv_run ON ai_conversations (run_id);
CREATE INDEX idx_ai_conv_section ON ai_conversations (section_id);
