# Catalog + Schema Targeting for Archive Job — Design Spec

**Date:** 2026-04-03
**Status:** Approved
**Branch:** `feat/delta_config_build`

## Problem

The archive job has no ergonomic way to target a single schema. Operators must write raw SQL via `table_config_filter` (e.g. `source_schema = 'claims'`), which:

1. Is easy to get wrong — a typo silently returns zero tables and the job succeeds with nothing done
2. Requires knowing internal column names
3. Has no equivalent of the scanner's validated `schema_id` targeting

## Decisions

- Add `source_catalog` and `source_schema` as first-class job parameters
- Both are optional; if neither is set, behavior is unchanged (all active tables run)
- `source_schema` without `source_catalog` is an error — schema names are not unique across catalogs
- If a filter is provided (catalog/schema or raw) and zero active tables match, raise `ArchiveConfigError` — mirrors the scanner's "schema_id not found" behavior
- `table_config_filter` is kept and ANDs with the catalog/schema filter — operators can still refine further
- No changes to `src/` — all logic lives in `generate_parameters.py` where parameter-wiring already happens

## Section 1: `notebooks/generate_parameters.py`

### New widgets (after existing `table_config_filter` widget)

```python
dbutils.widgets.text("source_catalog", "", "Source catalog (optional, requires source_schema)")
dbutils.widgets.text("source_schema",  "", "Source schema (optional, requires source_catalog)")
```

### Parse and validate

```python
source_catalog = dbutils.widgets.get("source_catalog").strip() or None
source_schema  = dbutils.widgets.get("source_schema").strip()  or None

if source_schema and not source_catalog:
    raise ArchiveConfigError("source_catalog is required when source_schema is set.")
```

### Build combined filter

Uses `sql_quote()` from `src/utils.py` — same injection-safe pattern as `load_schema_templates` in `src/config.py`.

```python
filter_parts = []
if source_catalog and source_schema:
    filter_parts.append(
        f"source_catalog = {sql_quote(source_catalog)} AND source_schema = {sql_quote(source_schema)}"
    )
if table_config_filter:
    filter_parts.append(f"({table_config_filter})")
filter_expr = " AND ".join(filter_parts) or None
```

### Zero-row guard (after `load_table_configs`)

```python
if not table_configs and filter_expr:
    raise ArchiveConfigError(
        f"No active table_configs matched filter: {filter_expr}"
    )
```

### Summary JSON gains two new fields for observability

```python
"source_catalog": source_catalog,
"source_schema":  source_schema,
```

## Section 2: Job YML — both `archive_job.yml` and `archive_job_serverless.yml`

Add under `parameters` (both files):

```yaml
- name: source_catalog
  default: ""
- name: source_schema
  default: ""
```

Add under `base_parameters` for the `generate_parameters` task (both files):

```yaml
source_catalog: "{{job.parameters.source_catalog}}"
source_schema:  "{{job.parameters.source_schema}}"
```

## Section 3: What does NOT change

- **`src/config.py`** — `load_table_configs` signature and implementation unchanged
- **`src/archiver.py`**, **`src/audit.py`**, **`src/scanner.py`**, **`src/rehydrator.py`** — no changes
- **`notebooks/run_archive.py`** — no changes; it receives `foreach_payload` from ForEach, unchanged
- **`resources/scanner_job.yml`**, **`resources/scanner_job_serverless.yml`** — no changes

## Section 4: CLI usage

```bash
# Archive a specific schema
databricks bundle run caresource_archive_run -t dev \
  --source_catalog prod_catalog \
  --source_schema claims

# Archive a specific schema, further filtered
databricks bundle run caresource_archive_run -t dev \
  --source_catalog prod_catalog \
  --source_schema claims \
  --table_config_filter "table_id LIKE '%.claim_%'"

# Archive all active tables (unchanged behavior)
databricks bundle run caresource_archive_run -t dev
```

## Section 5: Testing

No new unit tests required — `generate_parameters.py` is a Databricks notebook, not unit-tested directly. Manual verification: run the archive job in dev with `source_catalog` + `source_schema` set and confirm `table_count` in the printed JSON reflects only that schema's active tables.

## Files changed

| File | Change |
|------|--------|
| `notebooks/generate_parameters.py` | Add `source_catalog`/`source_schema` widgets, combined filter logic, zero-row guard, summary fields |
| `resources/archive_job.yml` | Add `source_catalog` and `source_schema` job parameters + base_parameters |
| `resources/archive_job_serverless.yml` | Same as above |
