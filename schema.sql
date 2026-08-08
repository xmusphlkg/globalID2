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
CREATE TYPE tasktype AS ENUM ('CRAWL_DATA', 'PROCESS_DATA', 'GENERATE_REPORT', 'GENERATE_SECTION', 'REVIEW_SECTION', 'UPDATE_DISEASE_KNOWLEDGE', 'EXPORT_DATA', 'SEND_EMAIL', 'AGENT_WORKFLOW');
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
	country_code VARCHAR(10) NOT NULL,
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

CREATE TABLE data_release_jobs (
	job_id VARCHAR(100) NOT NULL,
	name VARCHAR(255) NOT NULL,
	enabled BOOLEAN NOT NULL,
	priority VARCHAR(20) NOT NULL,
	auto_after_crawls BOOLEAN NOT NULL,
	include_git_push BOOLEAN NOT NULL,
	include_cloudflare_deploy BOOLEAN NOT NULL,
	require_clean_worktree BOOLEAN NOT NULL,
	interval_minutes INTEGER,
	daily_time VARCHAR(5),
	timezone VARCHAR(100),
	github_remote VARCHAR(100) NOT NULL,
	github_branch VARCHAR(255),
	cloudflare_project_name VARCHAR(255),
	commit_message_template VARCHAR(255) NOT NULL,
	notes TEXT,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (job_id)
)

;
CREATE INDEX idx_data_release_jobs_enabled ON data_release_jobs (enabled);
CREATE INDEX idx_data_release_jobs_job_id ON data_release_jobs (job_id);

CREATE TABLE disease_knowledge_briefs (
	disease_id VARCHAR(100) NOT NULL,
	language VARCHAR(20) NOT NULL,
	brief TEXT NOT NULL,
	definition TEXT,
	clinical_features TEXT,
	epidemiology TEXT,
	transmission TEXT,
	prevention TEXT,
	surveillance_note TEXT,
	clinical_summary TEXT,
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
	resolved_url VARCHAR(1000),
	title VARCHAR(500),
	license VARCHAR(200),
	status VARCHAR(30) NOT NULL,
	language VARCHAR(20) NOT NULL,
	raw_excerpt TEXT,
	content_text TEXT,
	content_sections JSON NOT NULL,
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

CREATE TABLE disease_taxonomy_nodes (
	node_code VARCHAR(120) NOT NULL,
	taxonomy_code VARCHAR(80) NOT NULL,
	facet VARCHAR(80) NOT NULL,
	node_type VARCHAR(40) NOT NULL,
	label_en VARCHAR(240) NOT NULL,
	label_zh VARCHAR(240),
	description TEXT,
	sort_order INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_taxonomy_node_label UNIQUE (taxonomy_code, facet, label_en),
	UNIQUE (node_code)
)

;
CREATE INDEX idx_disease_taxonomy_node_active ON disease_taxonomy_nodes (is_active);
CREATE INDEX idx_disease_taxonomy_node_taxonomy_facet ON disease_taxonomy_nodes (taxonomy_code, facet);

CREATE TABLE diseases (
	name VARCHAR(200) NOT NULL,
	name_en VARCHAR(200),
	category VARCHAR(100) NOT NULL,
	icd_10 VARCHAR(40),
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

CREATE TABLE disease_concept_assignments (
	disease_id VARCHAR(100) NOT NULL,
	node_code VARCHAR(120) NOT NULL,
	mapping_relation VARCHAR(30) NOT NULL,
	is_primary BOOLEAN NOT NULL,
	confidence_score FLOAT NOT NULL,
	assertion_status VARCHAR(30) NOT NULL,
	asserted_by VARCHAR(120),
	source_name VARCHAR(200),
	source_uri VARCHAR(1000),
	valid_from DATE,
	valid_to DATE,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_concept_assignment UNIQUE (disease_id, node_code),
	CONSTRAINT ck_disease_concept_assignment_mapping_relation CHECK (mapping_relation IN ('exact', 'narrower', 'broader', 'related', 'aggregate', 'ambiguous', 'unmapped')),
	CONSTRAINT ck_disease_concept_assignment_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1),
	CONSTRAINT ck_disease_concept_assignment_status CHECK (assertion_status IN ('proposed', 'approved', 'rejected', 'deprecated')),
	CONSTRAINT ck_disease_concept_assignment_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
	FOREIGN KEY(disease_id) REFERENCES standard_diseases (disease_id) ON DELETE CASCADE,
	FOREIGN KEY(node_code) REFERENCES disease_taxonomy_nodes (node_code) ON DELETE CASCADE
)

;
CREATE INDEX idx_disease_concept_assignment_disease ON disease_concept_assignments (disease_id);
CREATE INDEX idx_disease_concept_assignment_node ON disease_concept_assignments (node_code);
CREATE INDEX idx_disease_concept_assignment_status ON disease_concept_assignments (assertion_status);

CREATE TABLE disease_concept_relations (
	subject_disease_id VARCHAR(100) NOT NULL,
	object_disease_id VARCHAR(100) NOT NULL,
	relation_type VARCHAR(60) NOT NULL,
	comparability VARCHAR(30) NOT NULL,
	aggregation_policy VARCHAR(40) NOT NULL,
	is_hierarchical BOOLEAN NOT NULL,
	confidence_score FLOAT NOT NULL,
	assertion_status VARCHAR(30) NOT NULL,
	asserted_by VARCHAR(120),
	source_name VARCHAR(200),
	source_uri VARCHAR(1000),
	valid_from DATE,
	valid_to DATE,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_concept_relation UNIQUE (subject_disease_id, relation_type, object_disease_id),
	CONSTRAINT ck_disease_concept_relation_no_self_loop CHECK (subject_disease_id <> object_disease_id),
	CONSTRAINT ck_disease_concept_relation_comparability CHECK (comparability IN ('direct', 'conditional', 'not_comparable', 'unknown')),
	CONSTRAINT ck_disease_concept_relation_aggregation_policy CHECK (aggregation_policy IN ('none', 'direct_only', 'reported_aggregate', 'sum_disjoint', 'non_additive')),
	CONSTRAINT ck_disease_concept_relation_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1),
	CONSTRAINT ck_disease_concept_relation_status CHECK (assertion_status IN ('proposed', 'approved', 'rejected', 'deprecated')),
	CONSTRAINT ck_disease_concept_relation_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
	FOREIGN KEY(subject_disease_id) REFERENCES standard_diseases (disease_id) ON DELETE CASCADE,
	FOREIGN KEY(object_disease_id) REFERENCES standard_diseases (disease_id) ON DELETE CASCADE
)

