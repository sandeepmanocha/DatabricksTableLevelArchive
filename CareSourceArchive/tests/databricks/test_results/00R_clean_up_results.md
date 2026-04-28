# 00 — Clean Up Results

## Run — 2026-04-27 21:55 CDT

**TL;DR:** Full cleanup on `dev2_archive.metadata` (the actual `dev-serverless` workspace under test). 7 audit tables dropped, `caresource_rehydrated` schema dropped + recreated, archive volume `source_data_samples` folder removed. Source tables preserved. All steps PASS.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`)
**Profile / Target:** `fe-sandbox-manocha` / `dev-serverless` (substituted from the test case's `--profile DEFAULT` / `-t dev` to match the actual bundle deployment).
**Audit schema under test:** `dev2_archive.metadata`
**Source schema under test:** `dev2_archive.source_data_samples`
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`

### Path substitutions

The test case hard-codes `sandeep_manocha.caresource_audit` and `--profile DEFAULT`. The actual workspace under test (per `databricks.yml`'s `dev-serverless` target) is the `fe-sandbox-manocha` profile with config catalog `dev2_archive` + schema `metadata`. Same substitutions as the 2026-04-27 20:30 08R integration run.

### Steps

| # | Step | Result |
|---|------|--------|
| 1 | DROP TABLE IF EXISTS for 7 audit tables in `dev2_archive.metadata` (global_settings, schema_templates, table_configs, table_configs_staging, archive_audit_log, scanner_log, rehydration_audit_log) | PASS — all 7 returned `Query executed successfully (no results)` |
| 2 | DROP SCHEMA IF EXISTS `dev2_archive.caresource_rehydrated` CASCADE; then CREATE SCHEMA IF NOT EXISTS | PASS |
| 3 | `databricks fs rm dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples --recursive` | PASS — folder removed (~57s) |
| 4a | `SHOW TABLES IN dev2_archive.metadata` | PASS — `[]` |
| 4b | `databricks fs ls dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/` | PASS — empty listing (no `source_data_samples` directory) |

### Errata — earlier 21:50 CDT entry below

An earlier attempt this session ran `--profile DEFAULT` against the literal `sandeep_manocha.caresource_audit` paths. That profile resolves to `e2-demo-field-eng`, not the `fe-sandbox-manocha` workspace under test. The DROP IF EXISTS calls succeeded but no-op'd because those tables don't exist on `e2-demo-field-eng`. This `21:55` re-run is the authoritative cleanup; the `21:50` entry is kept below for transparency. Anyone re-reading 00T should treat the 21:50 row as a misdirected attempt and the 21:55 row as the actual cleanup.

### Next Steps

Proceed to 01T using `--profile fe-sandbox-manocha -t dev-serverless`.

---

## Run — 2026-04-27 21:50 CDT (misdirected — see 21:55 errata)

**TL;DR:** Cleanup ran against `sandeep_manocha.caresource_audit` on profile `DEFAULT`, but the bundle under test deploys to `dev2_archive.metadata` on profile `fe-sandbox-manocha`. Substitution miss; no-op cleanup. Re-run captured at 21:55 above.

**Branch:** `feat/delta_config_build_v12_archive_refactor` (commit `a089564`)
**Profile:** `DEFAULT` (literal — wrong profile for this bundle)

### Steps

| # | Step | Result |
|---|------|--------|
| 1 | DROP TABLE IF EXISTS for 7 audit tables (literal `sandeep_manocha.caresource_audit`) | PASS — but no-op (target schema does not exist on `e2-demo-field-eng`) |
| 2 | DROP SCHEMA IF EXISTS `sandeep_manocha.caresource_rehydrated` CASCADE; CREATE SCHEMA IF NOT EXISTS | PASS — but on wrong workspace |
| 3 | `databricks fs rm dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples --recursive` | Returned `Error: file does not exist: ...source_data_samples` — wrong volume root |
| 4a | `SHOW TABLES IN sandeep_manocha.caresource_audit` | `[]` — but on wrong workspace |
| 4b | `databricks fs ls dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/` | Showed `caresource_data_samples`. Wrong volume — the actual workspace's volume is `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/`. |

---

## Run — 2026-04-27 08:08 CDT

**TL;DR:** Full cleanup on `dev2_archive.metadata` completed cleanly. 7 audit tables dropped, `caresource_rehydrated` schema dropped + recreated, archive volume `source_data_samples` folder removed (~42s). Source tables preserved (claims=5000, members=3000, providers=1000). All steps PASS.

**Branch:** `feat/delta_config_build_v10_test_cases` (commit `74f10cb`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target under test:** `dev-serverless` → config `dev2_archive.metadata`, source `dev2_archive.source_data_samples`
**Note on test case:** `00T_clean_up.md` hard-codes `sandeep_manocha.caresource_audit` paths and `--profile DEFAULT`. Substituted with the actual `dev-serverless` workspace values per the bundle under test (same substitutions as the 2026-04-24 run). Used `/tmp/run_sql.py` helper instead of the deprecated `databricks experimental aitools tools query`.

### Before State

| Object | Count |
|---|---|
| `dev2_archive.metadata.archive_audit_log` rows | 122 |
| `dev2_archive.metadata.table_configs` rows | 3 |
| `dev2_archive.metadata.schema_templates` rows | 1 |
| `dev2_archive.metadata.global_settings` rows | 1 |
| `dev2_archive.metadata.scanner_log` rows | 6 |
| `dev2_archive.metadata.rehydration_audit_log` rows | 0 |
| `dev2_archive.source_data_samples.claims` rows | 5000 |
| `dev2_archive.source_data_samples.members` rows | 3000 |
| `dev2_archive.source_data_samples.providers` rows | 1000 |
| Archive volume `source_data_samples/claims/year_*` | 8 folders (2018–2025) |
| Archive volume `source_data_samples/members/year_*` | 7 folders (2019–2025) |
| Archive volume `source_data_samples/providers/year_*` | 0 folders (already cleaned during prior 30T run) |

### Step 1 — Drop 7 audit tables — **PASS**

Dropped `global_settings`, `schema_templates`, `table_configs`, `table_configs_staging`, `archive_audit_log`, `scanner_log`, `rehydration_audit_log` from `dev2_archive.metadata`. All `DROP TABLE IF EXISTS` returned `STATE: SUCCEEDED`.

### Step 2 — Drop + recreate `caresource_rehydrated` schema — **PASS**

`DROP SCHEMA IF EXISTS dev2_archive.caresource_rehydrated CASCADE` followed by `CREATE SCHEMA IF NOT EXISTS dev2_archive.caresource_rehydrated`. Both `STATE: SUCCEEDED`. Post-state: 0 tables in schema.

### Step 3 — Remove archive volume contents — **PASS**

`databricks fs rm dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples --recursive`. Completed in ~42s. Removed all `claims/year_*` (8) and `members/year_*` (7) folders.

### Step 4 — Verify clean state — **PASS**

| Check | Expected | Actual |
|---|---|---|
| `SHOW TABLES IN dev2_archive.metadata` | empty | 0 rows |
| `databricks fs ls dbfs:/Volumes/.../sample_data_archive_ext_vol` | no `source_data_samples/` directory | empty listing |
| `SHOW TABLES IN dev2_archive.caresource_rehydrated` | empty | 0 rows |

### What Happened

Routine cleanup before re-running the test suite. User flagged "lot of bad data" accumulated from 34T–37T tests; this wipe restores a known-clean state. Source data tables (`claims`, `members`, `providers`) were intentionally preserved as the test design specifies. The `notebooks/seed_config.py` 5-pattern modification (adds `start_date`, `effective_date`) is left in place per user direction — they authored that change and want it kept.

### Next Steps

Proceed to `01T_setup_and_deploy` to redeploy the bundle, recreate config tables, seed dev config (5-pattern version), and regenerate test data.

---

## Run — 2026-04-24 09:15 CDT

**TL;DR:** Full cleanup on `dev2_archive.metadata` completed cleanly. 7 audit tables dropped, archive volume folder removed (~24s). Source tables preserved (claims=5000, members=3000, providers=1000). Catalog mismatch: test doc names `sandeep_manocha.caresource_audit`, we used the actual `dev-serverless` target location `dev2_archive.metadata`.

**Branch:** `feat/delta_config_build_v9_del_data_phase2` (commit `d838774`)
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle target under test:** `dev-serverless` → config `dev2_archive.metadata`, source `dev2_archive.source_data_samples`
**Note on test case:** `00T_clean_up.md` hard-codes `sandeep_manocha.caresource_audit` and `--profile DEFAULT`. Substituted with the actual `dev-serverless` target values per the bundle under test. Step 2 (drop `caresource_rehydrated` schema) was N/A — that schema belongs to the old `sandeep_manocha` catalog and does not exist under `dev2_archive`.

### Before State

| Table | Row count |
|---|---|
| `dev2_archive.metadata.global_settings` | 1 |
| `dev2_archive.metadata.schema_templates` | 1 |
| `dev2_archive.metadata.table_configs` | 3 |
| `dev2_archive.metadata.table_configs_staging` | 6 |
| `dev2_archive.metadata.archive_audit_log` | 29 |
| `dev2_archive.metadata.scanner_log` | 6 |
| `dev2_archive.metadata.rehydration_audit_log` | 2 |

Archive volume `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/` contained `source_data_samples/` folder.
Source sample tables: claims=5000, members=3000, providers=1000.

### Step 1 — Drop 7 audit tables: **PASS**

All seven `DROP TABLE IF EXISTS` statements returned "Query executed successfully (no results)" in ~2s each:

- `global_settings` ✓
- `schema_templates` ✓
- `table_configs` ✓
- `table_configs_staging` ✓
- `archive_audit_log` ✓
- `scanner_log` ✓
- `rehydration_audit_log` ✓

### Step 2 — Drop + recreate rehydrated schema: **N/A (SKIP)**

Test case names `sandeep_manocha.caresource_rehydrated`. This schema does not exist under the `dev2_archive` catalog used by the `dev-serverless` target, and the tests being run (00–03) do not exercise rehydration. Skipped without impact.

### Step 3 — Remove archive files from volume: **PASS**

```
databricks fs rm dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples --recursive --profile fe-sandbox-manocha
```

Completed in ~24s. `source_data_samples/` folder removed.

### Step 4 — Verify clean state: **PASS**

| Check | Expected | Actual |
|---|---|---|
| `SHOW TABLES IN dev2_archive.metadata` | Empty | `[]` ✓ |
| `databricks fs ls /Volumes/.../sample_data_archive_ext_vol/` | No `source_data_samples/` | Empty listing ✓ |
| Source `claims` | 5000 | 5000 ✓ |
| Source `members` | 3000 | 3000 ✓ |
| Source `providers` | 1000 | 1000 ✓ |

### Preserved

- Catalog: `dev2_archive`
- Schemas: `metadata`, `source_data_samples`, `source_data_samples_archive`
- Volume: `sample_data_archive_ext_vol` (empty)
- Source tables: `claims`, `members`, `providers`

## What Happened

Ran 00 cleanup against the `dev-serverless` target's actual config location (`dev2_archive.metadata`) rather than the stale `sandeep_manocha.caresource_audit` path written in the test doc. All 7 audit tables dropped and the archive volume folder was wiped cleanly in under 30 seconds of wall time. Source sample tables left untouched so tests 02/03 have data to scan without re-running `generate_test_data`.

## Next Steps

- Update `tests/databricks/test_cases/00T_clean_up.md` to match the parameterization style used in `02T_scanner_first_run.md` (placeholders + "ask the runner" block). Currently 00 and 03 hard-code `sandeep_manocha` while 01/02 use placeholders — inconsistent.
- Proceed to `01T_setup_and_deploy` (already run in this session — see `01R_setup_and_deploy_results.md`).

---

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
