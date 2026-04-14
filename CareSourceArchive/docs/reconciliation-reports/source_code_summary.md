# Source Code Summary — `src/`

Use this reference to identify reusable functions across the CareSource Archive codebase.

---

## src/utils.py — Shared utilities (SQL builders, Spark helpers, path/naming)

The workhorse module. Pure functions for SQL generation, Spark row handling, path construction, logging setup, and secret loading. Most functions here are already reused across multiple modules.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `generate_archive_run_id` | `() -> str` | UUID-based run ID for each archive/scan session |
| `build_archive_path` | `(base_path, table_name, year) -> str` | Construct `/base/table/year_YYYY` path |
| `build_full_table_name` | `(catalog, schema, table) -> str` | `catalog.schema.table` string |
| `source_fq_from_config` | `(table_config) -> str` | Build FQ source table name from config dict (wraps `build_full_table_name`) |
| `archive_path_from_config` | `(table_config, year) -> str` | Build archive Delta path from config dict and year (wraps `build_archive_path`) |
| `create_schema_if_not_exists` | `(spark, catalog, schema) -> None` | DDL wrapper for schema creation |
| `archive_folder_exists` | `(dbutils, base_path, table_name, year) -> bool` | Check if delta folder exists via `dbutils.fs.ls` |
| `row_value` | `(row, field, default=None) -> Any` | Safe field extraction from Spark Row, dict, or similar |
| `row_to_dict` | `(row) -> dict` | Convert Spark Row to plain dict |
| `collect_column` | `(spark, sql, field) -> list` | Run SQL, return single column as list |
| `ensure_table_exists` | `(spark, fq_table) -> None` | Verify Delta table exists via DESCRIBE; raises `ArchiveConfigError` |
| `ensure_table_with_setup_message` | `(spark, fq_table, label="Table") -> None` | Verify table exists; raise with setup-specific hint if not |
| `spark_count` | `(spark, sql) -> int` | Run SQL returning a `count` column and return the int result |
| `configure_logging` | `(archive_run_id) -> None` | Set up root logger with run-ID filter and formatter |
| `load_secrets` | `(settings, dbutils) -> dict` | Pull `warehouse_id`, `client_id`, `client_secret` from secret scope |
| `sql_quote` | `(s: str) -> str` | Single-quote a string, escaping embedded quotes |
| `sql_str_or_null` | `(val) -> str` | Quote string or return `NULL` literal |
| `sql_int` | `(val: int) -> str` | Cast to int string for SQL |
| `sql_int_or_null` | `(val) -> str` | Int string or `NULL` |
| `sql_bool` | `(b: bool) -> str` | `true` / `false` literal |
| `sql_expr` | `(expr: str) -> str` | Passthrough for trusted SQL expressions (e.g. `current_timestamp()`) |
| `build_insert_values_sql` | `(table, columns, values) -> str` | Single-row `INSERT INTO ... VALUES (...)` builder |
| `build_multi_insert_values_sql` | `(table, columns, rows) -> str` | Multi-row `INSERT INTO ... VALUES (...), (...)` builder |

**Dataclass:**

| Class | Fields | Purpose |
|-------|--------|---------|
| `RunContext` | `settings: dict, secrets: dict, job_context: dict, archive_run_id: str` | Immutable session context passed to engines |

**Helper:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `get_job_context` | `(dbutils) -> dict` | Extract `workspace_id`, `job_id`, `job_run_id`, `task_run_id` from notebook context tags |

---

## src/config.py — Configuration loading and validation

Loads global settings, table configs, and schema templates from Delta tables. Validates required fields, timezone strings, exclusion-condition JSON, and template constraints.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `load_settings` | `(spark, config_table) -> dict` | Load and validate global settings row |
| `load_table_configs` | `(spark, table_configs_table, active_only=True, filter_expr=None) -> list[dict]` | Load table configs with optional active/expression filter; validates each row |
| `load_schema_templates` | `(spark, schema_templates_table, schema_id=None) -> list[dict]` | Load schema templates; single or all active; validates uniqueness and `min_table_size_gb` |
| `validate_table_config_dict` | `(d) -> None` | Validate required keys, timezone, and exclusion conditions on a single config dict |
| `validate_exclusion_conditions_json` | `(raw, *, table_id=None) -> None` | Parse and validate exclusion-conditions JSON string (operators, condition keys, types) |
| `get_active_tables` | `(configs) -> list[dict]` | Filter configs to `is_active=True` |
| `merge_settings` | `(global_settings, table_config) -> dict` | Merge global defaults into per-table config (e.g. `retention_years` fallback) |