;
CREATE INDEX idx_disease_concept_relation_object ON disease_concept_relations (object_disease_id);
CREATE INDEX idx_disease_concept_relation_subject ON disease_concept_relations (subject_disease_id);
CREATE INDEX idx_disease_concept_relation_type ON disease_concept_relations (relation_type);

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

CREATE TABLE disease_taxonomy_edges (
	parent_node_code VARCHAR(120) NOT NULL,
	child_node_code VARCHAR(120) NOT NULL,
	relation_type VARCHAR(40) NOT NULL,
	aggregation_policy VARCHAR(40) NOT NULL,
	sort_order INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_taxonomy_edge UNIQUE (parent_node_code, child_node_code, relation_type),
	CONSTRAINT ck_disease_taxonomy_edge_no_self_loop CHECK (parent_node_code <> child_node_code),
	CONSTRAINT ck_disease_taxonomy_edge_aggregation_policy CHECK (aggregation_policy IN ('none', 'direct_only', 'reported_aggregate', 'sum_disjoint', 'non_additive')),
	FOREIGN KEY(parent_node_code) REFERENCES disease_taxonomy_nodes (node_code) ON DELETE CASCADE,
	FOREIGN KEY(child_node_code) REFERENCES disease_taxonomy_nodes (node_code) ON DELETE CASCADE
)

;
CREATE INDEX idx_disease_taxonomy_edge_active ON disease_taxonomy_edges (is_active);
CREATE INDEX idx_disease_taxonomy_edge_child ON disease_taxonomy_edges (child_node_code);
CREATE INDEX idx_disease_taxonomy_edge_parent ON disease_taxonomy_edges (parent_node_code);

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
	source_id VARCHAR(120) DEFAULT '*' NOT NULL,
	series_id VARCHAR(160),
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
CREATE INDEX idx_mapping_lookup ON disease_mappings (country_code, source_id, local_name);
CREATE INDEX idx_mapping_series ON disease_mappings (series_id);
CREATE INDEX idx_mapping_source ON disease_mappings (source_id);
CREATE INDEX idx_mapping_target ON disease_mappings (disease_id);
CREATE UNIQUE INDEX idx_mapping_unique ON disease_mappings (disease_id, country_code, source_id, local_name);

