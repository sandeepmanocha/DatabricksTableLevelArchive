# CareSource Archive — Feature List

Features are organized for parallel development. Each feature maps to one or two modules and can be built independently.

## Feature Dependency Order

```
F13 (exceptions)  ─┬── F1 (utils) ──┬── F3 (config) ── F4 (conditions) ── F6 (archiver) ── F8 (archive notebook + job)
                   │                │        │                                    │
                   │   F2 (audit) ──┤        │                                    └── F5 (dry-run)
                   │                │        │
                   │                └── F7 (rehydrator) ── F9 (rehydrate notebook + job)
                   │                         │
                   │                         └── F11 (scanner) ── F12 (scanner notebook)
                   │
                   └── F14 (package setup — pyproject.toml, can start immediately)
                        F15 (config Delta table setup — can start immediately, infrastructure only)
```

**Parallel lanes:**
- Lane A: F13 → F1 → F3 → F4 → F6 → F8
- Lane B: F2 (can start immediately, no deps beyond F13)
- Lane C: F7 → F9 (starts after F1 + F2 are done)
- Lane D: F5 (starts after F4 + F6 + F2 are done)
- Lane E: F11 → F12 (starts after F3 is done)
- Lane F: F10 (tests — written BEFORE each module's implementation, not after)
- Lane G: F14 (package setup — can start immediately, no code deps)
- Lane H: F15 (config Delta table setup — can start immediately, infrastructure only)

**Per-module build order:** For every module (F1, F2, F3, etc.), write the corresponding test file FIRST from requirements, then write the implementation to pass those tests. See F10 for details.

---

## Features

### F1: Shared Utilities (`src/utils.py`)
**Modules:** `src/utils.py`
**Dependencies:** F13 (exceptions)
**Parallel:** Start after F13

| Task | Description |
|------|------------|
| F1.1 | `RunContext` dataclass — fields: `settings` (dict), `secrets` (dict), `job_context` (dict), `archive_run_id` (str). Data container only, no methods |
| F1.2 | `get_job_context(dbutils)` → extract workspace_id, job_id, job_run_id, task_run_id from notebook context tags. Returns dict. Gracefully returns NULLs when running interactively |
| F1.3 | `generate_archive_run_id()` → UUID string for correlating audit entries across a single job run |
| F1.4 | `build_archive_path(base_path, table_name, year)` → `{base_path}/{table_name}/year_{year}` |
| F1.5 | `build_full_table_name(catalog, schema, table)` → `catalog.schema.table` |
| F1.6 | `create_schema_if_not_exists(spark, catalog, schema)` |
| F1.7 | `archive_folder_exists(dbutils, base_path, table_name, year)` → bool |
| F1.8 | `list_existing_archive_years(dbutils, base_path)` → list of ints |
| F1.9 | `get_distinct_years(spark, catalog, schema, table, date_column)` → list of ints |
| F1.10 | `configure_logging(settings, archive_run_id)` → set up Python logging with structured format `[{timestamp}] [{level}] [{archive_run_id}] [{table}] {message}`. Called once per notebook |
| F1.11 | `load_secrets(settings, dbutils)` → reads `settings["secret_scope"]` and retrieves known keys (`warehouse_id`, `client_id`, `client_secret`) via `dbutils.secrets.get()`. Returns dict of key→value. Missing keys return `None` (optional credentials). Raises `ArchiveConfigError` if `secret_scope` is missing from settings |

**Acceptance:** NFR-05, AUD-06, AUD-07, LOG-01 – LOG-03, JOB-08 → NFR-13

---

### F2: Audit Logging (`src/audit.py`)
**Modules:** `src/audit.py`
**Dependencies:** F13 (exceptions)
**Parallel:** Can start immediately after F13

| Task | Description |
|------|------------|
| F2.1 | `AuditLogger.__init__(self, ctx: RunContext, spark)` — extract audit_catalog/schema from `ctx.settings`, store spark and ctx for run_id and job_context |
| F2.2 | `ensure_archive_audit_table()` — create `archive_audit_log` Delta table if not exists (schema per design doc Section 7, including `null_date_count` column) |
| F2.3 | `ensure_rehydration_audit_table()` — create `rehydration_audit_log` Delta table if not exists |
| F2.4 | `.log_archive(table, year, status, record_count, conditions_applied, null_date_count=None, error_message=None)` — insert row with all context (timestamp, user, job context, run_id injected automatically). Status must be from enumerated set: `STARTED`, `DRY_RUN`, `ARCHIVED`, `ARCHIVED_AND_DELETED`, `FAILED`, `SKIPPED`, `SKIPPED_CONCURRENT`, `NO_DATA` |
| F2.5 | `.log_rehydrate(archive_path, source, target, years, tables_created, status, error_message=None)` |
| F2.6 | `.log_dry_run(table, year, total_eligible, would_archive, per_condition_counts, null_date_count=None)` — status = `DRY_RUN` |
| F2.7 | `.check_resume_state(table_config, year)` → query audit for latest status of a table+year. Returns status string or None |
| F2.8 | `.check_concurrent(table_config, year, archive_run_id, stale_threshold_hours=4)` → query audit for `STARTED` rows with a different `archive_run_id` for the same table+year. Returns `(is_concurrent, is_stale, foreign_run_id, age_hours)` tuple. If foreign STARTED age < threshold: fresh concurrent. If age >= threshold: stale (abandoned). Configurable via `stale_started_threshold_hours` in global settings |
| F2.9 | `.get_latest_status(table, year)` → query audit for the most recent entry of any status. Returns `(status, archive_run_id)` or `None`. Used for informational retry logging before archiving |

**Acceptance:** AUD-01 – AUD-08, CONC-02, ARC-10

---

### F3: Configuration Loading (`src/config.py`)
**Modules:** `src/config.py`
**Dependencies:** F1 (utils)
**Parallel:** Start after F1

| Task | Description |
|------|------------|
| F3.1 | `load_table_configs(spark, table_configs_table, active_only=True, *, source_catalog=None, source_schema=None, table_id=None, filter_expr=None)` → read `table_configs` Delta table, return validated list of table config dicts. Optionally filter to active rows. Optional typed filters (`source_catalog`, `source_schema`, `table_id`) and `filter_expr` (validated, parse-checked) for subset processing |
| F3.2 | `load_settings(spark, config_table)` → read the single-row `global_settings` Delta table, validate required fields, return settings dict. The table contains pointers (`schema_templates_table`, `table_configs_table`) to other config tables |
| F3.3 | `load_schema_templates(spark, schema_templates_table, schema_id=None)` → read `schema_templates` Delta table. Always filters `is_active = true`. When `schema_id` provided: return single matching template (raise if not found, inactive, or duplicate). When omitted: return all active templates, validate no duplicate `(source_catalog, source_schema)` pairs. Validates `min_table_size_gb` per row (required, numeric, >= 0). Uses `sql_quote()` for safe SQL construction |
| F3.4 | Validate required fields per table config: source_catalog, source_schema, source_table, date_column, archive_base_path |
| F3.5 | Validate operator values against allowed set in exclusion_conditions |
| F3.6 | Validate no duplicate `table_id` values in the table_configs table |
| F3.7 | Validate `custom_sql` conditions contain required `{source_alias}` placeholder |
| F3.8 | `get_active_tables(configs)` → filter by `is_active == true` |
| F3.9 | Two-tier merge at runtime via `merge_settings()` in `config.py`: global settings defaults → table-level overrides. All overridable fields are resolved in one place: (1) `retention_years` — table configs can omit it to inherit from `global_settings.default_retention_years`; (2) `timezone` — table configs can omit it to inherit from `global_settings.timezone`, defaults to `"UTC"` if neither level sets it. Consumers (e.g., `ArchiveEngine._set_timezone()`) read the already-resolved value from the merged dict. To add a new global-to-local override, add it to `merge_settings()` |
| F3.10 | *(Removed — `null_date_policy` no longer exists. NULL date handling is fixed behavior, not configurable)* |
| F3.11 | Validate `timezone` field in settings if present — must be a valid timezone string |

**Acceptance:** CFG-01 – CFG-10

---

### F4: Condition Builder (`src/conditions.py`)
**Modules:** `src/conditions.py`
**Dependencies:** F3 (config)
**Parallel:** Start after F3

| Task | Description |
|------|------------|
| F4.1 | `build_exclusion_clause(conditions, source_catalog, source_schema, source_alias)` → SQL WHERE fragment |
| F4.2 | `same_table` handler: translate column + operator + value → SQL |
| F4.3 | Operator implementations: `equals`, `not_equals`, `in`, `within_years`, `within_months`, `greater_than`, `is_not_null` |
| F4.4 | `custom_sql` handler: substitute `{source_catalog}`, `{source_schema}`, `{source_alias}` placeholders |
| F4.5 | Combine all conditions with AND (negated OR logic) |
| F4.6 | `get_condition_names(conditions)` → list of name strings for dry-run reporting |
| F4.7 | `build_individual_condition_sql(condition, ...)` → single condition SQL (for per-condition counting in dry-run) |

**Acceptance:** EXC-01 – EXC-05

---

### F5: Dry-Run Mode
**Modules:** `src/archiver.py` (dry-run path within `ArchiveEngine.run()`)
**Dependencies:** F4 (conditions), F6 (archiver), F2 (audit)
**Parallel:** Start after F4 + F6 + F2

| Task | Description |
|------|------------|
| F5.1 | Count total eligible records per year (before exclusions) |
| F5.2 | Count excluded records per condition name (dynamic; counts may overlap) |
| F5.3 | Calculate would-archive count using the single combined exclusion clause |
| F5.4 | Write dry-run results to audit via `AuditLogger.log_dry_run()` |
| F5.5 | Return structured dry-run report for notebook display |

**Acceptance:** DRY-01 – DRY-05, AUD-05. Note: F5 is the `dry_run=True` branch within `ArchiveEngine.run()`.

---

### F6: Archive Engine (`src/archiver.py`)
**Modules:** `src/archiver.py`
**Dependencies:** F1 (utils), F2 (audit), F3 (config), F4 (conditions)
**Parallel:** Start after F4

| Task | Description |
|------|------------|
| F6.1 | `ArchiveEngine.__init__(self, ctx: RunContext, audit: AuditLogger, spark)` — store ctx, audit, and spark |
| F6.2 | `.run(table_config, dry_run=True)` — orchestrate: calculate years, check resume state, build conditions, archive or dry-run, verify, delete, write metadata, log audit |
| F6.3 | `._calculate_eligible_years(table_config)` → years to archive (retention window minus already archived) |
| F6.4 | `._archive_year(table_config, year, conditions_clause)` → write to year folder (initial write) or append to existing year folder (incremental — see F6.15) |
| F6.5 | Verify archive count matches expected before proceeding |
| F6.6 | `._delete_archived(table_config, year)` → conditional delete from source |
| F6.7 | Handle `year_override` parameter to bypass retention calculation |
| F6.8 | `._write_metadata(table_config, year, record_count, conditions, mode)` → write `_archive_metadata.json` alongside year folder. `mode` is `"write"` for initial archive, `"append"` for incremental append (see F6.15) |
| F6.9 | Resume check: call `audit.check_resume_state()` before skipping. If `ARCHIVED` + `delete_after_archive=true`, run delete step |
| F6.10 | `.validate_archives(table_configs)` → health check: verify expected year folders exist for all active tables |
| F6.11 | Concurrency guard: write `STARTED` to audit before archiving, call `audit.check_concurrent()` to detect another run, skip with `SKIPPED_CONCURRENT` if detected. Verify ownership (same `archive_run_id`) before delete |
| F6.12 | NULL date handling: always add `AND {date_column} IS NOT NULL` to WHERE clause. Count NULL-date records and log at ERROR level (required value missing). Record `null_date_count` in audit. No configuration — NULL dates are always an error |
| F6.13 | Wrap `custom_sql` condition execution in try/except — catch exceptions, include SQL text in `ArchiveOperationError` message |
| F6.14 | Apply `timezone` setting from merged settings to the Spark session before date comparisons (`spark.conf.set("spark.sql.session.timeZone", tz)`). Default to UTC if field is absent |
| F6.15 | **Incremental append mode (per-year watermark)** `[PENDING CUSTOMER DISCUSSION]` — the watermark is tracked **per table per year**, not globally per table. Each year's archive stores `MAX(watermark_column)` independently in the audit log. When a year folder exists, compare the year's last watermark against source records and APPEND only rows above it. Source only has records that were excluded in previous runs; no deduplication needed. Audit logs with `record_count` reflecting only the appended records. Metadata written with `"mode": "append"`. **Late-arrival gap:** records arriving with a watermark value below the year's archived MAX will not be picked up — they stay in source, unarchived. Acceptable for always-increasing columns (`etl_load_date`); problematic for business dates with late arrivals (see A13 in requirements-summary.md) |
| F6.16 | **Skip-or-override for existing years** `[PENDING CUSTOMER DISCUSSION]` — when year folder exists and `delete_after_archive=false`, skip the year with status `SKIPPED`. Operator uses `year_override` (F6.7) to force a full re-archive when needed. This avoids the deduplication problem where source still contains already-archived records |
| F6.17 | **Catch-all FAILED logging** — after logging STARTED, wrap year processing in try/except. On any unhandled exception, log FAILED with error message in audit, then re-raise. Preserves ArchiveOperationError and ArchiveVerificationError types; wraps other exceptions in ArchiveOperationError. Sequential year processing stops on first failure (fail-fast) |
| F6.18 | **Orphan folder detection** — if archive folder exists but no successful archive (ARCHIVED/ARCHIVED_AND_DELETED) is recorded in audit, raise ArchiveOperationError with reason="orphan_folder" and actionable resolution steps |
| F6.19 | **Explicit retry logging** — before logging STARTED, call `audit.get_latest_status()` to check previous run state. If previous was FAILED, log INFO retry message. If previous was STARTED (stale), log WARNING about incomplete prior run |
| F6.20 | **Stale STARTED threshold** — `check_concurrent` now differentiates stale abandoned runs from active concurrent runs using configurable `stale_started_threshold_hours` (default 4). Stale foreign STARTED entries are logged as warnings and processing proceeds; fresh concurrent entries trigger SKIPPED_CONCURRENT |

**Acceptance:** ARC-01 – ARC-13, CONC-01 – CONC-04, EDGE-01 – EDGE-03, ERR-03, ROLL-03

---

### F7: Rehydration Engine (`src/rehydrator.py`)
**Modules:** `src/rehydrator.py`
**Dependencies:** F1 (utils), F2 (audit)
**Parallel:** Start after F1 + F2

| Task | Description |
|------|------------|
| F7.1 | `RehydrationEngine.__init__(self, ctx: RunContext, audit: AuditLogger, spark)` — store ctx, audit, and spark |
| F7.2 | `.run(params)` → orchestrate full rehydration from runtime params |
| F7.3 | Create external table using `USING DELTA LOCATION` only — no `SHALLOW CLONE` fallback. LOCATION failure raises `ArchiveOperationError` with table, year, operation, and root cause |
| F7.4 | Create unified view: `UNION ALL` of source table + external year tables (optional, controlled by `create_unified_view` param). View creation failure raises typed `ArchiveOperationError` with the failing SQL |
| F7.5 | Verify archive folder exists before creating external table — notebooks resolve `available_archive_years` via `dbutils` and pass the list to the engine. `RehydrationEngine` never uses `dbutils` directly |
| F7.6 | Runtime `table_prefix` parameter — prefixes external table and view names for namespace isolation |
| F7.7 | Explicit validation of all required `run()` params at entry — missing params raise `ArchiveOperationError(reason="invalid_params")` with actionable message listing missing keys |
| F7.8 | Audit write failure resilience — if `log_rehydrate` fails, emit structured JSON fallback payload to `LOGGER.error` preserving both primary and audit errors, then raise `ArchiveOperationError` |
| F7.9 | Status contract: `COMPLETED` (all years restored), `PARTIAL_COMPLETED` (some years skipped), `FAILED` (no years restored or hard error). Enforced via `ALLOWED_REHYDRATION_STATUSES` frozenset in `audit.py` |

**Acceptance:** RHY-01 – RHY-06, ROLL-01, AUD-08

---

### F8: Archive Notebook & Job (`notebooks/`, `resources/`)
**Modules:** `notebooks/generate_parameters.py`, `notebooks/run_archive.py`, `resources/archive_job.yml`
**Dependencies:** F6 (archiver), F3 (config)
**Parallel:** Start after F6

| Task | Description |
|------|------------|
| F8.1 | `generate_parameters.py` — take required `config_table` job parameter, read `global_settings` Delta table via `load_settings()`, then read `table_configs` via the pointer, filter active, generate `archive_run_id` (UUID), emit ForEach task values including the run_id. Raises `ArchiveConfigError` if `config_table` is not provided |
| F8.2 | `run_archive.py` — parse widget params, call `load_settings(spark, config_table)`, call `load_secrets()` with settings + dbutils, build RunContext (settings, secrets, job_context, run_id), create AuditLogger + ArchiveEngine, call `.run()`, display HTML summary |
| F8.3 | `archive_job.yml` — DABs job: generate_parameters → ForEach → run_archive. Targets: dev, qa, stage, prod |
| F8.4 | Job parameters: `config_table` (required, full 3-level name of global_settings Delta table), `dry_run` (default true), `year_override` (optional), `table_config_filter` (optional filter expression for subset processing) |
| F8.5 | Wire `concurrency` from `global_settings` table into ForEach task concurrency. DABs targets map to environments (dev, qa, stage, prod) |
| F8.6 | `validate_config.py` — read and validate all config Delta tables (global_settings, schema_templates, table_configs), report errors |
| F8.7 | `validate_archives.py` — call `ArchiveEngine.validate_archives()`, display HTML report |
| F8.8 | **Service principal `run_as`** — QA, Stage, and Prod targets configure `run_as` with a service principal name. Dev target omits `run_as` (runs as deploying user). SP permissions documented in design doc Section 10 |
| F8.9 | **M2M OAuth in CI/CD** — see JOB-07 → NFR-12. Documented in design doc Section 10-11 |
| F8.10 | **Secret scope per environment** — see JOB-08 → NFR-13. Notebooks call `load_secrets()` to retrieve warehouse IDs and runtime credentials from the scope |

**Acceptance:** JOB-01 – JOB-09, LOG-04

---

### F9: Rehydration Notebook & Job (`notebooks/`, `resources/`)
**Modules:** `notebooks/run_rehydrate.py`, `resources/archive_job.yml`
**Dependencies:** F7 (rehydrator)
**Parallel:** Start after F7

| Task | Description |
|------|------------|
| F9.1 | `run_rehydrate.py` — widgets for all runtime params, build RunContext, create AuditLogger + RehydrationEngine, call `.run()`, display HTML summary |
| F9.2 | Add rehydrate job to `archive_job.yml` — on-demand, all params from job parameters |

**Acceptance:** JOB-03, RHY-01, LOG-04

---

### F10: Tests (Write BEFORE Implementation)
**Modules:** `tests/`
**Dependencies:** Each test file should be written BEFORE its corresponding module's implementation code
**Parallel:** Test files for independent modules can be written in parallel

**Critical rule:** Tests are derived from the **requirements document** (requirements.md Section 2), not from the implementation. For each module, the workflow is:

1. Read the relevant requirements (CFG-*, ARC-*, EXC-*, etc.)
2. Write test cases that verify those requirements
3. Run tests — they should all fail (no implementation yet)
4. Write the implementation to make the tests pass

This prevents bias: if tests are written after seeing the code, they tend to verify what the code *does* rather than what it *should* do.

| Task | Description |
|------|------------|
| F10.1 | `tests/unit/test_config.py` — derive from CFG-01 through CFG-10: valid config reads from Delta tables (mocked spark), two-tier merge (global defaults + table overrides), invalid config rejected with `ArchiveConfigError`, duplicate table_id caught, `min_table_size_gb` required in schema templates (missing raises error, `0` valid), **schema_id filtering** (single active template returned, inactive raises, not found raises, duplicate defensive check, scan-all filters `is_active = true`, duplicate `(source_catalog, source_schema)` raises) |
| F10.2 | `tests/unit/test_scanner.py` — derive from SCN-01 through SCN-16, CFG-10: column pattern matching (exact + regex), **ambiguous match detection** (single pattern matching multiple columns → `is_active: false` with reason listing pattern and matched columns), exclude tables, preserve overrides (via `modified_by` check), unmatched flagging, size threshold (above/below/unknown/zero-disables/missing-errors), staging table writes, MERGE logic (new/updated/preserved/dropped tables), force flag overwrite, archive path validation against external locations (path covered → pass, path not covered → `ArchiveConfigError`), **scanner log writes** (per-table rows with match_status, ambiguity_detail, all_matched_columns, merge_action), **schema_id targeting** (single template scan, schema_id not found, schema_id inactive, zero active templates → early return) |
| F10.3 | `tests/unit/test_conditions.py` — derive from EXC-01 through EXC-05: SQL generation for each operator, custom_sql placeholder substitution, empty conditions |
| F10.4 | `tests/unit/test_exceptions.py` — derive from ERR-01 through ERR-04: exception hierarchy, exception messages include table/year/operation/root cause |
| F10.5 | `tests/integration/test_archiver.py` — derive from ARC-01 through ARC-11, DRY-01 through DRY-05, CONC-01 through CONC-04, EDGE-01 through EDGE-03: archive, verify, delete, resume, dry-run, concurrency detection, NULL date error handling, schema evolution (EDGE-02), timezone (EDGE-03) |
| F10.6 | `tests/integration/test_rehydrator.py` — derive from RHY-01 through RHY-06, ROLL-01: create external table, verify zero-copy, unified view, rehydrate-as-rollback |
| F10.7 | `tests/integration/test_audit.py` — derive from AUD-01 through AUD-08: audit table creation, all status values (including STARTED, SKIPPED_CONCURRENT), archive_run_id correlation, null_date_count, job context columns |
| F10.8 | `tests/unit/test_utils.py` — derive from F1.1 through F1.10, LOG-01 through LOG-03: RunContext fields, path formatting, configure_logging() sets correct format/level/handler hierarchy |
| F10.9 | `tests/interactive/` scripts — manual ad-hoc validation scripts for each module, run against real data on Databricks |
| F10.10 | **Not automated**: ROLL-02 through ROLL-04 (Delta time travel, archive immutability, onboarding practice) and JOB-01 through JOB-04 (DABs orchestration) are validated via runbook procedures and `databricks bundle validate`, not automated tests |

**Acceptance:** All requirements in Section 2 of requirements.md. Unit tests run without Spark; integration tests require a Databricks cluster. Every test traces back to a requirement ID.

---

### F11: Schema Scanner (`src/scanner.py`)
**Modules:** `src/scanner.py`
**Dependencies:** F3 (config)
**Parallel:** Start after F3

| Task | Description |
|------|------------|
| F11.1 | `scan_schema(spark, template)` → query Unity Catalog for all tables in catalog.schema |
| F11.2 | `match_date_column(table_columns, patterns)` → try each pattern in order (exact then regex). Returns `(matched_column, matched_pattern, all_matched_columns, match_status)`. If a single pattern matches exactly one column → `match_status = "matched"`. If a single pattern matches multiple columns → `match_status = "ambiguous"`, `matched_column = None`. If no pattern matches → `match_status = "unmatched"`. First pattern to produce any match wins — later patterns are not evaluated |
| F11.3 | `_list_table_columns(spark, catalog, schema, table)` → queries `{catalog}.information_schema.columns` filtered by `table_catalog`, `table_schema`, `table_name`, ordered by `ordinal_position`. Returns list of column name strings. No `DESCRIBE TABLE` parsing |
| F11.4 | `generate_table_config(template, table_name, matched_column)` → build table config dict from template defaults |
| F11.5 | `flag_unmatched(template, table_name)` → table config with `is_active: false` and reason `"no matching date/watermark column"`. `flag_ambiguous(template, table_name, pattern, matched_columns)` → table config with `is_active: false` and reason `"ambiguous date column: pattern '{pattern}' matched [{col1}, {col2}, ...]"` |
| F11.6 | `get_table_size_gb(spark, catalog, schema, table)` → run `DESCRIBE DETAIL`, return size in GB (float) or `None` if unavailable |
| F11.7 | `check_size_threshold(table_size_gb, min_table_size_gb)` → returns `(is_active, reason)` tuple. Below threshold: `"table size 0.8 GB is below minimum threshold of 2.0 GB"`. Unknown: `"table size unknown — DESCRIBE DETAIL returned no size information"` |
| F11.8 | `write_staging(spark, configs, staging_table, scan_run_id)` → INSERT discovered table configs into the `table_configs_staging` Delta table with `scan_run_id` and `scan_timestamp` |
| F11.9 | *(Removed — diff logic now handled by MERGE SQL in F11.10)* |
| F11.10 | `merge_staging_to_final(spark, staging_table, table_configs_table, scan_run_id, force=False)` → apply MERGE logic (SCN-11): INSERT new tables, UPDATE scanner-managed rows (`modified_by = 'scanner'`), preserve manually edited rows, mark dropped scanner-managed tables inactive. If `force=True`, overwrite the table_configs from staging with no diff |
| F11.11 | `run_scanner(spark, settings, force=False, schema_id=None)` → read `schema_templates` Delta table (via pointer in settings). When `schema_id` provided, scan only that active template; when omitted, scan all active templates. Generate `scan_run_id` (UUID), orchestrate full two-stage scan for each schema row, write to staging, MERGE to table_configs, write per-table rows to `scanner_log` Delta table via `write_scanner_log()`. If zero active templates, return early with all-zero summary (no staging/merge). Return summary report with counts for matched, **ambiguous**, unmatched, excluded, preserved, below_size_threshold, size_unknown |
| F11.12 | `validate_archive_path(spark, archive_base_path)` → query `SHOW EXTERNAL LOCATIONS` and verify at least one location's URL is a prefix of `archive_base_path`. Raises `ArchiveConfigError` if no external location covers the path. Called by `run_scanner()` before scanning tables for each schema — fail-fast before any table discovery |
| F11.13 | `ensure_scanner_log_table(spark, settings)` → create `scanner_log` Delta table if not exists. Location from `audit_catalog` / `audit_schema` in settings. Schema per design doc Section 9 |
| F11.14 | `write_scanner_log(spark, settings, scan_run_id, table_results)` → write per-table detail rows to `scanner_log` Delta table. Called once per schema after intermediate file is written. Each row captures: pattern matching results (including all matched columns for ambiguity visibility), size check results, active/inactive status with reason, and merge action (populated after merge step). `scan_run_id` is a UUID generated once per `run_scanner()` invocation for correlating all rows from a single scan |

**Acceptance:** SCN-01 – SCN-16, CFG-10

---

### F12: Scanner Notebook (`notebooks/run_scanner.py`)
**Modules:** `notebooks/run_scanner.py`
**Dependencies:** F11 (scanner)
**Parallel:** Start after F11

| Task | Description |
|------|------------|
| F12.1 | `run_scanner.py` — widgets for `config_table` (global_settings table name), `schema_id` (optional — target a single template), and `force` flag. Reads schema templates from the Delta table pointed to by global_settings. Call `scanner.run_scanner()`, display summary |
| F12.2 | Display scan report: tables matched, **ambiguous**, unmatched, excluded, overrides preserved, below size threshold, size unknown |
| F12.3 | Display scanner log query hint — show the `scan_run_id` and a sample SQL query for the operator to explore per-table details in the `scanner_log` Delta table |

**Acceptance:** SCN-07, SCN-13, SCN-15, SCN-16, LOG-04

---

### F13: Exception Hierarchy (`src/exceptions.py`)
**Modules:** `src/exceptions.py`
**Dependencies:** None
**Parallel:** Can start immediately

| Task | Description |
|------|------------|
| F13.1 | `ArchiveError` — base exception for all archive errors |
| F13.2 | `ArchiveConfigError(ArchiveError)` — raised for config validation failures. Message includes field name, table_id, source file |
| F13.3 | `ArchiveOperationError(ArchiveError)` — raised for runtime failures. Message includes table_id, year, operation, root cause |
| F13.4 | `ArchiveVerificationError(ArchiveError)` — raised for count mismatches and concurrent run detection. Message includes expected vs actual counts |

**Acceptance:** ERR-01 – ERR-04

---

### F14: Package Setup (`pyproject.toml`)
**Modules:** `pyproject.toml`
**Dependencies:** None
**Parallel:** Can start immediately

| Task | Description |
|------|------------|
| F14.1 | `pyproject.toml` with `[project]` metadata: name=`caresource-archive`, version, requires-python>=3.10 |
| F14.2 | `[build-system]` using `setuptools` |
| F14.3 | `[tool.setuptools.packages.find]` configured to find `src` package |
| F14.4 | `[tool.pytest.ini_options]` with `testpaths = ["tests/unit"]` |
| F14.5 | No external dependencies — Spark and Databricks are provided at runtime |

**Acceptance:** NFR-08. `pip install -e .` works locally; `pytest` discovers unit tests; DABs deploys `src/` via workspace sync.

---

### F15: Config Delta Table Setup (`notebooks/setup_config_tables.py`, `resources/setup_job.yml`)
**Modules:** `notebooks/setup_config_tables.py`, `resources/setup_job.yml`
**Dependencies:** F13 (exceptions — uses `ArchiveConfigError` for validation failures)
**Parallel:** Can start after F13

| Task | Description |
|------|------------|
| F15.1 | `setup_config_tables.py` — notebook that creates all 4 config Delta tables using `CREATE TABLE IF NOT EXISTS`. Parameters: `config_catalog` (required), `config_schema` (required), `seed_data` (default false). Verifies catalog exists and creates schema if it doesn't exist |
| F15.2 | DDL execution — read CREATE TABLE statements from `notebooks/setup_config_tables.py` logic (hardcoded in notebook, not file-read). Substitute `{config_catalog}` and `{config_schema}` from parameters. Tables: `global_settings`, `schema_templates`, `table_configs`, `table_configs_staging` |
| F15.3 | Conditional seed — when `seed_data=true` AND the table is empty, INSERT sample data adapted for this environment. Seed values for `audit_catalog`, `archive_base_path_prefix`, `secret_scope` come from additional notebook parameters or sensible defaults |
| F15.4 | Post-creation validation — read each table, confirm schema matches expected columns/types. Display HTML summary of table status (created, already existed, row counts) |
| F15.5 | `setup_job.yml` — DABs job definition with `setup_config_tables.py` as the single task. Targets per environment (dev, qa, stage, prod). `run_as` service principal for higher environments. Parameters passed via job parameters |
| F15.6 | **Idempotent** — safe to re-run. `CREATE TABLE IF NOT EXISTS` skips existing tables. Seed only inserts into empty tables. No data loss on re-run |
| F15.7 | **Secret scope per environment** — `secret_scope` field in `global_settings` (e.g., `archive-dev`, `archive-prod`) identifies the Databricks secret scope. Scope creation and secret population are out of scope (CareSource platform team). Code validates that `secret_scope` is present in settings |
| F15.8 | Grant `SELECT` on config tables to the service principal for each environment. Grant `MODIFY` on `table_configs` and `table_configs_staging` for the scanner SP. Grants are documented but not automated (CareSource platform team handles UC permissions) |

**Acceptance:** NFR-09, JOB-09, JOB-10
