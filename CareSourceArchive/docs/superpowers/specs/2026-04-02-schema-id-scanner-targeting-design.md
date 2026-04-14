# Schema ID & Scanner Targeting — Design Spec

**Date:** 2026-04-02
**Status:** Approved
**Branch:** `feat/delta_config_build`

## Problem

The scanner job scans every row in `schema_templates` with no way to:
1. Target a single schema template
2. Filter by `is_active` flag
3. Identify templates with a short human-readable key

## Decisions

- `schema_id`: user-chosen human-readable string (e.g. `claims`, `members`)
- `schema_id` becomes the new PK; `(source_catalog, source_schema)` retains a uniqueness constraint (informational — enforced in code since Delta doesn't enforce unique beyond PK)
- Scanner accepts one `schema_id` at a time (not comma-separated); empty means scan all active
- Both paths (single and scan-all) always filter on `is_active = true`
- Remove dead `dbutils=None` parameter from `run_scanner`

## Section 1: DDL — `schema_templates` table

Add `schema_id STRING NOT NULL` as the first column. Replace the composite PK with `PRIMARY KEY (schema_id)`.

```sql
CREATE TABLE IF NOT EXISTS {config_catalog}.{config_schema}.schema_templates (
  schema_id                STRING         NOT NULL  COMMENT 'Human-readable unique identifier for this template',
  source_catalog           STRING         NOT NULL  COMMENT 'Unity Catalog catalog name',
  source_schema            STRING         NOT NULL  COMMENT 'Unity Catalog schema name',
  date_column_patterns     ARRAY<STRING>  NOT NULL  COMMENT 'Ordered list of regex patterns for date column matching',
  default_retention_years  INT            NOT NULL  COMMENT 'Default retention for tables in this schema',
  archive_base_path        STRING         NOT NULL  COMMENT 'Base archive storage path for this schema',
  delete_after_archive     BOOLEAN        NOT NULL  COMMENT 'Default delete behavior for tables in this schema',
  min_table_size_gb        DOUBLE         NOT NULL  COMMENT 'Minimum table size in GB for scanner to mark active',
  exclude_tables           ARRAY<STRING>            COMMENT 'Table names to skip during scanning',
  description              STRING                   COMMENT 'Human-readable description of this schema group',
  is_active                BOOLEAN        NOT NULL  COMMENT 'Whether to include in scanner runs',
  modified_by              STRING         NOT NULL  COMMENT 'Who last modified this row',
  modified_at              TIMESTAMP      NOT NULL  COMMENT 'When last modified',
  change_reason            STRING                   COMMENT 'Why the change was made',
  CONSTRAINT pk_schema_templates PRIMARY KEY (schema_id)
)
```

Seed data gains `schema_id` as the first value: `'claims'`, `'members'`, `'provider'`.

## Section 2: Config layer — `load_schema_templates` in `src/config.py`

**New signature:** `load_schema_templates(spark, schema_templates_table, schema_id=None)`

Both paths share a common tail:
- Per-row validation: `min_table_size_gb` must be present, numeric, non-negative (existing checks, unchanged)
- Runs on every template row before returning

### When `schema_id` is provided

1. Escape `schema_id` using `sql_quote()` from `src/utils.py` to prevent SQL injection
2. Query: `SELECT * FROM {table} WHERE schema_id = {sql_quote(schema_id)} AND is_active = true`
3. If zero rows, run a second query without the `is_active` filter (`SELECT 1 FROM {table} WHERE schema_id = {sql_quote(schema_id)} LIMIT 1`) to disambiguate:
   - Row exists but inactive → `ArchiveConfigError(msg="schema_id '{x}' is inactive")`
   - Row doesn't exist → `ArchiveConfigError(msg="schema_id '{x}' not found")`
4. If more than one row → `ArchiveConfigError(msg="duplicate schema_id '{x}'")` (defensive)
5. Run per-row validation
6. Return list with one template

### When `schema_id` is not provided (scan-all)

1. Query: `SELECT * FROM {table} WHERE is_active = true`
2. Validate no duplicate `(source_catalog, source_schema)` pairs in result — raise if found
3. Run per-row validation on all rows
4. Return all active templates

## Section 3: Scanner + job parameter plumbing

### `run_scanner` in `src/scanner.py`

**Old signature:** `run_scanner(spark, settings, dbutils=None, force=False)`
**New signature:** `run_scanner(spark, settings, force=False, schema_id=None)`

- Remove dead `dbutils` parameter (never used inside the function)
- Pass `schema_id` through to `load_schema_templates`:

```python
templates = config.load_schema_templates(
    spark, settings["schema_templates_table"], schema_id=schema_id
)
```

Everything downstream unchanged — `run_scanner` iterates whatever templates it gets back.

### `notebooks/run_scanner.py`

Add widget:
```python
dbutils.widgets.text("schema_id", "", "Schema template ID (optional)")
```

Parse: empty string → `None`, otherwise the string value.
Pass to `run_scanner(..., schema_id=schema_id)`.

### `resources/scanner_job_serverless.yml` and `resources/scanner_job.yml`

Add job parameter and base parameter:
```yaml
parameters:
  - name: config_table
    default: ""
  - name: schema_id
    default: ""
  - name: force
    default: "false"
tasks:
  - task_key: run_scanner
    notebook_task:
      notebook_path: ../notebooks/run_scanner.py
      base_parameters:
        config_table: "{{job.parameters.config_table}}"
        schema_id: "{{job.parameters.schema_id}}"
        force: "{{job.parameters.force}}"
```

### CLI usage

- Scan one schema: `databricks bundle run caresource_scanner -t dev -- --schema_id claims`
- Scan all active: `databricks bundle run caresource_scanner -t dev`

### Edge case: zero active templates

If the scan-all path returns zero templates, `run_scanner` should return early with all-zero summary counts and skip `write_staging` / `merge_staging_to_final`. This prevents an empty staging set from marking all existing `modified_by = 'scanner'` configs inactive.

## Section 4: Testing + seed data

### Seed data (`notebooks/setup_config_tables.py`)

- Update `DDL_SCHEMA_TEMPLATES` to new DDL from Section 1
- Update `SEED_SCHEMA_TEMPLATES` INSERT values to include `schema_id` as first column

### Existing tests to update

**`tests/unit/test_config.py`** — 3 existing `load_schema_templates` tests:
- Update mock rows to include `schema_id` and `is_active` fields
- Add new tests:
  - `schema_id` provided → returns single active template
  - `schema_id` provided but inactive → raises `ArchiveConfigError`
  - `schema_id` provided but not found → raises `ArchiveConfigError`
  - `schema_id` provided with duplicates (defensive) → raises `ArchiveConfigError`
  - No `schema_id` → returns only `is_active = true` rows
  - No `schema_id` with duplicate `(source_catalog, source_schema)` → raises

**`tests/unit/test_scanner.py`** — 3 existing `run_scanner` calls:
- Remove `dbutils=None` from all call sites
- Add tests:
  - `run_scanner` with `schema_id` passes it through to `load_schema_templates`
  - `run_scanner` without `schema_id` scans all active
  - Scan-all with zero active templates → summary all zeros, `write_staging` / `merge_staging_to_final` not invoked

### Dead code cleanup

- Remove `dbutils=None` from `run_scanner` signature in `src/scanner.py`
- Update all test call sites that pass `dbutils=None`
- Check `notebooks/run_scanner.py` caller — remove `dbutils` pass-through

## Files changed

| File | Change |
|------|--------|
| `notebooks/setup_config_tables.py` | DDL + seed data: add `schema_id` column |
| `src/config.py` | `load_schema_templates`: add `schema_id` param, `is_active` filtering, validation |
| `src/scanner.py` | `run_scanner`: remove `dbutils`, add `schema_id` pass-through |
| `notebooks/run_scanner.py` | Add `schema_id` widget, remove `dbutils` pass-through |
| `resources/scanner_job_serverless.yml` | Add `schema_id` job + base parameter |
| `resources/scanner_job.yml` | Add `schema_id` job + base parameter |
| `tests/unit/test_config.py` | Update existing + add new `load_schema_templates` tests |
| `tests/unit/test_scanner.py` | Update `run_scanner` calls, add `schema_id` tests |
| `notebooks/manual/validate_config.py` | `load_schema_templates` call now returns active-only by default (intentional — validation checks what the scanner will see) |
