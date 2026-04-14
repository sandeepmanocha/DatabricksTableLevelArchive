# CareSource Delta Table Archive — Requirements

## 1. Overview

Production-grade data lifecycle management for Databricks Delta tables. Automatically archives aged records to cost-effective cloud storage while respecting configurable business rules that prevent archiving records that are still operationally relevant. Provides on-demand rehydration of archived data to any catalog/schema.

---

## 2. Functional Requirements

### 2.1 Configuration Management

| ID | Requirement |
|----|------------|
| CFG-01 | **Global settings** stored in a single-row `global_settings` Delta table — audit location, defaults, concurrency, dry-run default, secret scope, archive path prefix, and pointers to other config tables. Job receives this table's full 3-level name as a parameter (bootstrap entry point) |
| CFG-02 | **Schema templates** stored in a `schema_templates` Delta table, one row per schema. Each row has a human-readable `schema_id` (PK) and a unique `(source_catalog, source_schema)` pair (enforced in code). Defines date column regex patterns (`ARRAY<STRING>`), retention years, archive base path, delete behavior, table exclusions (`ARRAY<STRING>`), and `min_table_size_gb` (required, see CFG-10). Scanner reads from this table via the pointer in global_settings. Scanner can target a single template by `schema_id` or scan all active templates when `schema_id` is omitted |
| CFG-03 | **Table configs** stored in a `table_configs` Delta table — one row per table. The archive job reads this table as primary input (via pointer in global_settings). Supports optional filter expressions for subset processing (e.g., `source_schema = 'claims'`). Exclusion conditions stored as `ARRAY<STRUCT<name, scope, column, operator, value, sql>>` |
| CFG-04 | Validate all configs on load — reject missing required fields, invalid operators, duplicate table IDs across all files |
| CFG-05 | `is_active` flag per table to enable/disable archiving without removing config |
| CFG-06 | Schema template `date_column_patterns` supports exact names and regex (e.g., `["claim_date", ".*_date$", ".*_timestamp$"]`). **Resolution order:** patterns are evaluated in array order; for each pattern, table columns are checked in the order returned by Unity Catalog. **First match wins** across patterns — once a pattern produces a match, later patterns are not evaluated. If a single pattern matches more than one column in the same table, the match is **ambiguous**: the table is flagged `is_active: false` with a reason listing the pattern and all matched columns, forcing the operator to add a more specific pattern or manually set `date_column` in the table config |
| CFG-07 | Schema template `exclude_tables` list to skip specific tables during scanning |
| CFG-08 | **Config validation notebook** — standalone `notebooks/manual/validate_config.py` that reads and validates all config Delta tables (global_settings, schema_templates, table_configs), reports errors. Can run manually before deployment |
| CFG-09 | **Default inheritance at runtime** — config loading applies two-tier merge: global settings defaults → table-level overrides. Table configs can omit `retention_years` to inherit from `global_settings.default_retention_years` |
| CFG-10 | **`min_table_size_gb`** required field in schema templates — minimum Delta table size in GB for the scanner to mark a table as active. Must be numeric and >= 0. Value of `0` disables size filtering. Missing, non-numeric, or negative values raise `ArchiveConfigError` |

### 2.2 Schema Scanner (Onboarding Tool)

