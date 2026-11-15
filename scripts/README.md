# Script index

Run commands from the repository root using `venv/bin/python` (or the script's
shell/Node entrypoint). Use `PYTHONPATH=.` for scripts that import application
modules without installing the package. A script name is not an execution
safety guarantee: imports, configuration, repairs and publishers can change
the database or external state. Consult the corresponding guide and supported
arguments before running a command.

## Common entrypoints

| Task | Entry / guide |
| --- | --- |
| CLI and initial database setup | `venv/bin/python main.py --help`; [Quick start](../QUICKSTART.md) |
| Worker and scheduler readiness | [check_task_runtime.py](check_task_runtime.py) |
| Managed runtime restart | [restart_task_runtime.py](restart_task_runtime.py); [operations](../docs/operations/DEVELOPMENT.md) |
| Publication | [current publication workflow](../docs/publishing/MAIN_SITE_JSON_PUBLICATION_IMPLEMENTATION.md) |
| Source onboarding and imports | [source directory](../docs/data-sources/README.md) |
| Legacy dataset rebuild | [full_rebuild_database.py](full_rebuild_database.py); [rebuild guide](../docs/operations/DATABASE_REBUILD.md) |
| Repository guardrails | [check_repository_boundaries.sh](check_repository_boundaries.sh), [check_legacy_disease_access.py](check_legacy_disease_access.py) |

Existing runtime, publishing and country-import entrypoints retain their paths
because systemd, CI, application code and operational commands call them.
Historical migrations now live in [maintenance/](maintenance/README.md), and
one-off CSV preparation lives in [imports/](imports/README.md). Do not add new
ad hoc schema patches when a managed Alembic revision is appropriate.

Retired single-language mapping/reimport tools, duplicate task-table creators,
manual CN insertion, the legacy smoke query and the unguarded test-task purge
have been removed. Use the CLI/migration workflow and isolated test fixtures.
Normal application startup never requires a full data rebuild.

## Runtime and scheduling

| Script | Purpose |
| --- | --- |
| [configure_europe_automation.py](configure_europe_automation.py) | Idempotently configure dynamic European surveillance refresh jobs. |
| [configure_half_hour_ingestion.py](configure_half_hour_ingestion.py) | Spread enabled ingestion jobs over a 30-minute cycle. |
| [configure_iceland_automation.py](configure_iceland_automation.py) | Idempotently configure Iceland surveillance refresh jobs. |
| [configure_ingestion_coverage.py](configure_ingestion_coverage.py) | Idempotently add ingestion jobs for implemented but unscheduled pipelines. |
| [configure_nz_dhb_automation.py](configure_nz_dhb_automation.py) | Configure the 19 PHF Science annual DHB refresh jobs. |
| [dashboard.sh](dashboard.sh) | Manage local API, worker, scheduler and web processes. |
| [dashboard_web_supervisor.cjs](dashboard_web_supervisor.cjs) | Supervise the dashboard web process. |
| [install_systemd_services.sh](install_systemd_services.sh) | Install or remove systemd service templates. |
| [monitor_realtime_data.py](monitor_realtime_data.py) | Read-only publication health samples; exit 0/1/2 for ok/warn/error. |
| [recover_stale_tasks.py](recover_stale_tasks.py) | Inspect or explicitly recover task rows left RUNNING by a dead worker. |
| [restart_task_runtime.py](restart_task_runtime.py) | Fail-closed drain, restart, and owner-scoped recovery for task runtime units. |
| [run_abstract_backfill.py](run_abstract_backfill.py) | Continuously queue bounded historical abstract repairs and fresh review passes. |
| [run_ai_worker_service.sh](run_ai_worker_service.sh) | Run ai worker service. |
| [run_dashboard_api_service.sh](run_dashboard_api_service.sh) | Run dashboard api service. |
| [run_dashboard_scheduler_service.sh](run_dashboard_scheduler_service.sh) | Run dashboard scheduler service. |
| [run_dashboard_web_service.sh](run_dashboard_web_service.sh) | Run dashboard web service. |
| [run_dashboard_worker_service.sh](run_dashboard_worker_service.sh) | Run dashboard worker service. |
| [run_infra_service.sh](run_infra_service.sh) | Run infra service. |
| [run_literature_autopilot.py](run_literature_autopilot.py) | Evaluate or apply the Research Radar automatic publication policy. |
| [run_site_service.sh](run_site_service.sh) | Run site service. |
| [run_situation_notices.py](run_situation_notices.py) | Queue collection and extraction; --export-only publishes stored Situation data. |
| [send_systemd_failure_alert.py](send_systemd_failure_alert.py) | Send rate-limited systemd failure alerts using the application's SMTP config. |
| [serve_site.py](serve_site.py) | Serve the built Astro site with gzip and production cache headers. |
| [service_common.sh](service_common.sh) | Shared shell helpers used by service launchers. |
| [us_configure_hiv_automation.py](us_configure_hiv_automation.py) | Idempotently enable the production US NHSS HIV refresh job. |