**Internal helpers:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_validate_timezone_string` | `(tz_value, *, field, table_id) -> None` | Validate via `ZoneInfo`; raises `ArchiveConfigError` |
| `_require_non_empty_str` | `(d, key, *, table_id) -> None` | Assert dict key is a non-empty string |
| `_assert_unique_table_ids` | `(rows) -> None` | Ensure no duplicate `table_id` values |
| `_validate_template_row` | `(d) -> None` | Validate `min_table_size_gb` is a non-negative number |

---

## src/conditions.py — Exclusion condition SQL generation

Translates JSON exclusion conditions into SQL WHERE clause fragments. Supports `same_table` predicates and `custom_sql` templates with placeholder substitution.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `build_exclusion_clause` | `(conditions, source_catalog, source_schema, source_alias) -> str` | Build combined `NOT (...)  AND NOT (...)` clause from all conditions |
| `build_individual_condition_sql` | `(condition, source_catalog, source_schema, source_alias) -> str` | Single condition → SQL predicate (delegates to type handler) |
| `get_condition_names` | `(conditions) -> list[str]` | Extract `name` field from each condition dict |

**Internal helpers:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_substitute_custom_sql` | `(value, source_catalog, source_schema, source_alias) -> str` | Replace `{source_alias}`, `{source_catalog}`, `{source_schema}` placeholders |
| `_same_table_predicate` | `(column, operator, value, source_alias) -> str` | Operator dispatch: `equals`, `not_equals`, `in`, `within_years`, `within_months`, `greater_than`, `is_not_null` |

---

## src/archiver.py — Archive engine (write, verify, delete)

The core archive workflow. Calculates eligible years by retention policy, writes data to Delta archive paths, verifies counts, optionally deletes from source, and writes metadata JSON. Supports dry-run mode, incremental append via watermark, and concurrent-run protection.

**Class: `ArchiveEngine(ctx, audit, spark)`**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `run` | `(table_config, dry_run=True, dbutils=None) -> dict` | Main entry point — dry-run report or live archive for one table |
| `validate_archives` | `(table_configs, dbutils) -> dict` | Check all expected archive folders exist; returns `{valid, missing}` |

**Key internal methods:**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `_calculate_eligible_years` | `(table_config, retention_years) -> list[int]` | Distinct years from source where year <= current - retention |
| `_year_where_sql` | `(table_config, year, exclusion_clause, alias, after_watermark=None) -> str` | WHERE fragment for eligible rows; optional watermark for incremental |
| `_archive_year` | `(table_config, year, exclusion_clause) -> int` | CREATE delta archive table; returns archived count |
| `_verify_archive` | `(table_config, year, expected_count) -> None` | Count-match check; raises `ArchiveVerificationError` on mismatch |
| `_delete_archived` | `(table_config, year, exclusion_clause) -> None` | DELETE from source table for archived year |
| `_write_metadata` | `(table_config, year, record_count, conditions, mode, dbutils, ...) -> None` | Write `_archive_metadata.json` sidecar to archive folder |
| `_archive_table_year` | `(merged, year, exclusion_clause, conditions, dbutils) -> dict` | Full live flow for one year: audit, check concurrent, archive, verify, delete, metadata |
| `_dry_run_year` | `(table_config, year, conditions, exclusion_clause) -> dict` | Counts only — total eligible, would-archive, per-condition, nulls |
| `_count_source_by_year` | `(table_config, year) -> int` | Total rows in source for a given year |
| `_count_archive_delta` | `(table_config, year, exclusion_clause) -> int` | Rows in delta archive for a given year |
| `_count_new_records` | `(table_config, year, last_watermark, exclusion_clause) -> int` | Incremental rows beyond last watermark |
| `_count_nulls` | `(table_config, year) -> int` | Count rows with NULL watermark column |
| `_get_watermark_value` | `(table_config, year) -> Optional[str]` | MAX watermark from existing archive |
| `_exclusion_clause_for_config` | `(table_config) -> str` | Parse and build exclusion clause from config JSON |
| `_set_timezone` | `(merged) -> None` | Set Spark session timezone |