| ID | Requirement |
|----|------------|
| SCN-01 | Scan Unity Catalog to discover all tables in a catalog.schema matching a schema template. Accepts optional `schema_id` parameter — when provided, scan only that template (must be active); when omitted, scan all active templates |
| SCN-02 | Match table columns against `date_column_patterns` (ordered, first match wins) to resolve the date column per table. If a single pattern matches multiple columns in the same table, flag as **ambiguous**: set `is_active: false` with reason `"ambiguous date column: pattern '{pattern}' matched [{col1}, {col2}, ...]"`. The operator must add a more specific exact-name pattern earlier in the list, or manually set `date_column` in the table config (which the merge logic preserves as a manual edit) |
| SCN-03 | Auto-generate table config entries from scan results. Output goes to the `table_configs_staging` Delta table first (see SCN-08), then MERGEd into the main `table_configs` Delta table |
| SCN-04 | Flag unmatched tables (no date column found) in the generated config with `is_active: false` and reason |
| SCN-05 | Skip tables listed in `exclude_tables` |
| SCN-06 | Preserve manual overrides — during MERGE, if a table config row in `table_configs` has `modified_by != 'scanner'` (manually edited), preserve the row. See SCN-11 for detailed merge cases |
| SCN-07 | Report scan summary: tables matched, **tables ambiguous** (date column pattern matched multiple columns), tables unmatched, tables excluded, tables with existing overrides preserved |
| SCN-08 | **Two-stage scan** — scanner writes discovered table configs to the `table_configs_staging` Delta table with a `scan_run_id` and `scan_timestamp`. A separate MERGE step reconciles staging into the main `table_configs` table. Delta time travel on staging provides version history |
| SCN-09 | **Table size check** — for each discovered table, run `DESCRIBE DETAIL` to obtain `sizeInBytes`. Comparison is done in bytes (`sizeInBytes >= min_table_size_gb * 1024^3`); the reason string displays GB rounded to 2 decimal places. Tables below `min_table_size_gb` flagged `is_active: false` with reason including measured size and threshold (e.g., `"table size 0.80 GB is below minimum threshold of 2.00 GB"`). Tables where size cannot be determined flagged `is_active: false` with reason `"table size unknown — DESCRIBE DETAIL returned no size information"` |
| SCN-10 | **Staging table content** — includes all fields needed for the main `table_configs` table plus scanner metadata: `scan_run_id`, `scan_timestamp` |
| SCN-11 | **MERGE logic (default)** — when reconciling staging into `table_configs`: (a) new table in staging, not in table_configs → INSERT with `modified_by = 'scanner'`; (b) table in both, `modified_by = 'scanner'` in table_configs → UPDATE from staging; (c) table in both, `modified_by != 'scanner'` in table_configs → preserve (manual edit detected); (d) table in table_configs only, `modified_by = 'scanner'` → set `is_active = false` with reason noting scan timestamp; (e) table in table_configs only, `modified_by != 'scanner'` → preserve unchanged. **Match key:** `table_id` |
| SCN-12 | **REMOVED** — The scanner force flag (`force=true`) has been removed. Forcefully overwriting `table_configs` from staging bypasses the manual-edit detection logic and creates risk of losing operator-curated overrides. If a full reset is needed, the operator manually truncates or updates `table_configs` before re-running the scanner |
| SCN-13 | **Scan summary additions** — report includes `tables_ambiguous`, `tables_below_size_threshold` and `tables_size_unknown` counts alongside existing counters |
| SCN-14 | **Archive path validation** — before scanning tables for a schema, validate that the schema's `archive_base_path` is a valid Unity Catalog External Volume path in the form `/Volumes/{catalog}/{schema}/{volume_name}/`. Query registered volumes via `SHOW VOLUMES` and verify the path prefix matches a known volume. If the path is not a Volume path or no matching volume is found, raise `ArchiveConfigError` directing the admin to create an External Volume and register it in Unity Catalog. This runs early in the scanner — before any table discovery — so invalid storage paths fail fast at onboarding time, not at archive time |
| SCN-15 | **Scanner log table** — every scanner run writes per-table detail rows to a `scanner_log` Delta table (separate from `archive_audit_log`). One row per table per scan. Columns capture: scan_run_id, scan_timestamp, source_catalog, source_schema, source_table, table_columns (all columns as JSON array), date_column_patterns (patterns evaluated as JSON array), matched_pattern (pattern that won, or NULL), matched_column (resolved column, or NULL), all_matched_columns (JSON array of all columns that matched any pattern — for ambiguity visibility), match_status (`matched` / `ambiguous` / `unmatched` / `excluded`), ambiguity_detail (e.g. `"pattern '.*_date$' matched [start_date, end_date]"`), table_size_gb, size_status (`above_threshold` / `below_threshold` / `unknown`), min_table_size_gb (threshold used), is_active, reason, merge_action (`added` / `updated` / `preserved` / `dropped` / `force_overwrite` / NULL for staging-only runs), scanned_by (`current_user()`), workspace_id. Table location uses `audit_catalog` / `audit_schema` from `global_settings` |
| SCN-16 | **Scanner log is diagnostic, not operational** — no runtime code reads the scanner log. It exists purely for operator analysis: identifying ambiguous tables across schemas, tracking scan-over-scan changes, auditing pattern effectiveness. Queryable via SQL, exportable to CSV/Excel from any SQL client |

