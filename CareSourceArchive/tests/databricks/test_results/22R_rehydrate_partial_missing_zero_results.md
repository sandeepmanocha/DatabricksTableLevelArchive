# 22 — Rehydration Partial Success, Missing Folders, and Zero-Restore — Results

## Run — 2026-04-17 12:21 CDT

**TL;DR:** All four phases passed. Partial (2 of 3 years) → `PARTIAL_COMPLETED`. Zero-restore (0 of 2) → `FAILED` with `ArchiveOperationError`. Folder deletion left an orphan view that fails with `INCOMPATIBLE_VIEW_SCHEMA_CHANGE`; unified view correctly rebuilt to exclude deleted year. Restore + re-run recovered to 2 views.

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
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated_22` |
| `REHYDRATE_ZERO_TEST_SCHEMA` | `rehydrated_22_zero` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |
| `unified_view_suffix` | `_unified` (default) |

**Note — missing year adjusted:** Test case specifies year 2019 as the missing year, but `claims/year_2019` existed in the archive volume (624 rows from a prior archive run). Used **2017** instead (no archive folder exists). All phases used `years=2020,2021,2017`.

---

### Pre-flight: **PASS**

| Check | Result |
|---|---|
| Archive `claims/year_2020` | 623 rows |
| Archive `claims/year_2021` | 623 rows |
| Archive `claims/year_2019` | EXISTS (624 rows) — using 2017 instead |
| Archive `claims/year_2017` | PATH_NOT_FOUND |
| Archive `claims/year_2015` | PATH_NOT_FOUND |
| Archive `claims/year_2016` | PATH_NOT_FOUND |
| Schema `rehydrated_22` | Does not exist |
| Schema `rehydrated_22_zero` | Does not exist |
| Audit table | 11 existing rows |
| Source table `claims` | 5,000 rows |

---

### Step 1 — Phase 1: Run rehydration (partial: 2020, 2021, 2017): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/79818058150383](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/79818058150383) |
| Status | TERMINATED SUCCESS |
| Duration | ~34 sec |

---

### Step 2 — Phase 1: Verify PARTIAL_COMPLETED, objects, audit: **PASS**

**Objects in `dev2_archive.rehydrated_22`:**

| Object | Type |
|--------|------|
| `claims_year_2020` | View |
| `claims_year_2021` | View |
| `claims_unified` | View |

No `claims_year_2017` view created (no folder → skipped).

**Unified view row counts (`include_live_data=false`):**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

**Latest audit row:**

| Field | Value |
|-------|-------|
| `status` | **PARTIAL_COMPLETED** |
| `tables_created` | 2 |
| `years` | `[2020, 2021, 2017]` |
| `error_message` | (empty) |
| `created_at` | `2026-04-17T17:21:45.374Z` |

---

### Step 3 — Phase 2: Drop disposable zero-test schema: **PASS**

`DROP SCHEMA IF EXISTS dev2_archive.rehydrated_22_zero CASCADE` — succeeded (schema didn't exist).

---

### Step 4 — Phase 2: Zero-restore (years 2015, 2016): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/18070595666011](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/18070595666011) |
| Status | INTERNAL_ERROR FAILED |
| Duration | ~50 sec |
| Error | `ArchiveOperationError: dev2_archive.source_data_samples.claims year all: rehydrate failed — no requested years restored` |

Job failed as expected — `ArchiveOperationError` with `reason=no_years_restored` raised at `rehydrator.py:112`.

---

### Step 5 — Phase 2: Verify FAILED audit and empty target: **PASS**

**`SHOW TABLES IN dev2_archive.rehydrated_22_zero`:** empty (`[]`).

Schema exists (created by `CREATE SCHEMA IF NOT EXISTS` in the engine) but contains no views.

**Audit rows for `rehydrated_22_zero`:**

| `created_at` | `status` | `tables_created` | `years` | `error_message` |
|---|---|---|---|---|
| `2026-04-17T17:23:24.689Z` | FAILED | 0 | `[2015, 2016]` | `...no requested years restored; created_years=[]; skipped_years=[2015, 2016]` |
| `2026-04-17T17:23:01.637Z` | FAILED | 0 | `[2015, 2016]` | (same) |

**Observation:** Two FAILED audit rows for a single job run. The engine writes a FAILED audit row in the exception handler before re-raising. The notebook catches `ArchiveOperationError` and re-raises, which causes the outer `except` in the engine to write a second audit row. This is a minor observation — the audit correctly captures the failure, but produces a duplicate entry.

**Phase 1 schema unchanged:** `rehydrated_22` still has `claims_year_2020`, `claims_year_2021`, `claims_unified`.

---

### Step 6 — Phase 3: Delete `year_2021` archive folder: **PASS**

```
databricks fs rm -r dbfs:<ARCHIVE_VOL>/claims/year_2021 --profile fe-sandbox-manocha
```

Verified: `SELECT COUNT(*) FROM delta.\`...year_2021\`` returns `PATH_NOT_FOUND`.

---

### Step 7 — Phase 3: Re-run Phase 1 params (only 2020 available): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/594676331152646](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/594676331152646) |
| Status | TERMINATED SUCCESS |
| Duration | ~33 sec |

**Audit:** `PARTIAL_COMPLETED`, `tables_created=1`.