**Module-level helpers:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_extract_year` | `(row, key="yr") -> int` | Extract year int from dict or Row |
| `_parse_conditions` | `(raw) -> list` | Parse exclusion conditions JSON or return empty list |
| `_resolve_exclusion` | `(exclusion_clause, strip_alias=False) -> str` | Return clause or `1=1` fallback; optionally strip `src.` alias prefix |

---

## src/audit.py — Audit logging (archive and rehydration)

Writes structured audit records to Delta audit tables for both archive and rehydration operations. Supports status tracking, concurrency detection, ownership verification, and resume-state queries.

**Class: `AuditLogger(ctx, spark)`**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `ensure_archive_audit_table` | `() -> None` | Verify archive audit table exists (delegates to `ensure_table_with_setup_message`) |
| `ensure_rehydration_audit_table` | `() -> None` | Verify rehydration audit table exists (delegates to `ensure_table_with_setup_message`) |
| `log_archive` | `(table, year, status, record_count, conditions_applied=None, null_date_count=None, error_message=None, watermark_value=None, source_year_count=None, archive_mode=None) -> None` | Insert one archive audit row |
| `log_rehydrate` | `(archive_path, source, target_catalog, target_schema, years, tables_created, status, error_message=None) -> None` | Insert one rehydration audit row |
| `log_dry_run` | `(table, year, total_eligible, would_archive, per_condition_counts, null_date_count=None) -> None` | Log dry-run stats as a DRY_RUN audit row |
| `is_archived_by_run` | `(table, year, archive_run_id) -> bool` | Check if the given run owns an ARCHIVED entry for this table+year |
| `check_resume_state` | `(table_config, year) -> Optional[str]` | Latest audit status for table+year (delegates to `get_latest_status`) |
| `get_last_run_state` | `(table, year) -> tuple` | Latest successful `(status, watermark_value, source_year_count)` |
| `get_latest_status` | `(table, year) -> Optional[tuple]` | Most recent `(status, archive_run_id)` regardless of success |
| `check_concurrent` | `(table, year, archive_run_id, stale_threshold_hours=4) -> tuple` | Detect concurrent/stale STARTED entries from other runs; returns `(is_concurrent, is_stale, foreign_run_id, age_hours)` |

**Constants:**

| Name | Purpose |
|------|---------|
| `ALLOWED_ARCHIVE_STATUSES` | Valid status values for archive audit rows |
| `ARCHIVE_AUDIT_COLUMNS` | Column list for archive audit table |
| `REHYDRATION_AUDIT_COLUMNS` | Column list for rehydration audit table |

---

## src/scanner.py — Schema scanning and table-config generation

Scans Unity Catalog schemas to discover tables, match watermark columns by pattern, check size thresholds, and merge results into the config table via a staging table. Produces detailed scan logs.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `run_scanner` | `(spark, settings, force=False, schema_id=None) -> dict` | Full scan pipeline: discover → match → size-check → stage → merge → log |
| `scan_schema` | `(spark, template) -> list[str]` | List table names in a catalog.schema |
| `match_watermark_column` | `(table_columns, patterns) -> tuple` | Match columns against patterns (exact then regex); returns `(col, pattern, all_matched, status)` |
| `generate_table_config` | `(template, table_name, matched_column) -> dict` | Build an active table-config dict from template + matched column |
| `flag_unmatched` | `(template, table_name) -> dict` | Build inactive config with "no match" reason |
| `flag_ambiguous` | `(template, table_name, pattern, matched_columns) -> dict` | Build inactive config with ambiguity reason |
| `get_table_size_gb` | `(spark, catalog, schema, table) -> Optional[float]` | Table size via `DESCRIBE DETAIL`; returns `None` for views |
| `check_size_threshold` | `(table_size_gb, min_table_size_gb) -> tuple[bool, Optional[str]]` | Compare size against minimum; returns `(passes, reason)` |
| `write_staging` | `(spark, configs, staging_table, scan_run_id) -> None` | Bulk-insert scanned configs into staging table |
| `merge_staging_to_final` | `(spark, staging_table, table_configs_table, scan_run_id, force=False) -> None` | MERGE staging into config table; marks dropped tables inactive |
| `validate_archive_path` | `(spark, archive_base_path) -> None` | Verify path is under an external volume or external location |
| `ensure_scanner_log_table` | `(spark, settings) -> None` | Verify scanner_log table exists (delegates to `ensure_table_with_setup_message`) |
| `write_scanner_log` | `(spark, settings, scan_run_id, table_results) -> None` | Bulk-insert detailed per-table scan results |

**Internal helpers:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_scanner_table_config` | `(template, table_name, **overrides) -> dict` | Base config dict builder with template defaults |
| `_validate_volume_is_external` | `(spark, archive_base_path) -> None` | DESCRIBE VOLUME and assert EXTERNAL type |
| `_staging_table_from_configs` | `(table_configs_table) -> str` | Derive staging table name from config table name |
| `_list_table_columns` | `(spark, catalog, schema, table) -> list[str]` | Column names from `information_schema.columns` |
| `_make_log_entry` | `(tid, cat, sch, table_name, match_status, **overrides) -> dict` | Template for a scanner log row |
| `_empty_scan_summary` | `() -> dict` | Zero-initialized summary counters |
| `_scanner_log_fq` | `(settings) -> str` | Fully-qualified scanner_log table name |

