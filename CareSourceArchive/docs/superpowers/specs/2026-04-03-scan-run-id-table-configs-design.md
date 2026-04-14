# Scan Run ID on table_configs — Design Spec

**Date:** 2026-04-03
**Status:** Draft
**Branch:** `feat/delta_config_build`

## Problem

When the scanner MERGEs rows from `table_configs_staging` into `table_configs`, the scan provenance is lost. `table_configs_staging` has `scan_run_id` and `scan_timestamp`, but `table_configs` only preserves the scan reference as free-text in `change_reason` (e.g. `"added by scan abc-123"`). There is no queryable column to join `table_configs` back to `scanner_log` or `table_configs_staging` to trace which scanner invocation created or last updated a config row.

## Decisions

- Add `scan_run_id STRING` (nullable) to `table_configs`
- No `version` column — drift detection deferred to convention-based approach (dry-runs)
- No Delta Row Tracking — investigated but it provides table-level commit versions, not application-level provenance
- The deactivation UPDATE (rows dropped from a scan) also sets `scan_run_id` to the current run, since that scan caused the deactivation

## Section 1: DDL — `table_configs` table

Add `scan_run_id` after `change_reason`, before the `CONSTRAINT` line:

```sql
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.table_configs (
  table_id                 STRING    NOT NULL  COMMENT 'Logical ID: catalog.schema.table',
  source_catalog           STRING    NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING    NOT NULL  COMMENT 'Unity Catalog schema name',
  source_table             STRING    NOT NULL  COMMENT 'Table name',
  date_column              STRING    NOT NULL  COMMENT 'Resolved date column for retention calculation',
  retention_years          INT                 COMMENT 'Table-specific retention (NULL = inherit from global_settings)',
  archive_base_path        STRING    NOT NULL  COMMENT 'Full archive storage path for this table',
  delete_after_archive     BOOLEAN   NOT NULL  COMMENT 'Whether to delete from source after successful archive',
  is_active                BOOLEAN   NOT NULL  COMMENT 'Include in archive runs',
  reason                   STRING              COMMENT 'Reason when inactive (scanner or manual)',
  exclusion_conditions     ARRAY<STRUCT<
    name:     STRING,
    scope:    STRING,
    column:   STRING,
    operator: STRING,
    value:    STRING,
    sql:      STRING
  >>                                           COMMENT 'Business rules to protect records from archiving',
  modified_by              STRING    NOT NULL  COMMENT 'Who last modified — scanner uses "scanner"',
  modified_at              TIMESTAMP NOT NULL  COMMENT 'When last modified',
  change_reason            STRING              COMMENT 'Why the change was made',
  scan_run_id              STRING              COMMENT 'UUID of the scanner run that last inserted or updated this row',

  CONSTRAINT pk_table_configs PRIMARY KEY (table_id)
)
COMMENT 'Per-table archive configuration — primary input for archive and dry-run jobs.'
```

No change to `table_configs_staging` — it already has `scan_run_id`.

## Section 2: MERGE SQL — `merge_staging_to_final` in `src/scanner.py`

### INSERT (new rows)

Add `scan_run_id` to the column list and values:

```sql
WHEN NOT MATCHED THEN
  INSERT (..., scan_run_id)
  VALUES (..., source.scan_run_id)
```

### UPDATE (existing scanner-managed rows)

Add `scan_run_id` to the SET clause:

```sql
WHEN MATCHED AND (target.modified_by = 'scanner' OR {force_cond}) THEN
  UPDATE SET
    ...,
    target.scan_run_id = source.scan_run_id
```

### Deactivation UPDATE (rows dropped from scan)

The existing UPDATE that marks missing rows inactive also gets `scan_run_id`:

```sql
UPDATE {table_configs_table}
SET is_active = false,
    reason = CONCAT('dropped: not found in scan ...'),
    scan_run_id = {sql_quote(scan_run_id)},
    ...
WHERE modified_by = 'scanner'
  AND table_id NOT IN (SELECT table_id FROM {staging_table} WHERE scan_run_id = ...)
```

## Section 3: What doesn't change

- **`src/config.py`** — `load_table_configs` uses `SELECT *`, so it picks up `scan_run_id` automatically. No validation needed — it's informational.
- **`src/archiver.py`**, **`src/audit.py`**, **`src/conditions.py`**, **`src/rehydrator.py`** — don't read or write `scan_run_id`.
- **`table_configs_staging`** — already has `scan_run_id`.
- **`scanner_log`** — already logs `scan_run_id` per row.
- **`write_staging` in `src/scanner.py`** — already writes `scan_run_id` to staging. No change needed.

## Section 4: Testing

### `tests/unit/test_scanner.py`

Update existing `merge_staging_to_final` tests:

- Verify the MERGE INSERT SQL includes `scan_run_id` in columns and values
- Verify the MERGE UPDATE SQL sets `target.scan_run_id = source.scan_run_id`
- Verify the deactivation UPDATE SQL sets `scan_run_id`

### No new test files needed

This is an additive column change. Existing test patterns (mock spark, assert SQL content) cover it.

## Files changed

| File | Change |
|------|--------|
| `notebooks/setup_config_tables.py` | DDL: add `scan_run_id` column |
| `src/scanner.py` | `merge_staging_to_final`: add `scan_run_id` to INSERT, UPDATE, and deactivation SQL |
| `tests/unit/test_scanner.py` | Verify `scan_run_id` appears in MERGE and deactivation SQL |
