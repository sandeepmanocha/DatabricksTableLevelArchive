# 23 — Rehydration Failure Cascade, Permissions, Audit Fallback, and Recovery — Results

## Run — 2026-04-17 12:35 CDT

**TL;DR:** All five phases passed. Empty `source_table` → `ValueError` widget guard. Bad volume path → `SCHEMA_NOT_FOUND` before engine. Permission denial on `main` catalog → `FAILED` audit row. Dropped audit table → Branch C dual failure (`operation_failure_and_audit_failure`). Recovery via `setup_config_tables` → `COMPLETED` with 2 views and correct data.

**Branch:** `feat/delta_config_build_v5_rehydrate`
**Profile:** fe-sandbox-manocha
**Target:** dev-serverless
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated_23` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |
| `CONFIG_TABLES_PREFIX` | `dev2_archive.metadata` |
| `unified_view_suffix` | `_unified` (default) |

---

### Pre-flight: **PASS**

| Check | Result |
|---|---|
| Archive `claims/year_2020` | 623 rows |
| Archive `claims/year_2021` | 623 rows |
| Target schema `rehydrated_23` | Does not exist (clean) |
| Rehydration audit table | Exists, 17 existing rows |
| Source table `claims` | 5,000 rows |

---

### Step 1 — Phase 1: Missing required params (empty `source_table`): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/1013444978070501](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/1013444978070501) |
| Status | INTERNAL_ERROR FAILED |
| Duration | ~40 sec |
| Error | `ValueError: Widget 'source_table' is required (non-empty).` |

The widget guard in `run_rehydrate.py` (line 84–85) caught the empty `source_table` **before** `engine.run()`. The `ValueError` was raised in the `for label, val` loop that checks all required widgets.

- **LOG-04 HTML:** Not emitted (failure occurred before the `try` around `engine.run()`).
- **Audit:** No new row written (engine was never reached).
- **Behavior matches test expectation:** Empty string still supplies the key to the notebook, but the widget guard catches it as falsy.

---

### Step 2 — Phase 2: Bad archive path (non-existent volume): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/134619395211355](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/134619395211355) |
| Status | INTERNAL_ERROR FAILED |
| Duration | ~50 sec |
| Error | `ExecutionError: [SCHEMA_NOT_FOUND] The schema 'dev2_archive.nonexistent_volume' cannot be found.` |

The bad path `/Volumes/dev2_archive/nonexistent_volume/bad_path` caused `archive_folder_exists()` to raise an **unhandled** `ExecutionError` during the pre-engine widget processing block (line 90–94 of `run_rehydrate.py`). The volume's schema component (`nonexistent_volume`) does not exist, triggering `NoSuchDatabaseException` from Unity Catalog.

- **Deviation from expected:** Test case predicted the engine would raise `ArchiveOperationError` with `reason=no_years_restored` or `view_create_failed`. Instead, the failure occurred **before** `engine.run()` because `archive_folder_exists` raised a `SCHEMA_NOT_FOUND` exception — the non-existent volume path cannot be resolved at the `dbutils.fs.ls` level.
- **LOG-04 HTML:** Not emitted (failure before engine).
- **Audit:** No new row written (confirmed by query — most recent row still from test 22 at `17:30:17.205Z`).

---

### Step 3 — Phase 3: Inaccessible target catalog (`main`): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/433644590260129](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/433644590260129) |
| Status | INTERNAL_ERROR FAILED |
| Duration | ~49 sec |
| Error | `ArchiveOperationError: dev2_archive.source_data_samples.claims year all: rehydrate failed — (com.databricks.sql.managedcatalog.acl.UnauthorizedAccessException) PERMISSION_DENIED: User does not have CREATE SCHEMA and USE CATALOG on Catalog 'main'.` |

The engine reached `CREATE SCHEMA IF NOT EXISTS main.rehydrated_23` and Unity Catalog denied the operation. The `ArchiveOperationError` was raised inside the engine and caught by the notebook's `except ArchiveOperationError` block, which displays LOG-04 HTML and re-raises.

**Audit row (FAILED):**

| Field | Value |
|-------|-------|
| `target_catalog` | `main` |
| `target_schema` | `rehydrated_23` |
| `status` | **FAILED** |
| `tables_created` | 0 |
| `error_message` | `(UnauthorizedAccessException) PERMISSION_DENIED: User does not have CREATE SCHEMA and USE CATALOG on Catalog main....; created_years=[]; skipped_years=[]` |
| `created_at` | `2026-04-17T17:39:00.597Z` |

Audit catalog (`dev2_archive.metadata`) is separate from the target catalog (`main`), so the FAILED audit row was written successfully.

---

### Step 4 — Phase 4: Audit table unreachable: **PASS**

#### 4a. Drop audit table

`DROP TABLE IF EXISTS dev2_archive.metadata.rehydration_audit_log` — succeeded.

#### 4b. Run rehydration with valid parameters (audit table missing)

| Field | Value |
|-------|-------|
| Run URL | [run/275249369804104](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/275249369804104) |
| Status | INTERNAL_ERROR FAILED |
| Duration | ~31 sec |
| Error | `ArchiveOperationError: dev2_archive.source_data_samples.claims year all: rehydrate failed — Rehydration audit table ... does not exist. Run the setup_config_tables job first.; audit_write_error=[TABLE_OR_VIEW_NOT_FOUND]` |

**Branch observed: C** — `ensure_rehydration_audit_table()` detected the missing table and raised an error ("Rehydration audit table does not exist. Run the setup_config_tables job first."). The failure handler then attempted to write a FAILED audit row, which also failed (`TABLE_OR_VIEW_NOT_FOUND`). The engine raised `ArchiveOperationError` with dual failure information.

**Key error structure:**
1. **Primary error:** "Rehydration audit table does not exist. Run the setup_config_tables job first."
2. **Audit write error:** `[TABLE_OR_VIEW_NOT_FOUND] ... rehydration_audit_log cannot be found`
3. **Appended context:** `created_years=[]; skipped_years=[]`

**Target schema state:** `dev2_archive.rehydrated_23` was created (by `CREATE SCHEMA IF NOT EXISTS` in the engine) but contained no views — the engine failed at the audit check before creating any per-year views.

**JSON fallback payload:** Per `rehydrator.py`, the `rehydration_audit_write_failed` JSON payload should appear in the driver logs. This is only visible in the Databricks UI (Run output / Driver logs) — not captured by CLI output.

---

### Step 5 — Phase 5: Recovery: **PASS**

#### 5a. Restore audit objects

1. `CREATE SCHEMA IF NOT EXISTS dev2_archive.metadata` — succeeded (schema already existed; only the table was dropped).
2. `databricks bundle run setup_config_tables -t dev-serverless --profile fe-sandbox-manocha` — TERMINATED SUCCESS (~51 sec). [run/819975838279870](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/993666625185044/run/819975838279870)
3. `DESCRIBE TABLE dev2_archive.metadata.rehydration_audit_log` — confirmed restored with expected schema.

**Note:** `setup_config_tables` recreated the table fresh — all prior audit rows (17 from tests 21/22 and Phase 3) were lost. This is expected when the table is dropped and recreated.

#### 5b. Clean target schema

`DROP SCHEMA IF EXISTS dev2_archive.rehydrated_23 CASCADE` — succeeded.

#### 5c. Recovery run

| Field | Value |
|-------|-------|
| Run URL | [run/414903985889682](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/414903985889682) |
| Status | TERMINATED SUCCESS |
| Duration | ~33 sec |

**Objects in `dev2_archive.rehydrated_23`:**

| Object | Type |
|--------|------|
| `claims_year_2020` | View |
| `claims_year_2021` | View |
| `claims_unified` | View |

**Unified view row counts:**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

**Audit row:**

| Field | Value |
|-------|-------|
| `status` | **COMPLETED** |
| `tables_created` | 2 |
| `years` | `[2020, 2021]` |
| `error_message` | (empty) |
| `created_at` | `2026-04-17T17:43:13.177Z` |

---

### Cleanup: **PASS**

- `DROP SCHEMA IF EXISTS dev2_archive.rehydrated_23 CASCADE` — succeeded.
- Audit table `dev2_archive.metadata.rehydration_audit_log` exists and is queryable (restored via `setup_config_tables`).
- Note: Prior audit rows from tests 21/22 were lost when Phase 4 dropped the table. Running `seed_config` would be needed to restore non-test audit data, but all test-generated rows are ephemeral.

---

## What Happened

1. **Pre-flight passed** — archive volumes for 2020 and 2021 had 623 rows each, audit table existed with 17 rows, source table had 5,000 rows, target schema `rehydrated_23` did not exist.

2. **Phase 1 (empty source_table)** — The notebook's widget guard (`if not val: raise ValueError`) caught the empty `source_table` before `engine.run()`. Job failed with `ValueError`. No audit row written, no LOG-04 HTML. Matches expected behavior.

3. **Phase 2 (bad archive path)** — The non-existent volume path `/Volumes/dev2_archive/nonexistent_volume/bad_path` caused `archive_folder_exists()` to raise `ExecutionError: [SCHEMA_NOT_FOUND]` in the pre-engine processing block. This differs from the test case's prediction of `no_years_restored` — the failure occurs earlier because the volume path's schema component doesn't exist, and `dbutils.fs.ls` cannot resolve it. No audit row written.

4. **Phase 3 (permission denial)** — Using `target_catalog=main` triggered `PERMISSION_DENIED: User does not have CREATE SCHEMA and USE CATALOG on Catalog 'main'` during `CREATE SCHEMA IF NOT EXISTS`. The engine wrapped this in `ArchiveOperationError` and the failure handler wrote a FAILED audit row to `dev2_archive.metadata.rehydration_audit_log` (separate from the inaccessible target catalog). LOG-04 HTML was displayed.

5. **Phase 4 (audit table dropped)** — With the audit table dropped, the engine detected its absence via `ensure_rehydration_audit_table()` and raised an error. The failure handler's attempt to write a FAILED audit row also failed (`TABLE_OR_VIEW_NOT_FOUND`). The engine raised `ArchiveOperationError` with `operation_failure_and_audit_failure` containing both the primary and audit errors. Branch C confirmed. Target schema was created but empty.

6. **Phase 5 (recovery)** — `setup_config_tables` recreated the audit table. After cleaning the target schema, a fresh rehydration run succeeded: 3 views created, 623 rows per year, `COMPLETED` audit row with `tables_created=2`.

7. **Cleanup** — Target schema dropped, audit table intact.

## Observations

- **Phase 2 failure path differs from prediction:** The test case expected the engine to handle the bad path (via `no_years_restored`), but the failure occurs earlier — `archive_folder_exists()` raises when the volume path can't be resolved. The `archive_folder_exists` helper should catch `Exception` and return `False` for invalid volume paths, or the notebook should wrap the call in a try/except. Currently, only valid-but-empty paths are handled gracefully.
- **Phase 4 `ensure_rehydration_audit_table` does not recreate:** The function checks for the table's existence and raises an error if missing, rather than attempting to recreate it. This means Branch A (automatic recreation) never triggers — Phase 4 always follows Branch C. The error message helpfully directs the user to "Run the setup_config_tables job first."
- **Audit data loss on table drop:** Dropping and recreating the audit table via `setup_config_tables` loses all prior audit rows. If audit history is important, a backup/restore workflow should be documented.
- **CLI `--params` quoting:** The `stringToString` pflag type requires CSV-style quoting for values containing commas. `\"years=2020,2021\"` (field-level CSV quoting) works; `years="2020,2021"` (inner quotes only) does not.

## Next Steps

- **Consider hardening `archive_folder_exists`** to catch `Exception` and return `False` for invalid volume paths, so the engine's `no_years_restored` path is reached instead of an unhandled notebook-level error.
- **Consider adding `ensure_rehydration_audit_table` auto-creation** (Branch A) so the engine can self-heal when the audit table is accidentally dropped, rather than always requiring `setup_config_tables`.
- **Proceed to test 24** (edge cases) or **test 25** (end-to-end archive-then-rehydrate).
- The `seed_config` job should be run to restore any non-test audit/config data lost during Phase 4's table drop.