---

## src/rehydrator.py — Rehydration engine (restore archived data)

Creates external Delta tables pointing at archive paths and a unified view that unions live source + restored year tables.

**Class: `RehydrationEngine(ctx, audit, spark)`**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `run` | `(params: dict) -> dict` | Main entry: create per-year external tables + unified view; audit log on success/failure |

**Internal methods:**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `_source_base_name` | `(source_table) -> str` | Extract table name from dotted path |
| `_create_external_table` | `(fq_table, loc_path) -> None` | CREATE TABLE USING DELTA LOCATION, fallback to SHALLOW CLONE |

---

## src/exceptions.py — Custom exception hierarchy

Three-level hierarchy for structured error handling across all modules.

| Class | Constructor | Purpose |
|-------|-------------|---------|
| `ArchiveError` | `(msg)` | Base exception for all archive errors |
| `ArchiveConfigError` | `(msg=None, *, field=None, table_id=None, source=None)` | Configuration/validation errors — auto-formats message from keyword args |
| `ArchiveOperationError` | `(msg=None, *, table=None, year=None, operation=None, reason=None)` | Runtime archive/delete operation failures |
| `ArchiveVerificationError` | `(msg=None, *, table=None, year=None, expected=None, actual=None, reason=None)` | Post-write count-mismatch verification failures |

---

## Reuse Candidates at a Glance

Functions shared across 2+ modules (high reuse value):

| Function | Defined in | Used by |
|----------|-----------|---------|
| `source_fq_from_config` | utils | archiver |
| `archive_path_from_config` | utils | archiver |
| `spark_count` | utils | archiver |
| `ensure_table_with_setup_message` | utils | audit, scanner |
| `build_full_table_name` | utils | scanner |
| `build_archive_path` | utils | rehydrator |
| `archive_folder_exists` | utils | archiver, rehydrator |
| `sql_quote` | utils | archiver, audit, scanner, config |
| `row_value` | utils | audit, scanner |
| `row_to_dict` | utils | config, scanner |
| `collect_column` | utils | scanner |
| `ensure_table_exists` | utils | (used internally by `ensure_table_with_setup_message`) |
| `build_insert_values_sql` | utils | audit |
| `build_multi_insert_values_sql` | utils | scanner |
| `sql_str_or_null` / `sql_int_or_null` / `sql_bool` / `sql_expr` | utils | audit, scanner |
| `build_exclusion_clause` | conditions | archiver |
| `build_individual_condition_sql` | conditions | archiver |
| `get_condition_names` | conditions | archiver |
| `merge_settings` | config | archiver |
| `load_table_configs` | config | scanner |
| `load_schema_templates` | config | scanner |
| `ArchiveConfigError` | exceptions | config, utils, audit, scanner, conditions |
| `ArchiveOperationError` | exceptions | archiver, rehydrator |
| `ArchiveVerificationError` | exceptions | archiver |

Functions with single-caller usage (candidates for future promotion):

| Function | Module | Single caller |
|----------|--------|---------------|
| `_extract_year` | archiver | archiver only |
| `_parse_conditions` | archiver | archiver only |
| `_resolve_exclusion` | archiver | archiver only |
| `get_job_context` | utils | notebook entry points only |
| `configure_logging` | utils | notebook entry points only |
| `load_secrets` | utils | notebook entry points only |
| `create_schema_if_not_exists` | utils | rehydrator only |