CREATE TABLE disease_surveillance_series (
	series_code VARCHAR(180) NOT NULL,
	disease_id VARCHAR(100),
	target_group_code VARCHAR(120),
	country_code VARCHAR(10) NOT NULL,
	scope_code VARCHAR(20),
	source_system VARCHAR(160) NOT NULL,
	source_series_code VARCHAR(240) NOT NULL,
	source_label VARCHAR(500) NOT NULL,
	definition_version VARCHAR(80) NOT NULL,
	case_definition TEXT,
	case_definition_uri VARCHAR(1000),
	metric_type VARCHAR(60) NOT NULL,
	reporting_basis VARCHAR(60) NOT NULL,
	temporal_granularity VARCHAR(40) NOT NULL,
	unit VARCHAR(60) NOT NULL,
	mapping_relation VARCHAR(30) NOT NULL,
	comparability VARCHAR(30) NOT NULL,
	aggregation_policy VARCHAR(40) NOT NULL,
	availability_status VARCHAR(30) NOT NULL,
	missing_value_policy VARCHAR(40) NOT NULL,
	valid_from DATE,
	valid_to DATE,
	is_active BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_surveillance_series_source_version UNIQUE (source_system, country_code, source_series_code, metric_type, definition_version),
	CONSTRAINT ck_disease_surveillance_series_exactly_one_target CHECK ((disease_id IS NOT NULL AND target_group_code IS NULL) OR (disease_id IS NULL AND target_group_code IS NOT NULL)),
	CONSTRAINT ck_disease_surveillance_series_mapping_relation CHECK (mapping_relation IN ('exact', 'narrower', 'broader', 'related', 'aggregate', 'ambiguous', 'unmapped')),
	CONSTRAINT ck_disease_surveillance_series_comparability CHECK (comparability IN ('direct', 'conditional', 'not_comparable', 'unknown')),
	CONSTRAINT ck_disease_surveillance_series_aggregation_policy CHECK (aggregation_policy IN ('none', 'direct_only', 'reported_aggregate', 'sum_disjoint', 'non_additive')),
	CONSTRAINT ck_disease_surveillance_series_availability CHECK (availability_status IN ('active', 'historical', 'discontinued', 'not_available', 'unknown')),
	CONSTRAINT ck_disease_surveillance_series_missing_value_policy CHECK (missing_value_policy IN ('missing_is_unknown', 'explicit_zero_only', 'silence_means_zero', 'suppressed', 'not_applicable')),
	CONSTRAINT ck_disease_surveillance_series_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
	UNIQUE (series_code),
	FOREIGN KEY(disease_id) REFERENCES standard_diseases (disease_id) ON DELETE RESTRICT,
	FOREIGN KEY(country_code) REFERENCES countries (code) ON DELETE RESTRICT,
	FOREIGN KEY(scope_code) REFERENCES country_scopes (scope_code) ON DELETE SET NULL
)

;
CREATE INDEX idx_disease_surveillance_series_active ON disease_surveillance_series (is_active);
CREATE INDEX idx_disease_surveillance_series_country ON disease_surveillance_series (country_code);
CREATE INDEX idx_disease_surveillance_series_disease ON disease_surveillance_series (disease_id);
CREATE INDEX idx_disease_surveillance_series_group ON disease_surveillance_series (target_group_code);
CREATE INDEX idx_disease_surveillance_series_scope ON disease_surveillance_series (scope_code);
CREATE INDEX idx_disease_surveillance_series_source ON disease_surveillance_series (source_system);

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