### 2.3 Archive Process

| ID | Requirement |
|----|------------|
| ARC-01 | Rolling retention window — archive records older than `retention_years` from current date |
| ARC-02 | Year-based partitioning — each year written as a self-contained **Delta External table** at `{base_path}/year_{YYYY}/` with independent `_delta_log`. The archive path must be a Unity Catalog External Volume path (see ARC-14). No managed Delta tables in archive storage |
| ARC-03 | **Watermark-based idempotency** — when a year folder already exists, compare `MAX(watermark_column)` recorded in the audit log against source records. If new records exist above the last watermark, APPEND them. If no new records exist above the last watermark, SKIP the year with status `SKIPPED`. No full re-archive or overwrite is supported — the archive is append-only |
| ARC-04 | Apply exclusion conditions before archiving — records matching ANY condition are excluded (OR logic) |
| ARC-05 | Verify archive record count matches expected count before deleting from source |
| ARC-06 | Optional `delete_after_archive` per table — archive-only mode keeps source intact |
| ARC-07 | **REMOVED** — `year_override` has been removed. Forced re-archiving of a year is not supported. If an archive folder must be corrected, the operator manually removes the folder and its audit entries, then re-runs. Providing an in-process override is dangerous — it is safer to fail and require deliberate human intervention |
| ARC-08 | **REMOVED** — `year_override` overwrite behavior removed. See ARC-07 |
| ARC-09 | **Archive metadata** — write `_archive_metadata.json` alongside each year folder after successful archive. Contains: source table, timestamp, record count, last watermark value, conditions applied, user, retention_years, archive_run_id |
| ARC-10 | **Resumable archive** — before processing a year, check audit table for last status. If status is `ARCHIVED` and `delete_after_archive=true`, resume by executing the delete step |
| ARC-11 | **Archive health check** — `validate_archives()` verifies all expected archive year folders exist in storage for all active tables. Available as a standalone notebook |
| ARC-12 | **Universal incremental append** — when a year folder already exists and new records are detected above the last watermark, APPEND newly-eligible records to the existing year's Delta External table. This behavior applies regardless of `delete_after_archive` mode. Audit logs the append with `record_count` reflecting only the newly-appended records. `_archive_metadata.json` is updated with `"mode": "append"` and the new high-watermark value |
| ARC-13 | **Monotonically increasing watermark column required** (Hard Constraint) — the watermark column (configured as `date_column`) MUST be monotonically increasing. New records must always have a higher watermark value than previously archived records. Back-filled, updated, or otherwise non-monotonic values are not supported — the incremental append logic will silently miss those records. This is enforced as an operator responsibility at configuration time (see Assumption A11) |
| ARC-14 | **External Volumes as archive locations** (see Assumption A12) — all archive paths must be Unity Catalog External Volume paths in the form `/Volumes/{catalog}/{schema}/{volume_name}/`. Direct cloud storage URLs (`s3://`, `abfss://`) are not accepted. The scanner validates this at onboarding time (see SCN-14). Using External Volumes ensures all storage access goes through Unity Catalog governance with no direct cloud manipulation |

### 2.4 Exclusion Conditions

| ID | Requirement |
|----|------------|
| EXC-01 | `same_table` scope — structured conditions using column, operator, value |
| EXC-02 | Supported operators: `equals`, `not_equals`, `in`, `within_years`, `within_months`, `greater_than`, `is_not_null` |
| EXC-03 | `custom_sql` scope — freeform SQL with placeholders resolved at runtime. Required: `{source_alias}`. Optional: `{source_catalog}`, `{source_schema}` (for cross-table references) |
| EXC-04 | Conditions combined with AND (negated OR — "exclude if ANY match" = "keep only if NONE match") |
| EXC-05 | Each condition has a `name` field used in dry-run reporting and audit logs |

### 2.5 Dry-Run Mode