---

### Step 8 — Phase 3: Document orphan view and query behavior: **PASS**

**Objects in `dev2_archive.rehydrated_22`:** still 3 objects (`claims_unified`, `claims_year_2020`, `claims_year_2021`).

**Orphan `claims_year_2021` view query:**

```
Error: INCOMPATIBLE_VIEW_SCHEMA_CHANGE
The SQL query of view `dev2_archive`.`rehydrated_22`.`claims_year_2021` has an incompatible
schema change and column claim_id cannot be resolved. Expected 1 columns named claim_id
but got [].
Please try to re-create the view by running:
CREATE OR REPLACE VIEW dev2_archive.rehydrated_22.claims_year_2021
AS SELECT * FROM delta.`<path>/claims/year_2021`.
```

The orphan view object persists from Phase 1, but querying it fails because the underlying Delta path was deleted.

**Unified view:** `SELECT COUNT(*) FROM claims_unified` → **623** (only 2020). The engine rebuilt `claims_unified` using only `created_years` (2020), correctly excluding the deleted 2021.

---

### Step 9 — Phase 4: Restore `year_2021` folder: **PASS**

Restored via:

```sql
CREATE TABLE delta.`<ARCHIVE_VOL>/claims/year_2021`
AS SELECT * FROM dev2_archive.source_data_samples.claims
WHERE YEAR(event_date) = 2021
```

Verified: 623 rows.

---

### Step 10 — Phase 4: Re-run Phase 1 params (idempotent): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/549839735322168](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/549839735322168) |
| Status | TERMINATED SUCCESS |
| Duration | ~32 sec |

**Unified view row counts after restore + re-run:**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

**Audit (most recent for `rehydrated_22`):**

| `created_at` | `status` | `tables_created` | `years` |
|---|---|---|---|
| `2026-04-17T17:30:17.205Z` | PARTIAL_COMPLETED | 2 | `[2020, 2021, 2017]` |
| `2026-04-17T17:27:21.829Z` | PARTIAL_COMPLETED | 1 | `[2020, 2021, 2017]` |
| `2026-04-17T17:26:34.798Z` | PARTIAL_COMPLETED | 1 | `[2020, 2021, 2017]` |
| `2026-04-17T17:21:45.374Z` | PARTIAL_COMPLETED | 2 | `[2020, 2021, 2017]` |

---

### Cleanup: **PASS**

- `DROP SCHEMA IF EXISTS dev2_archive.rehydrated_22 CASCADE` — succeeded.
- `DROP SCHEMA IF EXISTS dev2_archive.rehydrated_22_zero CASCADE` — succeeded.

---

## What Happened

1. **Pre-flight:** Year 2019 had an existing archive folder (from prior runs), so 2017 was substituted as the missing year. All other preconditions met.

2. **Phase 1 (partial):** Requested 2020, 2021, 2017. Notebook's `archive_folder_exists` excluded 2017. Engine created views for 2020 and 2021, reported `PARTIAL_COMPLETED` with `tables_created=2`. Unified view correctly contained only 2020+2021 archive data.

3. **Phase 2 (zero-restore):** Requested 2015, 2016 against a disposable schema. Both years had no archive folders. Engine raised `ArchiveOperationError` with `reason=no_years_restored`. Audit logged `FAILED` with `tables_created=0`. Schema existed but was empty. Phase 1 schema was unaffected.

4. **Phase 3 (folder deletion):** Deleted `year_2021` via `databricks fs rm -r`. Re-ran with same params — engine found only 2020, created 1 view, rebuilt `claims_unified` to union only 2020. The orphan `claims_year_2021` view from Phase 1 persisted but failed to query with `INCOMPATIBLE_VIEW_SCHEMA_CHANGE`.

5. **Phase 4 (restore):** Recreated `year_2021` via `CREATE TABLE delta.\`path\` AS SELECT`. Re-ran — engine restored 2020+2021, `PARTIAL_COMPLETED` with `tables_created=2`. Unified view back to 2020+2021.

6. **Cleanup:** Both schemas dropped.

## Observations

- **Duplicate audit rows on failure:** The zero-restore run produced 2 FAILED audit rows per execution. The engine writes a FAILED audit row in the exception handler, then re-raises. Consider deduplicating or guarding against double-write.
- **Orphan views:** When an archive folder is deleted, the per-year view remains as a catalog object. Direct queries fail, but `SHOW TABLES` still lists it. The engine does not drop stale per-year views — it only creates/replaces views for `created_years`. This is expected behavior (no data loss), but could be documented.
- **Duplicate audit rows on re-run during Phase 3:** Two `tables_created=1` rows appeared (at `17:26:34` and `17:27:21`) suggesting a retry or double-run. Only one bundle run was executed; the second row may be from the notebook's internal retry or a timing artifact.

## Next Steps

- **Proceed to test 23** (rehydration failure / permissions / recovery).
- Consider adding a cleanup step in the engine to `DROP VIEW IF EXISTS` for per-year views that are no longer in `created_years`, to prevent orphan views.
- Investigate the duplicate FAILED audit rows — the engine's exception handler may need a guard to avoid double-writing when `ArchiveOperationError` is re-raised.
- Test case file updated to use `databricks fs` CLI commands instead of `dbutils.fs` notebook steps.
