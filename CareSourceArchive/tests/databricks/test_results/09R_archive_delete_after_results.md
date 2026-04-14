# 09 — Archive with Delete After Archive Results

## Run — 2026-04-10 15:44 CDT (retry after cleanup)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

### Before

- Cleared all 64 providers audit log entries
- Archive folders already removed (from prior failed attempt)
- Set `delete_after_archive = true`
- Baseline source counts: NULL=5, 2020=167, 2021=167, 2022=167, 2023=170, 2024=163, 2025=166

### Step 1 — Run Archive (live, scoped to providers): PASS

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~147 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/188951095818276 |

### Step 2 — Audit Log: PASS

Every year shows the full three-step progression. All `archive_mode = CREATE`.

| Year | Status Progression | Record Count |
|------|-------------------|-------------|
| 2020 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 167 |
| 2021 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 167 |
| 2022 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 167 |
| 2023 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 170 |
| 2024 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 163 |
| 2025 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 166 |

### Step 3 — Source Rows Deleted: PASS

| Year | Before | After |
|------|--------|-------|
| NULL | 5 | 5 |
| 2020 | 167 | **0** |
| 2021 | 167 | **0** |
| 2022 | 167 | **0** |
| 2023 | 170 | **0** |
| 2024 | 163 | **0** |
| 2025 | 166 | **0** |

All archived years deleted from source. Only NULL-date rows remain.

### Step 4 — Archive Has the Data: PASS

| Archive Folder | Count | Matches Baseline? | Result |
|---------------|-------|-------------------|--------|
| year_2020 | 167 | 167 = 167 | **PASS** |
| year_2021 | 167 | 167 = 167 | **PASS** |
| year_2022 | 167 | 167 = 167 | **PASS** |
| year_2023 | 170 | 170 = 170 | **PASS** |
| year_2024 | 163 | 163 = 163 | **PASS** |
| year_2025 | 166 | 166 = 166 | **PASS** |

### Cleanup

`delete_after_archive` reset to `false` (1 row updated).

### Overall Result: PASS

---

## Run — 2026-04-10 15:35 CDT (failed — see root cause below)

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

### Before — Environment State

- **Archive folders:** providers year_2020 through year_2025 existed on volume (from prior test runs)
- **Audit log:** Only DRY_RUN entries + old SKIPPED/STARTED entries from earlier today's runs
- **`delete_after_archive`:** Set to `true` (1 row updated)
- **Source counts (baseline):**

| Year | Count |
|------|-------|
| NULL | 5 |
| 2020 | 167 |
| 2021 | 167 |
| 2022 | 167 |
| 2023 | 170 |
| 2024 | 163 |
| 2025 | 166 |

### Step 1 — Run Archive (live, scoped to providers): FAIL

| Field | Value |
|-------|-------|
| Status | **FAIL** — INTERNAL_ERROR |
| Duration | ~84 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/593926816421442 |
| Error | `Task run_archive failed: Some iterations failed` |

### Step 2 — Audit Log: FAIL

The archiver attempted year 2020 twice (STARTED → FAILED both times). All other years were not attempted.

**Error:** `[PATH_NOT_FOUND] Path does not exist: /Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples/providers/year_2020. SQLSTATE: 42K03`

The archive folder was removed by the user via `dbutils.fs.rm` before the run, but the archiver's `_resolve_year_action` apparently determined that the folder exists (possibly a stale `SKIPPED`/`STARTED` audit entry from a prior run made it attempt an APPEND or RESUME_DELETE instead of CREATE). When it tried to read from the path, the Delta table was gone.

### Step 3 — Verify Source Rows: PASS (unchanged)

Source data intact — no rows were deleted since the archive never completed.

| Year | Before | After |
|------|--------|-------|
| NULL | 5 | 5 |
| 2020 | 167 | 167 |
| 2021 | 167 | 167 |
| 2022 | 167 | 167 |
| 2023 | 170 | 170 |
| 2024 | 163 | 163 |
| 2025 | 166 | 166 |

### Step 4 — Verify Archive: SKIP

Not applicable — job failed before creating any archives.

### Cleanup

`delete_after_archive` reset to `false` (1 row updated).

### Overall Result: FAIL

### What Happened