## Publication and export

| Script | Purpose |
| --- | --- |
| [capture_papers_compatibility.py](capture_papers_compatibility.py) | Read-only production Papers baseline and compatibility comparison. |
| [capture_research_context.py](capture_research_context.py) | Capture published surveillance and Situation inputs for a research candidate. |
| [collect_publication_usage.py](collect_publication_usage.py) | Collect one read-only account usage snapshot; no deployment or mutation. |
| [export_dashboard_openapi.py](export_dashboard_openapi.py) | Export the canonical FastAPI contract for TypeScript client generation. |
| [export_literature_site_data.py](export_literature_site_data.py) | Queue the unified site release for reviewed Research Radar changes. |
| [export_research_snapshot.py](export_research_snapshot.py) | Build a private Research Radar candidate from a managed DB read snapshot. |
| [export_situation_data.py](export_situation_data.py) | Export independent Situation JSON using the caller's database read snapshot. |
| [generate_schema.py](generate_schema.py) | Generate and optionally apply SQL DDL from SQLAlchemy metadata. |
| [generate_site_data.py](generate_site_data.py) | Generate static JSON data files for the Astro-based report site. |
| [prepare_papers_projection.py](prepare_papers_projection.py) | Prepare an opt-in Papers v3 migration or single-body update, without publishing. |
| [prepare_papers_templates.py](prepare_papers_templates.py) | Build an isolated JSON/template candidate from a verified Papers bundle. |
| [prepare_static_research_release.py](prepare_static_research_release.py) | Prepare a Pages + direct-R2 research release without deploying it. |
| [publish_download_repo.py](publish_download_repo.py) | Incrementally publish partitioned downloads to the data repository. |
| [publish_papers_r2.py](publish_papers_r2.py) | Incrementally publish the papers site bundle to Cloudflare R2. |
| [publish_public_updates.py](publish_public_updates.py) | Reconcile public monthly update JSON from acknowledged publication tasks. |
| [publish_raw_git_archive.py](publish_raw_git_archive.py) | Mirror data/raw into a configured branch of a dedicated Git repository. |
| [publish_reports_r2.py](publish_reports_r2.py) | Incrementally publish the reports site bundle to its R2 bucket. |
| [publish_research_candidate.py](publish_research_candidate.py) | Publish a privately built research snapshot and its versioned page bundle. |
| [publish_reviewed_disease_mapping_release.py](publish_reviewed_disease_mapping_release.py) | Discover reviewed source categories and publish one atomic v3 mapping release. |
| [publish_surveillance_data.py](publish_surveillance_data.py) | Publish browser JSON without building or deploying the static website. |
| [queue_surveillance_data.py](queue_surveillance_data.py) | Queue a surveillance JSON update without rebuilding the static site. |
| [reuse_published_research.py](reuse_published_research.py) | Install research inputs matching the publication baseline before a style build. |
| [split_papers_bundle.py](split_papers_bundle.py) | Split Astro dist into Pages main + papers staging for R2. |
| [split_reports_bundle.py](split_reports_bundle.py) | Move report archive pages out of Astro dist into the reports R2 bundle. |

## Historical imports and population