CREATE TABLE agent_runs (
	task_id INTEGER NOT NULL,
	mode VARCHAR(40) NOT NULL,
	output_format VARCHAR(40) NOT NULL,
	prompt TEXT NOT NULL,
	status VARCHAR(30) NOT NULL,
	risk_level VARCHAR(20) NOT NULL,
	country_id INTEGER,
	search_scope VARCHAR(80) NOT NULL,
	memory_scope VARCHAR(40) NOT NULL,
	allowed_actions JSON NOT NULL,
	plan_json JSON NOT NULL,
	summary TEXT,
	findings JSON NOT NULL,
	citations JSON NOT NULL,
	artifacts JSON NOT NULL,
	open_questions JSON NOT NULL,
	actions_taken JSON NOT NULL,
	result_json JSON NOT NULL,
	budget_tokens_total INTEGER,
	budget_tokens_used INTEGER NOT NULL,
	replan_count INTEGER NOT NULL,
	search_round_count INTEGER NOT NULL,
	review_round_count INTEGER NOT NULL,
	step_count INTEGER NOT NULL,
	error_message TEXT,
	metadata JSON NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (task_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE,
	FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE SET NULL
)

;
CREATE INDEX idx_agent_runs_country ON agent_runs (country_id);
CREATE INDEX idx_agent_runs_created ON agent_runs (created_at);
CREATE INDEX idx_agent_runs_status ON agent_runs (status);
CREATE INDEX idx_agent_runs_task ON agent_runs (task_id);

CREATE TABLE disease_series_observations (
	time TIMESTAMP WITH TIME ZONE NOT NULL,
	series_code VARCHAR(180) NOT NULL,
	geography_key VARCHAR(240) NOT NULL,
	dimension_key VARCHAR(500) NOT NULL,
	dimensions JSON NOT NULL,
	value FLOAT,
	unit VARCHAR(80) NOT NULL,
	suppressed BOOLEAN NOT NULL,
	suppression_reason VARCHAR(240),
	quality_status VARCHAR(30) NOT NULL,
	raw_data JSON NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_disease_series_observation_identity UNIQUE (time, series_code, geography_key, dimension_key),
	CONSTRAINT ck_disease_series_observation_geography_key CHECK (geography_key <> ''),
	CONSTRAINT ck_disease_series_observation_dimension_key CHECK (dimension_key <> ''),
	CONSTRAINT ck_disease_series_observation_unit CHECK (unit <> ''),
	CONSTRAINT ck_disease_series_observation_value_or_suppressed CHECK (suppressed OR value IS NOT NULL),
	CONSTRAINT ck_disease_series_observation_quality_status CHECK (quality_status IN ('raw', 'validated', 'provisional', 'revised', 'final', 'rejected')),
	FOREIGN KEY(series_code) REFERENCES disease_surveillance_series (series_code) ON DELETE RESTRICT
)

;
CREATE INDEX idx_disease_series_observation_geography_time ON disease_series_observations (geography_key, time);
CREATE INDEX idx_disease_series_observation_quality ON disease_series_observations (quality_status, suppressed);
CREATE INDEX idx_disease_series_observation_series_time ON disease_series_observations (series_code, time);

CREATE TABLE disease_source_availability (
	availability_code VARCHAR(180) NOT NULL,
	source_system VARCHAR(160) NOT NULL,
	country_code VARCHAR(10) NOT NULL,
	target_kind VARCHAR(30) NOT NULL,
	target_code VARCHAR(120) NOT NULL,
	series_code VARCHAR(180),
	status VARCHAR(60) NOT NULL,
	reason_code VARCHAR(160),
	notes TEXT,
	valid_from DATE,
	valid_to DATE,
	missing_value_policy VARCHAR(40) NOT NULL,
	is_active BOOLEAN NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_disease_source_availability_target_kind CHECK (target_kind IN ('concept', 'group')),
	CONSTRAINT ck_disease_source_availability_status CHECK (status IN ('available', 'upstream_available_ingestion_pending', 'not_reported_by_source', 'planned', 'not_assessed', 'parser_blocked', 'mapping_missing')),
	CONSTRAINT ck_disease_source_availability_missing_policy CHECK (missing_value_policy IN ('missing_is_unknown', 'explicit_zero_only', 'silence_means_zero', 'suppressed', 'not_applicable')),
	CONSTRAINT ck_disease_source_availability_valid_range CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from),
	CONSTRAINT ck_disease_source_availability_series_required CHECK (status NOT IN ('available', 'upstream_available_ingestion_pending') OR series_code IS NOT NULL),
	UNIQUE (availability_code),
	FOREIGN KEY(country_code) REFERENCES countries (code) ON DELETE RESTRICT,
	FOREIGN KEY(series_code) REFERENCES disease_surveillance_series (series_code) ON DELETE RESTRICT
)

;
CREATE INDEX idx_disease_source_availability_series ON disease_source_availability (series_code);
CREATE INDEX idx_disease_source_availability_source_country ON disease_source_availability (source_system, country_code);
CREATE INDEX idx_disease_source_availability_status ON disease_source_availability (status, is_active);
CREATE INDEX idx_disease_source_availability_target ON disease_source_availability (target_kind, target_code);

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

