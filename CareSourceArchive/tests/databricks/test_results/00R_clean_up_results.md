# 00 — Clean Up Results

## Run — 2026-04-13 12:01 CDT

**TL;DR:** Full cleanup completed. 7 audit tables dropped, rehydrated schema reset, archive folder removed (~44s). Source data preserved (claims=5,008, members=3,000, providers=5).

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT

### Before State

| Table | Rows |
|-------|------|
| global_settings | 1 |
| schema_templates | 1 |
| table_configs | 14 |
| table_configs_staging | 28 |
| archive_audit_log | 49 |
| scanner_log | 28 |
| rehydration_audit_log | 0 |

Archive volume folders: caresource_data_samples

Rehydrated schema: empty (no tables)

### Step 1 — Drop audit tables: **PASS**

All 7 tables dropped successfully:
- `global_settings` ✓
- `schema_templates` ✓
- `table_configs` ✓
- `table_configs_staging` ✓
- `archive_audit_log` ✓
- `scanner_log` ✓
- `rehydration_audit_log` ✓

### Step 2 — Drop + recreate rehydrated schema: **PASS**

- `DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE` ✓
- `CREATE SCHEMA IF NOT EXISTS sandeep_manocha.caresource_rehydrated` ✓

### Step 3 — Remove archive files from volume: **PASS**

- `databricks fs rm ... --recursive` completed (~44s)
- `caresource_data_samples` folder removed ✓

### Step 4 — Verify clean state: **PASS**

| Check | Expected | Actual |
|-------|----------|--------|
| `SHOW TABLES IN caresource_audit` | Empty | Empty ✓ |
| Volume `caresource_data_samples/` folder | Gone | Gone ✓ |
| Source data claims | 5,008 | 5,008 ✓ |
| Source data members | 3,000 | 3,000 ✓ |
| Source data providers | 5 | 5 ✓ |

### Preserved

- Catalog: `sandeep_manocha`
- Schemas: `caresource_audit`, `caresource_data_samples`, `caresource_archive`, `caresource_rehydrated`
- Volume: `caresource_archive_vol` (empty)
- Source tables: claims, members, providers

## What Happened

Environment fully reset to clean state. All 7 config/audit tables dropped, archive files removed from volume, rehydrated schema recreated empty. Source data tables untouched.

## Next Steps

- Run 01T (setup and deploy) to recreate tables and seed config

---

## Run — 2026-04-12 17:40 CDT

**TL;DR:** Full cleanup completed. 7 tables dropped, rehydrated schema reset, 6 archive folders removed. Source data preserved (claims=5,008, members=3,000, providers=5).

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT

### Before State

| Table | Rows |
|-------|------|
| global_settings | 1 |
| schema_templates | 1 |
| table_configs | 14 |
| table_configs_staging | 56 |
| archive_audit_log | 283 |
| scanner_log | 56 |
| rehydration_audit_log | 0 |

Archive volume folders: bronze_table_lineage, claims, gold_daily_access_trends, members, providers, silver_query_table_access

Rehydrated schema: empty (no tables)

### Step 1 — Drop audit tables: **PASS**

All 7 tables dropped successfully:
- `global_settings` ✓
- `schema_templates` ✓
- `table_configs` ✓
- `table_configs_staging` ✓
- `archive_audit_log` ✓
- `scanner_log` ✓
- `rehydration_audit_log` ✓

### Step 2 — Drop + recreate rehydrated schema: **PASS**

- `DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE` ✓
- `CREATE SCHEMA IF NOT EXISTS sandeep_manocha.caresource_rehydrated` ✓

### Step 3 — Remove archive files from volume: **PASS**

- `databricks fs rm ... --recursive` completed (~110s for 6 folders)
- All archive Delta folders removed ✓

### Step 4 — Verify clean state: **PASS**

| Check | Expected | Actual |
|-------|----------|--------|
| `SHOW TABLES IN caresource_audit` | Empty | Empty ✓ |
| Volume `caresource_data_samples/` folder | Gone | Gone ✓ |
| Source data claims | 5,008 | 5,008 ✓ |
| Source data members | 3,000 | 3,000 ✓ |
| Source data providers | 5 | 5 ✓ |

### Preserved

- Catalog: `sandeep_manocha`
- Schemas: `caresource_audit`, `caresource_data_samples`, `caresource_archive`, `caresource_rehydrated`
- Volume: `caresource_archive_vol` (empty)
- Source tables: claims, members, providers + all bronze/silver/gold tables

## What Happened

Environment fully reset to clean state. All config/audit tables dropped, archive files removed, rehydrated schema recreated empty. Source data tables untouched. Ready for `01_setup_and_deploy` to rebuild from scratch.

## Next Steps

- Run 01T (setup and deploy) to recreate tables and seed config
