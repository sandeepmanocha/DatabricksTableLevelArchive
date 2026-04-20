# DAB Commands Runbook

Copy-paste `databricks bundle` commands for deploying and running all CareSource Archive jobs.

---

## Placeholders

| Placeholder | Description | Example |
|---|---|---|
| `<target>` | Bundle target | `dev-serverless`, `dev`, `qa`, `stage`, `prod` |
| `<profile>` | `~/.databrickscfg` profile | `fe-sandbox-manocha` |
| `<config_table>` | 3-level global_settings table | `dev2_archive.metadata.global_settings` |
| `<schema_id>` | Schema template ID | `dev2_archive__source_data_samples` |

### Per-target config tables

| Target | `<config_table>` |
|---|---|
| `dev-serverless` | `dev2_archive.metadata.global_settings` |
| `dev` | `dev2_archive.metadata.global_settings` |
| `qa` | `qa_archive_operations.config.global_settings` |
| `stage` | `stage_archive_operations.config.global_settings` |
| `prod` | `prod_archive_operations.config.global_settings` |

---

## 1. Deploy

Deploy all resources to the target workspace. Run this before any job run when code has changed.

```bash
databricks bundle deploy -t <target> --profile <profile>
```

---

## 2. Setup — create config tables

Creates the `global_settings`, `schema_templates`, and `table_configs` Delta tables in Unity Catalog. Run once per environment or when DDL changes.

```bash
databricks bundle run setup_config_tables \
  -t <target> \
  --profile <profile>
```

No extra parameters — catalog and schema are injected from the target variables.

---

## 3. Seed config

Seeds initial rows into `global_settings` and `schema_templates`. Run once after setup, or to reset config to defaults.

```bash
databricks bundle run seed_config \
  -t <target> \
  --profile <profile>
```

No extra parameters — catalog, schema, and source_schema are injected from the target variables.

---

## 4. Generate test data (dev only)

Populates source tables with synthetic claims, members, and providers data for testing.

```bash
databricks bundle run generate_test_data \
  -t <target> \
  --profile <profile>
```

With custom catalog/schema:

```bash
databricks bundle run generate_test_data \
  -t <target> \
  --profile <profile> \
  --params catalog=dev2_archive,schema=source_data_samples
```

| Parameter | Default | Description |
|---|---|---|
| `catalog` | `dev2_archive` | Target catalog for test data |
| `schema` | `source_data_samples` | Target schema for test data |

---

## 5. Scanner

Scans source schemas and populates `table_configs` with discovered tables, watermark columns, and archive paths.

### Scan all schemas

```bash
databricks bundle run caresource_scanner \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>
```

### Scan a single schema

```bash
databricks bundle run caresource_scanner \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>,schema_id=<schema_id>
```

### Force re-scan (overwrite existing table_configs)

```bash
databricks bundle run caresource_scanner \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>,schema_id=<schema_id>,force=true
```

| Parameter | Default | Required | Description |
|---|---|---|---|
| `config_table` | _(empty)_ | **Yes** | 3-level `global_settings` table name |
| `schema_id` | _(empty)_ | No | Limit scan to one schema template |
| `force` | `false` | No | `true` to overwrite existing table_configs |

---

## 6. Archive run

Generates archive parameters and runs archival for each table_config via a ForEach task.

### Dry run (default)

```bash
databricks bundle run caresource_archive_run \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>
```

### Live run

```bash
databricks bundle run caresource_archive_run \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>,dry_run=false
```

### Filter to specific tables

```bash
databricks bundle run caresource_archive_run \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>,dry_run=false,table_config_filter="source_table = 'claims'"
```

### Override source catalog/schema

```bash
databricks bundle run caresource_archive_run \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table>,dry_run=false,source_catalog=my_catalog,source_schema=my_schema
```

| Parameter | Default | Required | Description |
|---|---|---|---|
| `config_table` | _(empty)_ | **Yes** | 3-level `global_settings` table name |
| `dry_run` | `true` | No | `false` to actually write archives and delete source rows |
| `table_config_filter` | _(empty)_ | No | SQL WHERE clause to filter `table_configs` |
| `source_catalog` | _(empty)_ | No | Override source catalog (both must be set if either is) |
| `source_schema` | _(empty)_ | No | Override source schema |

---

## 7. Rehydrate

Restores archived data as views in a target schema. Creates per-year views (`{table}_year_{YYYY}`) and an optional unified view that unions them.

```bash
databricks bundle run caresource_rehydrate \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table> \
  --params archive_base_path=<path> \
  --params source_table=<table> \
  --params target_catalog=<catalog> \
  --params target_schema=<schema> \
  --params 'years="2020,2021"'
```

> **CLI quoting note:** The `years` value contains a comma, which conflicts with the CLI's comma-separated param parsing. Pass `years` as a separate `--params` flag with inner quotes: `--params 'years="2020,2021"'`. The notebook strips the quote artifacts.

### Custom unified view suffix

```bash
databricks bundle run caresource_rehydrate \
  -t <target> \
  --profile <profile> \
  --params config_table=<config_table> \
  --params archive_base_path=<path> \
  --params source_table=<table> \
  --params target_catalog=<catalog> \
  --params target_schema=<schema> \
  --params 'years="2020,2021"' \
  --params unified_view_suffix=_combined
```

This creates `claims_combined` instead of `claims_unified`.

### Include live source data in unified view

```bash
  --params include_live_data=true
```

When `true`, the unified view unions archived year views **plus** the live source table.

| Parameter | Default | Required | Description |
|---|---|---|---|
| `config_table` | _(empty)_ | **Yes** | 3-level `global_settings` table name |
| `archive_base_path` | _(empty)_ | **Yes** | Volume path to the archived Parquet files |
| `source_table` | _(empty)_ | **Yes** | Original source table name (`catalog.schema.table`) |
| `target_catalog` | _(empty)_ | **Yes** | Catalog to restore into |
| `target_schema` | _(empty)_ | **Yes** | Schema to restore into |
| `years` | _(empty)_ | **Yes** | Comma-separated archive years to restore |
| `include_live_data` | `false` | No | Include live source table in unified view |
| `unified_view_suffix` | `_unified` | No | Suffix for the unified view name (e.g. `_combined`, `_all`) |

---

## Quick-reference: dev-serverless

End-to-end example using `dev-serverless` target and `fe-sandbox-manocha` profile.

```bash
PROFILE=fe-sandbox-manocha
TARGET=dev-serverless
CONFIG=dev2_archive.metadata.global_settings
SCHEMA=dev2_archive__source_data_samples

# Deploy
databricks bundle deploy -t $TARGET --profile $PROFILE

# Setup config tables
databricks bundle run setup_config_tables -t $TARGET --profile $PROFILE

# Seed config
databricks bundle run seed_config -t $TARGET --profile $PROFILE

# Generate test data
databricks bundle run generate_test_data -t $TARGET --profile $PROFILE

# Scan
databricks bundle run caresource_scanner -t $TARGET --profile $PROFILE \
  --params config_table=$CONFIG,schema_id=$SCHEMA

# Archive — dry run
databricks bundle run caresource_archive_run -t $TARGET --profile $PROFILE \
  --params config_table=$CONFIG

# Archive — live
databricks bundle run caresource_archive_run -t $TARGET --profile $PROFILE \
  --params config_table=$CONFIG,dry_run=false
```