| Script | Purpose |
| --- | --- |
| [fetch_au_location_aggregate.py](fetch_au_location_aggregate.py) | Fetch/prepare AU data by aggregating location-level rows into national totals. |
| [fetch_au_subdivision_history.py](fetch_au_subdivision_history.py) | Fetch all official NINDSS state/month/disease rows in annual query batches. |
| [import_au_national_history.py](import_au_national_history.py) | Import AU national monthly data into disease_records. |
| [import_br_history.py](import_br_history.py) | Historical backfill helper for Brazil SINAN / DATASUS. |
| [import_ch_history.py](import_ch_history.py) | Fetch and import Switzerland FOPH IDD historical case data. |
| [import_ecdc_baselines.py](import_ecdc_baselines.py) | Validate and atomically import prepared ECDC annual country baselines. |
| [import_hk_history.py](import_hk_history.py) | Fetch and import Hong Kong, China CHP monthly historical data. |
| [import_iceland_history.py](import_iceland_history.py) | Download, parse, audit, and import Iceland's historical Excel releases. |
| [import_jp_weekly_history.py](import_jp_weekly_history.py) | Import Japan weekly historical data into disease_records. |
| [import_kr_history.py](import_kr_history.py) | Fetch and import Korea KDCA monthly historical data. |
| [import_subdivision_population.py](import_subdivision_population.py) | Import official Chinese province and Australian state annual population. |
| [import_tw_history.py](import_tw_history.py) | Fetch and import Taiwan, China CDC NIDSS monthly history. |
| [import_us_strict_history.py](import_us_strict_history.py) | Import strict US historical weekly rows into disease_records without clearing recent US data. |
| [import_wpp_population.py](import_wpp_population.py) | Import WPP population CSV into population_records. |
| [cn_merge_diseases_data.py](imports/cn_merge_diseases_data.py) | Restart migration: merge history CSVs with special handling for 201301/201302. |
| [refresh_official_population.py](refresh_official_population.py) | Refresh official NBS, ABS and IBGE populations; default is fetch/validate only. |
| [update_au_subdivisions.py](update_au_subdivisions.py) | Restore official NINDSS state counts from archived national location responses. |
| [update_br_subdivisions.py](update_br_subdivisions.py) | Aggregate official SINAN microdata by residence state and notification month. |
| [update_cn_provinces.py](update_cn_provinces.py) | Import Chinese province history and refresh configured official sources. |
| [us_build_strict_import_ready.py](us_build_strict_import_ready.py) | Build a conservative US weekly history import set from review buckets. |
| [us_prepare_hiv_history.py](us_prepare_hiv_history.py) | Merge CDC NHSS annual HIV/AIDS history into the US history dataset. |
| [us_prepare_nndss_history.py](us_prepare_nndss_history.py) | Normalize US CDC NNDSS weekly data into the project's history_merged.csv format. |

## Quality and diagnostics

| Script | Purpose |
| --- | --- |
| [audit_disease_cutover.py](audit_disease_cutover.py) | Audit per-concept readiness for strict disease source-series reads. |
| [audit_disease_duplicates.py](audit_disease_duplicates.py) | Audit standard disease and mapping CSVs for likely duplicate concepts. |
| [audit_disease_mappings.py](audit_disease_mappings.py) | Run deterministic structural checks over country disease mappings. |
| [audit_existing_data_migration.py](audit_existing_data_migration.py) | Audit existing publication metadata without fetching or regenerating data. |
| [audit_iceland_integration.py](audit_iceland_integration.py) | Read-only final audit for the Iceland surveillance integration. |
| [audit_literature_publisher_coverage.py](audit_literature_publisher_coverage.py) | Audit stored Springer/Elsevier coverage without API calls or database writes. |
| [audit_publication_cloudflare.py](audit_publication_cloudflare.py) | Read Cloudflare publication metadata without deploying or reading R2 objects. |
| [benchmark_ai_literature.py](benchmark_ai_literature.py) | Measure real backlog workflows in a disposable PostgreSQL schema. |
| [check_collection_schema.py](check_collection_schema.py) | Read-only PostgreSQL schema contract for collection and model work tables. |
| [check_dashboard_openapi.py](check_dashboard_openapi.py) | Check the checked-in dashboard API contract without modifying the worktree. |
| [check_legacy_disease_access.py](check_legacy_disease_access.py) | Prevent new production dependencies on the legacy disease fact table. |
| [check_literature_publication_readiness.py](check_literature_publication_readiness.py) | Read-only readiness check for the Literature JSON publication channel. |
| [check_repository_boundaries.sh](check_repository_boundaries.sh) | Check ignored data boundaries and tracked artifact sizes. |
| [check_repository_size.py](check_repository_size.py) | Reject newly tracked backups, database dumps, and oversized Git blobs. |
| [check_research_radar_health.py](check_research_radar_health.py) | Emit a privacy-safe, read-only Research Radar health report as JSON. |
| [check_task_runtime.py](check_task_runtime.py) | Read-only readiness check for the API, scheduler, worker, and task queue. |
| [check_theme_publication_readiness.py](check_theme_publication_readiness.py) | Read-only readiness check for the v1 independent theme publication. |
| [cn_data_quality_check.py](cn_data_quality_check.py) | 全面数据库数据检查 |
| [inspect_standard_countries.py](inspect_standard_countries.py) | Print and validate countries defined in the standard country library. |
| [release_readiness.py](release_readiness.py) | Read-only release readiness report for a reproducible deployment. |
| [test_email_config.py](test_email_config.py) | Validate SMTP email configuration and send a test email. |
| [us_history_data_quality_check.py](us_history_data_quality_check.py) | Validate US strict historical import against the import-ready CSV. |
| [us_hiv_data_quality_check.py](us_hiv_data_quality_check.py) | Deterministic checks for US CDC NHSS annual HIV/AIDS history rows. |
| [validate_research_release.py](validate_research_release.py) | Validate the generated Research Radar release artifact. |
| [validate_situation_release.py](validate_situation_release.py) | Validate the alert-based Situation export and built pages before deployment. |
| [verify_data_line_acceptance.py](verify_data_line_acceptance.py) | Read-only migration acceptance for explicitly selected task UUIDs. |
| [verify_raw_git_archive.py](verify_raw_git_archive.py) | Verify or restore a GlobalID readable raw-data mirror. |
| [verify_reports_host.py](verify_reports_host.py) | Check an R2-backed reports host serves its control files and report pages. |
| [verify_subdivision_incidence.py](verify_subdivision_incidence.py) | Verify every exported subdivision rate against its official local/year denominator. |