| ID | Requirement |
|----|------------|
| DRY-01 | `dry_run` parameter defaults to `true` — must explicitly set `false` to execute |
| DRY-02 | Dry-run evaluates all conditions and reports per-condition exclusion counts (counts may overlap when a record matches multiple conditions; `would_archive` uses the single combined OR exclusion) |
| DRY-03 | Dry-run report columns are dynamic, driven by condition `name` fields |
| DRY-04 | Dry-run audit logging — see AUD-05 |
| DRY-05 | No data is written, moved, or deleted during dry-run |

### 2.6 Rehydration Process

| ID | Requirement |
|----|------------|
| RHY-01 | All rehydration parameters are runtime (not config) — catalog, schema, years, table prefix |
| RHY-02 | Multiple users can rehydrate the same source table to different target catalogs/schemas simultaneously |
| RHY-03 | Zero-copy only — external table with LOCATION (preferred), SHALLOW CLONE (fallback) |
| RHY-04 | Create unified view combining main table + rehydrated year tables (optional) |
| RHY-05 | Create target schema if it doesn't exist |
| RHY-06 | Verify archive folder exists before attempting to create external table |

### 2.7 Audit Logging

| ID | Requirement |
|----|------------|
| AUD-01 | All archive and rehydration activities logged to Delta audit tables |
| AUD-02 | Audit table location configured in `global_settings` Delta table |
| AUD-03 | Archive audit captures: timestamp, source, target, year, record count, status, conditions applied, user, error_message, archive_run_id, workspace_id, job_id, job_run_id, task_run_id |
| AUD-04 | Rehydration audit captures: timestamp, archive path, source, target, years, tables created, status, user, error_message, archive_run_id, workspace_id, job_id, job_run_id, task_run_id |
| AUD-05 | Dry-run results logged with per-condition counts and status `DRY_RUN` |
| AUD-06 | **archive_run_id** — UUID generated once per job execution, passed to all downstream tasks, stored in every audit row and in `_archive_metadata.json`. **Durable correlation key** — Lakeflow job run history in system tables may age out; this UUID is the long-lived link across audit rows, year folders, and metadata files |
| AUD-07 | **Databricks job context** — workspace_id, job_id, job_run_id, task_run_id captured from notebook context tags and stored in audit rows. Nullable (NULL when running interactively). Enables joins to `system.lakeflow.job_run_timeline` while that data is retained; `archive_run_id` serves as the permanent key |
| AUD-08 | **Enumerated status values** — archive and rehydration statuses use a fixed set of values (see Decisions D8) |

### 2.8 Job Orchestration

| ID | Requirement |
|----|------------|
| JOB-01 | Archive job uses DABs ForEach pattern — accepts required `config_table` job parameter (global_settings Delta table name). `generate_parameters` reads settings, then reads `table_configs` via the pointer, filters active tables, then fans out per table |
| JOB-02 | Concurrency configurable — see CFG-01 |
| JOB-03 | Rehydration job is on-demand, all parameters passed at runtime |
| JOB-04 | Both jobs defined in `resources/archive_job.yml` |
| JOB-05 | **Service principal execution** — in QA, Stage, and Prod environments, jobs run as a service principal (not a user). DABs `run_as` configured per target. The service principal must have: (a) read/write on source catalog/schema, (b) read/write on audit catalog/schema, (c) write on archive storage paths, (d) create external table / create view on rehydration target catalogs. Dev may run as user for interactive testing |
| JOB-06 | **`archived_by` / `rehydrated_by` captures identity** — `current_user()` returns the service principal ID when running as SP, or user email when running interactively. Both are valid; audit queries should handle either format |
| JOB-07 | **M2M OAuth authentication** — see NFR-12 |
| JOB-08 | **Databricks secret scopes for runtime credentials** — see NFR-13 |
| JOB-09 | **Secret scope name in config** — the `global_settings` Delta table includes a `secret_scope` field pointing to the Databricks secret scope for that environment. Code uses this to retrieve runtime secrets |
| JOB-10 | **Setup job for config table creation** — a dedicated DABs job (`resources/setup_job.yml`) runs `notebooks/setup_config_tables.py` to create all 4 config Delta tables using `CREATE TABLE IF NOT EXISTS`. Parameters: `config_catalog`, `config_schema`, `seed_data` (bool). Idempotent — safe to re-run. Required because DABs does not support declarative table creation |