The providers archive folders were incorrectly removed via `dbutils.fs.rm` during test 08 cleanup (test 08 was only about claims — the providers folders should not have been touched). This left `ARCHIVED` audit entries (from the test 05 run at 18:24 UTC) with no corresponding folders on disk.

When the test 09 run started, `get_last_run_state` found `status = ARCHIVED` for each year. Combined with `delete_after_archive = true`, the archiver chose `RESUME_DELETE` — meaning it tried to verify the existing archive before deleting source rows. It attempted to read the Delta table at the archive path, but the folder was gone, causing `PATH_NOT_FOUND`.

**This is not a code bug.** The archiver correctly trusts that if the audit log says `ARCHIVED`, the folder exists. The failure was caused by an artificial inconsistency introduced during test cleanup (folders deleted, audit entries left). In a real-world scenario (switching `delete_after_archive` from `false` to `true`), the `RESUME_DELETE` path would work correctly because the archive folders would still be present.

### Next Steps

1. **Re-run:** Clear all providers audit log entries AND ensure the providers archive folder is removed (both must be done together). Then re-run test 09 from scratch.
2. **No code fix needed** — the archiver behavior is correct.

---

**Test Date:** 2026-04-07
**Test Time:** 13:39 – 13:44 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Before — Reset Providers Archive State

### 1. Removed existing archive folders

`databricks fs rm -r` on providers archive directory — success.

### 2. Cleared audit log

54 prior rows deleted from `archive_audit_log` for providers.

### 3. Enabled delete_after_archive

1 row updated in `table_configs`.

### 4. Baseline source counts

| Year | Count |
|------|-------|
| NULL | 5 |
| 2020 | 163 |
| 2021 | 168 |
| 2022 | 166 |
| 2023 | 167 |
| 2024 | 166 |
| 2025 | 165 |

Matches test 05 baseline — source data intact. **PASS**

---

## Step 1 — Run Archive (live, dry_run=false, scoped to providers)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~150 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/565644113633914 |

---

## Step 2 — Check Audit Log

Every year shows the three-step progression: STARTED → ARCHIVED → ARCHIVED_AND_DELETED. All `archive_mode = CREATE`. **PASS**

| Year | Status | Record Count | Mode | Result |
|------|--------|-------------|------|--------|
| 2020 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 163 | CREATE | **PASS** |
| 2021 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 168 | CREATE | **PASS** |
| 2022 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 166 | CREATE | **PASS** |
| 2023 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 167 | CREATE | **PASS** |
| 2024 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 166 | CREATE | **PASS** |
| 2025 | STARTED → ARCHIVED → ARCHIVED_AND_DELETED | 165 | CREATE | **PASS** |

---

## Step 3 — Verify Source Rows Deleted

| Year | Before | After |
|------|--------|-------|
| NULL | 5 | 5 |
| 2020 | 163 | **0** |
| 2021 | 168 | **0** |
| 2022 | 166 | **0** |
| 2023 | 167 | **0** |
| 2024 | 166 | **0** |
| 2025 | 165 | **0** |

All archived years deleted from source. Only NULL-date rows remain. **PASS**

---

## Step 4 — Verify Archive Has the Data

| Archive Folder | Count | Matches Baseline? | Result |
|---------------|-------|-------------------|--------|
| year_2020 | 163 | 163 = 163 | **PASS** |
| year_2021 | 168 | 168 = 168 | **PASS** |
| year_2022 | 166 | 166 = 166 | **PASS** |
| year_2023 | 167 | 167 = 167 | **PASS** |
| year_2024 | 166 | 166 = 166 | **PASS** |
| year_2025 | 165 | 165 = 165 | **PASS** |

Every archive folder count matches the original source count exactly. **PASS**

---

## Cleanup

`delete_after_archive` reset to `false` for providers (1 row updated).

To restore source data, re-run `generate_test_data` or use test 14 (rehydration).

---

## Overall Result: PASS

When `delete_after_archive = true`, the archiver correctly:
1. Creates the archive (CREATE mode) for each eligible year
2. Verifies archive integrity (count match)
3. Deletes the archived rows from the source table
4. Logs the full progression: STARTED → ARCHIVED → ARCHIVED_AND_DELETED
5. Rows with NULL dates (5 rows) are untouched in the source