CREATE TABLE agent_memories (
	run_id INTEGER,
	task_id INTEGER,
	memory_uuid VARCHAR(36) NOT NULL,
	scope VARCHAR(50) NOT NULL,
	memory_type VARCHAR(50) NOT NULL,
	content TEXT,
	summary TEXT,
	source_type VARCHAR(50),
	source_ref VARCHAR(500),
	content_hash VARCHAR(128) NOT NULL,
	embedding JSON NOT NULL,
	collection_name VARCHAR(100),
	qdrant_point_id VARCHAR(120),
	status VARCHAR(30) NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE SET NULL,
	UNIQUE (memory_uuid)
)

;
CREATE INDEX idx_agent_memories_created ON agent_memories (created_at);
CREATE INDEX idx_agent_memories_hash ON agent_memories (content_hash);
CREATE INDEX idx_agent_memories_run ON agent_memories (run_id);
CREATE INDEX idx_agent_memories_scope ON agent_memories (scope);
CREATE INDEX idx_agent_memories_task ON agent_memories (task_id);

CREATE TABLE agent_steps (
	run_id INTEGER NOT NULL,
	step_uuid VARCHAR(36) NOT NULL,
	step_key VARCHAR(120) NOT NULL,
	step_order INTEGER NOT NULL,
	step_type VARCHAR(40) NOT NULL,
	step_name VARCHAR(200) NOT NULL,
	status VARCHAR(30) NOT NULL,
	attempt INTEGER NOT NULL,
	input_summary TEXT,
	output_summary TEXT,
	input_payload JSON NOT NULL,
	output_payload JSON NOT NULL,
	prompt TEXT,
	system_prompt TEXT,
	response TEXT,
	model VARCHAR(120),
	provider VARCHAR(120),
	tokens JSON NOT NULL,
	duration FLOAT,
	error_message TEXT,
	metadata JSON NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,
	UNIQUE (step_uuid)
)

;
CREATE INDEX idx_agent_steps_created ON agent_steps (created_at);
CREATE INDEX idx_agent_steps_run ON agent_steps (run_id);
CREATE INDEX idx_agent_steps_status ON agent_steps (status);
CREATE UNIQUE INDEX idx_agent_steps_step_key ON agent_steps (run_id, step_key);

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

CREATE TABLE agent_conversations (
	run_id INTEGER NOT NULL,
	step_id INTEGER,
	conversation_uuid VARCHAR(36) NOT NULL,
	agent_role VARCHAR(50) NOT NULL,
	phase VARCHAR(50),
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
	prompt TEXT,
	system_prompt TEXT,
	response TEXT,
	model VARCHAR(120),
	provider VARCHAR(120),
	tokens JSON NOT NULL,
	duration FLOAT,
	temperature FLOAT,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(step_id) REFERENCES agent_steps (id) ON DELETE SET NULL,
	UNIQUE (conversation_uuid)
)

;
CREATE INDEX idx_agent_conversations_created ON agent_conversations (created_at);
CREATE INDEX idx_agent_conversations_run ON agent_conversations (run_id);
CREATE INDEX idx_agent_conversations_step ON agent_conversations (step_id);

CREATE TABLE agent_evidence (
	run_id INTEGER NOT NULL,
	step_id INTEGER,
	evidence_uuid VARCHAR(36) NOT NULL,
	evidence_type VARCHAR(30) NOT NULL,
	source_type VARCHAR(50) NOT NULL,
	source_name VARCHAR(200),
	title TEXT,
	url TEXT,
	resolved_url TEXT,
	content_snippet TEXT,
	content_hash VARCHAR(128) NOT NULL,
	confidence FLOAT NOT NULL,
	weight FLOAT NOT NULL,
	metadata JSON NOT NULL,
	id SERIAL NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(step_id) REFERENCES agent_steps (id) ON DELETE SET NULL,
	UNIQUE (evidence_uuid)
)

;
CREATE INDEX idx_agent_evidence_created ON agent_evidence (created_at);
CREATE UNIQUE INDEX idx_agent_evidence_hash ON agent_evidence (run_id, content_hash);
CREATE INDEX idx_agent_evidence_run ON agent_evidence (run_id);
CREATE INDEX idx_agent_evidence_step ON agent_evidence (step_id);