### 2.9 Error Handling

| ID | Requirement |
|----|------------|
| ERR-01 | **Custom exception hierarchy** in `src/exceptions.py` — common base `ArchiveError` with three concrete types: `ArchiveConfigError` (bad config, hard stop), `ArchiveOperationError` (runtime failure for one table), `ArchiveVerificationError` (count mismatch or concurrent run detected) |
| ERR-02 | **Fail-forward per table** — `ArchiveConfigError` stops the entire job (raised in `generate_parameters`). `ArchiveOperationError` and `ArchiveVerificationError` fail the current ForEach task; other tables continue processing |
| ERR-03 | **custom_sql error wrapping** — if a `custom_sql` exclusion condition fails at execution time, catch the exception, wrap it in `ArchiveOperationError` with the SQL text included in the message, and log to audit as `FAILED` |
| ERR-04 | **Structured error messages** — all exceptions include: table identifier, year (if applicable), operation being performed, and root cause. Error messages must be actionable for a data engineer diagnosing the failure from the audit table or job logs |

### 2.10 NULL and Edge Case Handling

| ID | Requirement |
|----|------------|
| EDGE-01 | **NULL date column = required value error** — the date column is required for archiving. Records with a NULL date column are excluded from archiving with `AND {date_column} IS NOT NULL`. They remain in the source table. The count of NULL-date records is logged in the audit table (`null_date_count` column) and in application logs at ERROR level. No configuration needed — NULL dates are always an error condition |
| EDGE-02 | **Schema evolution** — handled natively by Delta. Column additions to the source table between archive runs result in archive year folders with different schemas; Delta merge-on-read resolves this. Type changes that break Delta compatibility surface as Spark errors (caught by ERR-02) |
| EDGE-03 | **Timezone** — all date comparisons use the Spark session timezone. `global_settings` table includes a `timezone` field (default: UTC). Documented in runbook |

### 2.11 Concurrency Guards

| ID | Requirement |
|----|------------|
| CONC-01 | **Optimistic lock via audit table** — before archiving a table+year, write a `STARTED` status row to the audit table with the current `archive_run_id` |
| CONC-02 | **Concurrent run detection** — before writing the archive, check if another `STARTED` row exists for the same table+year with a different `archive_run_id`. If so, skip with status `SKIPPED_CONCURRENT` |
| CONC-03 | **Verify ownership before delete** — the verify-then-delete step re-reads the audit to confirm the archive was written by this run (same `archive_run_id`) before deleting from source |
| CONC-04 | **Delta ACID as safety net** — two writers to the same year folder will get a `ConcurrentAppendException` from Delta, caught and logged as `FAILED` |

### 2.12 Application Logging

| ID | Requirement |
|----|------------|
| LOG-01 | **Python `logging` module** — each module uses a logger named `caresource_archive.{module}` following the standard Python logger hierarchy |
| LOG-02 | **Structured log format** — `[{timestamp}] [{level}] [{archive_run_id}] [{table}] {message}` |
| LOG-03 | **Log levels**: DEBUG (SQL generation, config merge details), INFO (table/year being processed, skip reasons, completion), WARNING (empty custom_sql results, unusual counts), ERROR (NULL date records found — required value missing, operation failures with exception details) |
| LOG-04 | **Notebook summaries** — notebooks display an HTML summary at the end for interactive runs (table of outcomes per year) |
| LOG-05 | **Driver logs** — on Databricks, Python logs go to the driver log, visible in job run output and queryable via `system.compute.driver_logs` if enabled |

### 2.13 Rollback and Recovery

| ID | Requirement |
|----|------------|
| ROLL-01 | **Rehydrate-as-rollback** — rehydration is the rollback mechanism. To reverse an archive, rehydrate the year(s) back to the original catalog/schema |
| ROLL-02 | **Delta time travel as emergency backstop** — after source deletion, records are recoverable via `SELECT * FROM table VERSION AS OF timestamp` for up to 30 days (default Delta retention). Documented in runbook but not automated |
| ROLL-03 | **Archive folder immutability** — archive year folders are append-only after creation. No process overwrites them. There is no `year_override` or force-overwrite mechanism — by design. If a folder must be corrected, the operator manually removes it and re-runs |
| ROLL-04 | **Recommended onboarding practice** — run new tables in `delete_after_archive: false` mode for the first few cycles. Once the team is confident in exclusion conditions and counts, switch to `true`. Documented in runbook |

