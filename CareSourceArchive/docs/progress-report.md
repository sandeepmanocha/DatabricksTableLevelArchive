# CareSource Archive — Progress Report

Running log of completed work per module.

---

### F14: pyproject.toml + scaffolding
- **Status:** Complete
- **Tests:** N/A (infrastructure)
- **Notes:** pyproject.toml (setuptools backend), .python-version (3.10), .gitignore, conftest.py with mock_spark/mock_run_context/mock_audit fixtures, test directory structure (unit/integration/interactive)
- **Unblocks:** All modules

### F13: src/exceptions.py
- **Status:** Complete
- **Tests:** tests/unit/test_exceptions.py — 14/14 passing
- **Notes:** ArchiveError base, ArchiveConfigError (field/table_id/source kwargs), ArchiveOperationError (table/year/operation/reason), ArchiveVerificationError (table/year/expected/actual/reason). All support plain string or structured kwargs.
- **Unblocks:** F1, F2, F15

### F1: src/utils.py
- **Status:** Complete
- **Tests:** tests/unit/test_utils.py — 20/20 passing
- **Notes:** RunContext dataclass, get_job_context (graceful NULLs), generate_archive_run_id (UUID), build_archive_path, build_full_table_name, create_schema_if_not_exists, archive_folder_exists, list_existing_archive_years, get_distinct_years, configure_logging (archive_run_id filter), load_secrets (Rule 15/16 — scope-based, never logged)
- **Unblocks:** F3, F7

### F2: src/audit.py
- **Status:** Complete
- **Tests:** tests/unit/test_audit.py — 23/23 passing
- **Notes:** AuditLogger class. ensure_archive_audit_table/ensure_rehydration_audit_table DDL. log_archive (all 8 statuses validated), log_rehydrate, log_dry_run (JSON conditions_applied). check_resume_state, check_concurrent. current_user() for archived_by/rehydrated_by (JOB-06). Rule 16 verified — no secrets in SQL.
- **Unblocks:** F7, F5, F6

### F3: src/config.py
- **Status:** Complete
- **Tests:** tests/unit/test_config.py — 19/19 passing
- **Notes:** load_settings (required field validation, timezone validation via ZoneInfo), load_table_configs (active_only filter, filter_expr, duplicate table_id check, exclusion_conditions JSON parsing, operator validation, custom_sql placeholder check), load_schema_templates (min_table_size_gb validation, `schema_id` targeting with `is_active` filtering, duplicate `(source_catalog, source_schema)` check), get_active_tables, merge_settings (two-tier: global defaults + table overrides for retention_years)
- **Unblocks:** F4, F11

### F7: src/rehydrator.py
- **Status:** Complete
- **Tests:** tests/unit/test_rehydrator.py — 7/7 passing
- **Notes:** RehydrationEngine class. run(params) with dbutils in params. LOCATION preferred, SHALLOW CLONE fallback (RHY-03). Unified view via UNION ALL. Skips missing archive folders with warning. Audit logging (COMPLETED/FAILED). No shared state between runs (RHY-02).
- **Unblocks:** F9

### F4: src/conditions.py
- **Status:** Complete
- **Tests:** tests/unit/test_conditions.py — 17/17 passing
- **Notes:** build_exclusion_clause (NOT/AND pattern), all 7 operators (equals, not_equals, in, within_years, within_months, greater_than, is_not_null), custom_sql placeholder substitution ({source_alias}, {source_catalog}, {source_schema}), get_condition_names, build_individual_condition_sql. Unknown type/operator raises ArchiveConfigError.
- **Unblocks:** F6+F5 (archiver)

### F11: src/scanner.py
- **Status:** Complete
- **Tests:** tests/unit/test_scanner.py — 23/23 passing
- **Notes:** scan_schema (SHOW TABLES), match_date_column (exact/regex/ambiguous/unmatched), generate_table_config, flag_unmatched/flag_ambiguous, get_table_size_gb (DESCRIBE DETAIL), check_size_threshold (4 cases), validate_archive_path (external locations), ensure_scanner_log_table (DDL), write_staging (INSERT), merge_staging_to_final (MERGE with force flag), write_scanner_log, run_scanner (orchestration with summary counts, `schema_id` targeting, zero-template early return). Dead `dbutils` parameter removed.
- **Unblocks:** F12 (scanner notebook)

### F6+F5: src/archiver.py
- **Status:** Complete
- **Tests:** tests/unit/test_archiver.py — 22/22 passing
- **Notes:** ArchiveEngine class. run() orchestrates per-table. _calculate_eligible_years (retention window). _archive_year with write/append/skip modes (ARC-12/13). _verify_archive count match. _delete_archived with ownership check (CONC-03). _write_metadata with mode field. Concurrency guard (STARTED/SKIPPED_CONCURRENT). NULL date handling (IS NOT NULL + count at ERROR). Dry-run with per-condition counts and structured report. Timezone applied to Spark session. custom_sql error wrapping (ERR-03).
- **Unblocks:** F8 (archive notebooks)

### F15: notebooks/setup_config_tables.py + resources/setup_job.yml
- **Status:** Complete
- **Notes:** Databricks notebook with config_catalog/config_schema/seed_data widgets. CREATE TABLE IF NOT EXISTS for 4 Delta tables. Conditional seed into empty tables. HTML validation summary. DABs job with per-env targets. databricks.yml root bundle created.
- **Unblocks:** Environment setup

### F8: Archive Notebooks + Job
- **Status:** Complete
- **Notes:** generate_parameters.py (ForEach values), run_archive.py (ArchiveEngine wrapper), validate_config.py, validate_archives.py. resources/archive_job.yml with ForEach concurrency. Job params: config_table, dry_run, year_override, table_config_filter.
- **Unblocks:** End-to-end archive job

### F9: notebooks/run_rehydrate.py
- **Status:** Complete
- **Notes:** Widgets for all rehydration params. Builds RunContext, calls RehydrationEngine.run(). HTML summary with tables_created and view_name.

### F12: notebooks/run_scanner.py
- **Status:** Complete
- **Notes:** Widgets for config_table, schema_id (optional targeting), and force flag. Calls run_scanner(). HTML summary with all count categories (F12.2). Displays scan_run_id and scanner_log query hint (F12.3).

### scan_run_id on table_configs
- **Status:** Complete
- **Spec:** docs/superpowers/specs/2026-04-03-scan-run-id-table-configs-design.md
- **Tests:** tests/unit/test_scanner.py — 37/37 passing (2 updated, 2 new)
- **Files changed:** notebooks/setup_config_tables.py (DDL), src/scanner.py (MERGE + deactivation SQL), tests/unit/test_scanner.py
- **Notes:** Added `scan_run_id STRING` (nullable) to `table_configs` DDL. Updated `merge_staging_to_final` to propagate `scan_run_id` from staging on INSERT, UPDATE SET, and deactivation UPDATE. Harsh-reviewed all three file changes. Full suite 199/199 passing.

### Step 0 doc updates
- **Status:** Complete
- **Notes:** design.md (CI/CD section: uv sync/uv run pytest/uv build; project structure: .python-version, uv.lock), development-rules.md (uv run pytest), prerequisites.md (uv >= 0.6, uv sync)