## Maintenance and data repair

| Script | Purpose |
| --- | --- |
| [ai_review_research_weekly_briefs.py](ai_review_research_weekly_briefs.py) | Run the bounded weekly-brief AI quality review (disabled by default). |
| [apply_disease_surveillance_note_overrides.py](apply_disease_surveillance_note_overrides.py) | Apply reviewed source-data notes to existing disease knowledge briefs. |
| [backfill_br_ntra_history.py](backfill_br_ntra_history.py) | Safely rebuild Brazil SINAN NTRA monthly history. |
| [backfill_disease_series_from_legacy.py](backfill_disease_series_from_legacy.py) | Recover lossless source-series facts from legacy disease_records rows. |
| [backfill_disease_series_observations.py](backfill_disease_series_observations.py) | Backfill lossless disease source-series observations from a history CSV. |
| [backfill_literature_metadata.py](backfill_literature_metadata.py) | Audit or apply a resumable OpenAlex/Unpaywall backfill. |
| [backfill_public_disease_series.py](backfill_public_disease_series.py) | Queue an incremental site-release for stored, unpublished disease series. |
| [backfill_pubmed_abstracts.py](backfill_pubmed_abstracts.py) | Audit or apply PubMed EFetch abstract backfill for stored literature. |
| [backfill_us_nndss_resident_series.py](backfill_us_nndss_resident_series.py) | Backfill lossless US-resident NNDSS source-series observations safely. |
| [bootstrap_disease_mapping_registry_v3.py](bootstrap_disease_mapping_registry_v3.py) | Bootstrap Mapping Registry v3 from every current source and observation. |
| [build_situation_gazetteer.py](build_situation_gazetteer.py) | Build configs/situation_gazetteer.json from Natural Earth (public domain). |
| [canonicalize_duplicate_disease_ids.py](canonicalize_duplicate_disease_ids.py) | Canonicalize duplicate disease IDs in the database. |
| [cleanup_legacy_papers.py](cleanup_legacy_papers.py) | Plan or apply fenced deletion of replaced static paper pages. |
| [cleanup_suggestions.py](cleanup_suggestions.py) | 清理无效的疾病建议 |
| [cn_missing_data_get.py](cn_missing_data_get.py) | 获取中国疾病数据缺失月份的数据 |
| [compact_literature_payloads.py](compact_literature_payloads.py) | Compact legacy Research Radar source payloads in bounded transactions. |
| [control_plane_migrate.py](control_plane_migrate.py) | Preflight checks for adopting managed control-plane migrations. |
| [discover_literature_gaps.py](discover_literature_gaps.py) | Reconcile or execute Research Radar evidence-gap discovery. |
| [disease_cli.py](disease_cli.py) | GlobalID V2 - 疾病管理命令行工具 |
| [drain_peer_review_backlog.py](drain_peer_review_backlog.py) | Drain the AI peer-review backlog left by the old human/threshold gates. |
| [full_rebuild_database.py](full_rebuild_database.py) | Complete Database Rebuild - All-in-One Script |
| [generate_english_mappings.py](generate_english_mappings.py) | 生成英文疾病映射CSV文件 |
| [govern_literature_backlog.py](govern_literature_backlog.py) | Plan or explicitly apply a bounded Research Radar backlog policy. |
| [rebuild_source_series_components.py](rebuild_source_series_components.py) | Safely rebuild split source-series facts from source CSV extracts. |
| [reclassify_literature.py](reclassify_literature.py) | Backfill current Research Radar classification without provider requests. |
| [reconcile_source_current_mapping_categories.py](reconcile_source_current_mapping_categories.py) | Repair placeholder Mapping Registry categories using reviewed source identity. |
| [reconcile_stale_literature_ingest_runs.py](reconcile_stale_literature_ingest_runs.py) | Dry-run or reconcile legacy Research Radar runs left running. |
| [register_ecdc_baseline_countries.py](register_ecdc_baseline_countries.py) | Register every prepared reviewed ECDC annual country baseline. |
| [reopen_disease_literature.py](reopen_disease_literature.py) | Requeue historical exclusions with strong disease evidence, in bounded batches. |
| [repair_literature_abstracts.py](repair_literature_abstracts.py) | Repair short abstracts on eligible review articles; dry-run by default. |
| [replay_literature_journal_failures.py](replay_literature_journal_failures.py) | Inspect failed journal windows or enqueue one bounded, isolated replay page. |
| [rescreen_excluded_literature.py](rescreen_excluded_literature.py) | Rescreen a fixed snapshot of historical exclusions with bounded backpressure. |
| [restore_disease_migration.py](restore_disease_migration.py) | Preview or restore disease-record before-images from the migration ledger. |
| [review_research_weekly_brief.py](review_research_weekly_brief.py) | Record a human Research Radar weekly-brief review, bound to its content. |
| [seed_diseases.py](seed_diseases.py) | Seeds the database with standard diseases from the CSV file. |
| [sync_disease_ontology.py](sync_disease_ontology.py) | Synchronize the disease ontology and migrate known legacy concept facts. |
| [sync_iceland_registry.py](sync_iceland_registry.py) | Seed only Iceland's ontology catalogue and source-series registry rows. |
| [update_disease_knowledge.py](update_disease_knowledge.py) | Update the source-grounded disease knowledge base. |
| [us_apply_review_buckets.py](us_apply_review_buckets.py) | Split US review-report rows into handling buckets. |
| [us_reclean_and_audit_unmapped.py](us_reclean_and_audit_unmapped.py) | Re-clean US NNDSS weekly history output and audit unmapped labels. |