---

## 3. Non-Functional Requirements

| ID | Requirement |
|----|------------|
| NFR-01 | Modular Python package (`src/`) with thin notebook wrappers (`notebooks/`) |
| NFR-02 | Config parsing and condition SQL generation testable without Spark |
| NFR-03 | Integration tests require Spark session with disposable test tables |
| NFR-04 | No code duplication — shared utilities in `utils.py` |
| NFR-05 | All paths use consistent formatting (no trailing slashes) |
| NFR-06 | **Selective OOP** — classes for components with shared state (AuditLogger, ArchiveEngine, RehydrationEngine); pure functions for stateless logic (config, conditions, scanner, utils). Classes receive `spark` as a constructor parameter — the only platform dependency they need. Notebooks extract values from `dbutils` into `RunContext` and pass `spark` + `RunContext` to constructors. Classes never see `dbutils`. This enables unit testing with mocked `spark` |
| NFR-07 | **Requirements-first tests** — for each module, write tests BEFORE writing implementation code. Tests must be derived from the requirements in this document (Section 2), not from the implementation. This prevents bias: tests should verify what the system *should* do, not what it *happens* to do |
| NFR-08 | **Package management** — `pyproject.toml` at project root with `setuptools` build backend. No external dependencies beyond Spark/Databricks (provided at runtime). DABs deploys the `src/` package alongside notebooks via workspace file sync. Local dev uses `uv` (e.g. `uv sync` for editable install and dev dependencies, `uv run pytest` for tests) |
| NFR-09 | **Environment management** — DABs targets (`dev`, `qa`, `stage`, `prod`) map to environments. Each environment has its own set of config Delta tables with environment-specific values (audit catalog, archive paths, secret scope). The `config_table` job parameter determines the environment — no overlay or merge logic |
| NFR-10 | **CI/CD pipeline** — three stages: (1) PR validation: unit tests + `databricks bundle validate` — no Databricks needed; (2) Deploy to dev on merge to main: `databricks bundle deploy -t dev`; (3) Promote: manual trigger deploys to qa → stage → prod via `databricks bundle deploy -t {target}`. Config lives in Delta tables (not deployed by CI/CD). Higher-environment deploys use a service principal for authentication |
| NFR-11 | **Service principal per environment** — see JOB-05. Permissions (UC grants, storage ACLs) are provisioned outside this project but documented in the runbook |
| NFR-12 | **M2M OAuth for CI/CD** — higher-environment deployments authenticate via OAuth machine-to-machine (`DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`). CI pipeline secrets store the SP credentials — never committed to the repository |
| NFR-13 | **Databricks secret scopes for runtime credentials** — warehouse IDs, external credentials, and sensitive runtime values stored in per-environment Databricks secret scopes (`archive-{env}`). Code accesses via `dbutils.secrets.get()`. Scope creation and population handled by CareSource platform team |

---

## 4. Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Three-tier Delta table config: global_settings + schema_templates + table_configs | Stored in Unity Catalog, scalable to 10,000+ tables, auditable via Delta time travel + audit columns (modified_by, modified_at, change_reason). Each environment has its own tables — no overlay logic |
| D2 | OR exclusion logic | Healthcare safety — better to keep a record than lose one |
| D3 | Rolling window + year folders | Automated cutoff + clean self-contained archives per year |
| D4 | `same_table` + `custom_sql` scopes only | Start simple, add structured `cross_table` later when patterns emerge |
| D5 | Dry-run defaults to true | Safety first — see blast radius before executing |
| D6 | Modular package + thin wrappers | Testable, parallel-developable, no code duplication |
| D7 | Rehydration is runtime-only | Supports multiple concurrent rehydrations to different targets |
| D8 | Enumerated audit statuses: Archive = `STARTED`, `DRY_RUN`, `ARCHIVED`, `ARCHIVED_AND_DELETED`, `FAILED`, `SKIPPED`, `SKIPPED_CONCURRENT`, `NO_DATA`. Rehydration = `SUCCESS`, `PARTIAL_SUCCESS`, `FAILED` | Clear, queryable values. `STARTED` enables concurrency detection. `ARCHIVED` vs `ARCHIVED_AND_DELETED` enables resumability. `SKIPPED_CONCURRENT` distinguishes intentional skips from conflict skips |
| D9 | Store Databricks job context (workspace_id, job_id, job_run_id, task_run_id) in audit tables | Enables joining audit data with `system.lakeflow.job_run_timeline` for operational monitoring |
| D10 | Write `_archive_metadata.json` per year folder | Human-readable provenance for disaster recovery — no code reads it at runtime |
| D11 | Selective OOP: `RunContext` dataclass + `AuditLogger`, `ArchiveEngine`, `RehydrationEngine` classes. Pure functions for config, conditions, scanner | Eliminates parameter threading for shared state while keeping stateless modules simple |
| D12 | Write tests before implementation, derived from requirements | If tests are written after seeing the code, they test what the code does — not what it should do. Requirements-first tests catch design bugs that post-hoc tests miss |
| D13 | Custom exception hierarchy: `ArchiveConfigError`, `ArchiveOperationError`, `ArchiveVerificationError` | Config errors stop the job. Operation errors fail one table, others continue. Verification errors prevent data loss. 3 classes, minimal code, big diagnostic payoff |
| D14 | NULL date = required value error: always exclude, log as ERROR, count in audit | Date column is required for archiving. NULL means the record can't be dated — it stays in source and is flagged as a data quality error. No configuration needed, no `year_0000/` folder. Simple and safe |
| D15 | Audit-table-based optimistic lock for concurrency | No external infrastructure. `STARTED` row + `archive_run_id` check prevents two jobs from archiving the same table+year. Delta ACID is the safety net for writes |
| D16 | Python `logging` module with `[run_id][table]` structured format | Standard library, team already knows it, Databricks captures driver logs. Audit table handles structured outcomes — logging handles diagnostic detail |
| D17 | `pyproject.toml` + workspace file sync via DABs | Enables local `pytest`, clean notebook imports, version tracking. `src/` deployed alongside notebooks via DABs workspace sync — no wheel build needed |
| D18 | DABs targets + per-environment config Delta tables | Each environment has its own config tables with the correct values. The `config_table` job parameter determines the environment — no overlay logic needed |
| D19 | Three-stage CI/CD: PR validation → deploy dev → promote | Unit tests run without Databricks (fast, free). Config validation happens at runtime against Delta tables. Same bundle promotes through environments |
| D20 | Rehydrate-as-rollback + Delta time travel as safety net | No separate rollback code path. Rehydration already restores data. Delta time travel provides 30-day emergency backstop. Recommended practice: run `delete_after_archive: false` for initial cycles |
| D21 | Jobs run as service principal in higher environments | Dev runs as user (interactive testing). QA/Stage/Prod use `run_as` with a service principal in the DABs target. `current_user()` captures SP identity in audit — no code changes needed. SP permissions provisioned outside this project |
| D22 | M2M OAuth for CI/CD deployment to higher environments | CI pipeline authenticates via `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` environment variables (stored in CI secrets, not repo). Same SP used for deploy and `run_as` |
| D23 | Databricks secret scopes for runtime credentials, not config tables | Warehouse IDs and external credentials change per workspace and are sensitive — they belong in secret scopes, not config tables. Config Delta tables hold non-sensitive settings (catalogs, paths, retention). Each environment has its own scope (`archive-{env}`) |
| D24 | Two-stage scan: staging Delta table → MERGE → table_configs | Staging table is the scanner's view; MERGE uses `modified_by` to detect manual edits without field-by-field diff. Delta time travel on staging provides schema evolution history |
| D25 | `min_table_size_gb` required in schema templates, `0` disables | Explicit intent — no silent defaults. Size filtering is a conscious choice per schema |
| D26 | `DESCRIBE DETAIL` for table size, unknown size defaults to inactive | Reliable for Delta (managed and external). Conservative default — unknown means skip, not include |
| D27 | **REMOVED** — Scanner force flag removed | Forceful overwrite of table_configs bypasses manual-edit detection and creates risk of losing operator-curated overrides. If a full reset is needed, operators manually update the table. Safer to fail than to provide an in-process override |
| D28 | Validate `archive_base_path` against UC external locations at onboarding time | Fail-fast: catch missing storage infrastructure before scanning tables. Avoids a silent config that looks valid but fails at archive time when Spark can't write to the path. External locations are the UC-governed way to access cloud storage — if the path isn't registered, the archive job will fail anyway |
| D29 | Ambiguous date column match → flag as inactive, don't guess | For a data archival system, silently picking the wrong date column could archive (or fail to archive) the wrong records. Flagging as `is_active: false` with the ambiguity detail forces the operator to make a conscious choice — either add a specific exact-name pattern or manually set `date_column` in the table config. The scanner log captures all matched columns for every table, making it easy to diagnose and resolve ambiguities across hundreds of tables |
| D30 | Scanner log as a separate Delta table, not part of archive audit | The scanner is an onboarding/diagnostic tool with different cardinality and consumers than the archive runtime. Scanner log rows are per-table-per-scan (potentially thousands); archive audit rows are per-table-per-year-per-run. Separate tables keep queries clean and avoid bloating the operational audit. Delta format on Databricks enables SQL analysis, CSV export, and dashboard creation without extra tooling |
| D32 | **`schema_id` as schema template PK with targeted scanning** | Human-readable `schema_id` replaces composite `(source_catalog, source_schema)` as PK. Both paths always filter `is_active = true`. Single-schema scan uses `schema_id`; scan-all returns all active templates. `(source_catalog, source_schema)` uniqueness enforced in code (Delta doesn't enforce unique beyond PK). Dead `dbutils` parameter removed from `run_scanner` |
| D33 | **Delta External tables for archive storage** | Archive year folders are Delta External tables (LOCATION points to the External Volume path). Not managed Delta tables. This ensures: (a) the archive data persists if a catalog table entry is dropped; (b) rehydration can point a new external table at the same physical path with zero copy; (c) multiple environments can read the same archive folder independently |
| D34 | **Fail rather than override on bad signal** | No in-process force flags for overwriting archives, forcing scanner resets, or bypassing verification. If the process encounters an unexpected state (wrong counts, existing folder that shouldn't be there, concurrent run), it fails with a clear error. The operator investigates, takes corrective action manually, and re-runs. This prevents automated processes from silently correcting data integrity issues in the wrong way |
| D35 | **Unity Catalog External Volumes as the only supported archive storage mechanism** | Direct S3/ABFS paths are rejected. External Volumes provide: (a) UC-governed access control with no direct cloud credential exposure; (b) path stability — the `/Volumes/` namespace is portable across workspace relocations; (c) consistent auditing through Unity Catalog lineage. This is the Databricks best practice for managed external storage |
| D31 | **Universal watermark-driven append — same logic for both `delete_after_archive` modes** | When a year folder already exists: always check the last watermark from the audit log and APPEND records above it, regardless of `delete_after_archive`. This works because: (1) when `delete_after_archive=true`, source has only leftovers (excluded + not-yet-eligible), so watermark filters them correctly; (2) when `delete_after_archive=false`, source retains all records but the watermark still correctly identifies what is new vs. already archived. No `year_override` and no REPLACE — the archive is append-only by design. Sub-folders were rejected because they break the one-Delta-table-per-year model and complicate rehydration |

---

## 5. Out of Scope (for now)

- Structured `cross_table` condition scope (use `custom_sql` for cross-table checks)
- Cloud storage tier management (hot/cool/archive tier transitions)
- Automated rehydration expiry / cleanup
- Email/Slack notifications on job completion (use Databricks job alerts for now)
- UI for config management
- Custom Spark cluster configs (running on serverless)
- RBAC / fine-grained access control beyond service principal job ownership (rely on Unity Catalog permissions and SP grants)
- Service principal provisioning and permission grants (documented in runbook, provisioned by CareSource platform team)
- Secret scope creation and secret population (documented in runbook, provisioned by CareSource platform team per environment)
- CI/CD pipeline implementation (documented in design; pipeline YAML files are CareSource's responsibility to create from the documented stages)