## Automation and CI

| Script | Purpose |
| --- | --- |
| [check_contract_drift.py](automation/check_contract_drift.py) | Fail when generated Situation contract paths differ from the checkout. |
| [dispatch_research_digest.py](automation/dispatch_research_digest.py) | Build or queue a replay-safe email campaign from the latest Research Radar brief. |
| [prepare_site_build_fixture.py](automation/prepare_site_build_fixture.py) | Create minimal ignored site-data inputs for clean-checkout CI builds. |
| [prepare_subscription_overlay.py](automation/prepare_subscription_overlay.py) | Build an auditable three-page overlay on a verified Pages snapshot. |
| [seed_ci_database_fixtures.py](automation/seed_ci_database_fixtures.py) | Seed minimal deterministic database rows for clean-checkout CI tests. |
| [smoke_migrations.py](automation/smoke_migrations.py) | Validate the Alembic graph and round-trip the latest migration in a disposable DB. |
| [verify_site_commit.py](automation/verify_site_commit.py) | Fail a release until the public HTML exposes the expected source commit. |

## Historical maintenance tools

| Script | Purpose |
| --- | --- |
| [backfill_task_country_ids.py](maintenance/backfill_task_country_ids.py) | Backfill tasks.country_id from input_data.country/country_code for legacy tasks. |
| [migrate_add_revision_count.py](maintenance/migrate_add_revision_count.py) | Migration script: Add revision_count column to report_section_runs table. |
| [migrate_chfl_geography.py](maintenance/migrate_chfl_geography.py) | Correct CHFL IDD observation identities without changing reported values. |
| [migrate_country_scopes.py](maintenance/migrate_country_scopes.py) | Migrate country/scope structure to canonical countries + country_scopes. |
| [migrate_knowledge_automation.py](maintenance/migrate_knowledge_automation.py) | Reclassify legacy knowledge review rows into the automated evidence workflow. |
| [rebuild_database_schema.sh](maintenance/rebuild_database_schema.sh) | Rebuild database schema. |
