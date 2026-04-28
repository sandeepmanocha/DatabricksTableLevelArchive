# 31 — D14 Cross-Run Safety + Delete-Job Drift Detection

**Goal:** Prove two Phase 2 invariants in a single test, while minimising archiver re-runs:

1. **D14 cross-run safety (main archive job).** When a second archive run finds `ARCHIVED` rows from a prior run and no new data above the watermark, every year must resolve to `SKIP`. The main archive job **must not** auto-delete cross-run rows even with `delete_after_archive = true` — that's the job of the dedicated delete job.
2. **Delete-job drift detection.** When the archive row count for `(table, year)` does not match the live source count, the delete job writes a `VERIFY_FAILED / source_drift` audit row, refuses to delete, and the archive stays untouched. A second year with no drift in the same run succeeds normally.

**Covers:** D14 resolver rules E and G (the `is_archived_by_run` ownership gate), `delete_source_after_archive` drift check (`archive_row_count` vs source count → `log_verify_failed`), audit vocabulary for `ARCHIVED_AND_DELETED` (archive-job same-run vs delete-job cross-run), status distinction between the two paths.

**Depends on:** 01_setup_and_deploy (fresh test data). Uses `providers` in isolation. **Two archiver runs + two delete-job runs** — any fewer loses a case we want to exercise.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/31R_d14_cross_run_safety_and_drift_results.md` (below the H1 title). Do not overwrite previous runs.

## Execution Rules

> **DO NOT FIX CODE.** If a step fails or produces unexpected results, do **not** modify source code, notebooks, or SQL logic to make it pass. Instead:
>
> 1. **Record** the exact error, unexpected output, or deviation from expected behavior in the results file.
> 2. **Log** the issue with enough detail for a developer to reproduce (command run, actual vs expected output, full error messages).
> 3. **Continue** with remaining steps if possible (unless a failure makes subsequent steps meaningless).
> 4. **Summarize** at the end of the results file under a `## What Happened` section — plain-English description of everything that occurred.
> 5. **Recommend next steps** under a `## Next Steps` section — what the developer should investigate or fix, which test to re-run after the fix, and any manual actions needed.
> 6. Mark each step as **PASS**, **FAIL**, or **SKIP** (skipped due to prior failure) in the results.
> 7. Add a **TL;DR** (max 3 lines) right below the run header summarizing what happened and the outcome.
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers`. Check:
>    - **Source table (`providers`):** Must have data for all years (2020–2025). If rows were deleted by prior tests, re-run `generate_test_data` to restore.
>    - **Audit log (`providers` only):** Must be empty (Phase 1 clears it).
>    - **Archive volume (`providers` only):** No `year_*` folders should exist.
>    - **`table_configs` (`providers`):** `delete_after_archive` starts `false` (Phase 1 sets it explicitly). `archive_base_path` must be correct.
>    - **Bundle deploy:** Both `caresource_archive_run` and `caresource_delete_source_after_archive` must be deployed. Run `databricks bundle deploy -t dev --profile DEFAULT` if either is missing.
>    - **Other tables:** Do NOT touch `claims` or `members`.

---

## Phase 1 — Clean preconditions on `providers`

### 1a. Clear audit, remove archive folders, set `delete_after_archive = false`

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
```

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 31: baseline clean state'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 1b. Record baseline source counts and year-2022 max watermark

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
GROUP BY 1 ORDER BY 1
```

Record as `BASELINE_YYYY` for every year.

```sql
SELECT CAST(MAX(effective_date) AS DATE) AS max_wm_2022
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2022
```

Record as `MAX_WM_2022`.

> **Precondition.** The test targets years 2021 (clean, delete-job success) and 2022 (drift, delete-job refusal). Default retention (`retention_years = 2`) keeps both eligible in any year >= 2024. If your retention window excludes either year, pick two adjacent eligible years and substitute throughout.

---

## Phase 2 — Archive run #1: archive everything with `delete_after_archive = false`

### 2a. Run archive (providers only, live)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 2b. Verify audit — every eligible year `ARCHIVED`, no `ARCHIVED_AND_DELETED`

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
ORDER BY year, created_at
```

**Expect:** `STARTED` → `ARCHIVED` with `archive_mode = CREATE` per eligible year. No `ARCHIVED_AND_DELETED`. Record the `archive_run_id` as `RUN_1_ID`, and `record_count` for years 2021 and 2022 as `ARCHIVED_2021` and `ARCHIVED_2022`.

### 2c. Verify source untouched and archive matches source

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2021, 2022)
GROUP BY 1 ORDER BY 1
```

**Expect:** `cnt = BASELINE_2021` and `cnt = BASELINE_2022`.

```bash
databricks experimental aitools tools query \
  "SELECT '2021' AS yr, COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2021\`
   UNION ALL
   SELECT '2022', COUNT(*) FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2022\`" \
  --profile DEFAULT
```

**Expect:** `cnt = ARCHIVED_2021` and `cnt = ARCHIVED_2022` — both equal to their baselines.

---

## Phase 3 — Archive run #2: `delete_after_archive = true` with NO new source data (D14 cross-run safety)

### 3a. Flip `delete_after_archive = true`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 31 Phase 3: flip to exercise D14 cross-run safety'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

**Do not insert any new source rows.** The whole point of this phase is that the source is unchanged since Phase 2.

### 3b. Re-run archive (providers only, live)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 3c. Audit: every year SKIPped by this run; **zero** `ARCHIVED_AND_DELETED` rows owned by `RUN_2_ID`

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM sandeep_manocha.caresource_audit.archive_audit_log
    WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year, created_at
```

Call this run's id `RUN_2_ID`.

**Expect:**

- `RUN_2_ID != RUN_1_ID`.
- Every eligible year for this run is `SKIPPED` (or absent for builds that omit SKIP rows entirely). The D14 resolver rule E (`VALID + ARCHIVED + delete_after + same-run`) fails because `is_archived_by_run(RUN_2_ID) = false` — the prior `ARCHIVED` row belongs to `RUN_1_ID`. Rule G then falls through `_count_new_records = 0` → `SKIP`.
- **No row with `status = 'ARCHIVED_AND_DELETED'` and `archive_run_id = RUN_2_ID` exists for any year.** This is the critical invariant. A focused check:

```sql
SELECT year, COUNT(*) AS cnt
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND status = 'ARCHIVED_AND_DELETED'
  AND archive_run_id = (
    SELECT MAX(archive_run_id)
    FROM sandeep_manocha.caresource_audit.archive_audit_log
    WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
      AND status = 'SKIPPED'
  )
GROUP BY year
```

**Expect:** Empty result set. If any row comes back, the D14 cross-run safety guarantee has been violated — capture the full row dump in the results file and mark this phase as FAIL.

### 3d. Source untouched

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2021, 2022)
GROUP BY 1 ORDER BY 1
```

**Expect:** `cnt = BASELINE_2021` and `cnt = BASELINE_2022`. No row was deleted by `RUN_2_ID`.

### 3e. Archive folders still present on storage

```bash
databricks fs ls \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

**Expect:** All `year_*` folders still visible.

---

## Phase 4 — Induce drift on year 2022 (3 rows above watermark); leave 2021 clean

### 4a. Insert 3 drift rows for year 2022

```sql
INSERT INTO sandeep_manocha.source_data_samples.providers
  (provider_id, provider_name, specialty, npi, effective_date, state, is_active)
VALUES
  ('PRV-T31-01', 'Dr. T31-1', 'Cardiology', '9992000001', DATE_ADD(DATE'2022-12-20', 1), 'OH', true),
  ('PRV-T31-02', 'Dr. T31-2', 'Neurology',  '9992000002', DATE_ADD(DATE'2022-12-20', 2), 'KY', true),
  ('PRV-T31-03', 'Dr. T31-3', 'Pediatrics', '9992000003', DATE_ADD(DATE'2022-12-20', 3), 'IN', true)
```

> If `MAX_WM_2022 > 2022-12-20` for your seeded data, replace `DATE'2022-12-20'` with `DATE'<MAX_WM_2022>'` so the drift rows sit above the watermark.

### 4b. Confirm drift on year 2022, no drift on year 2021

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2021, 2022)
GROUP BY 1 ORDER BY 1
```

**Expect:** `yr = 2021, cnt = BASELINE_2021 = ARCHIVED_2021`. `yr = 2022, cnt = BASELINE_2022 + 3 = ARCHIVED_2022 + 3`.

---

## Phase 5 — Delete-job run #1: live, scoped to year 2021 (clean — should succeed)

### 5a. Run delete job live, scoped to year 2021 only

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'",years='"2021"'
```

### 5b. Audit: `ARCHIVED_AND_DELETED` with `archive_mode = DELETE`, `archive_delta_version IS NULL`

```sql
SELECT year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 200) AS message_excerpt,
       archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2021
  AND status = 'ARCHIVED_AND_DELETED'
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Latest row:

- `archive_mode = 'DELETE'`
- `archive_delta_version IS NULL`
- `record_count = ARCHIVED_2021 = BASELINE_2021`
- `message_excerpt` starts with `"Deleted <N> rows from source."` and references `RUN_1_ID` as the prior archive run.

### 5c. Source year 2021 emptied; year 2022 unchanged

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2021, 2022)
GROUP BY 1 ORDER BY 1
```

**Expect:** `yr = 2021, cnt = 0`. `yr = 2022, cnt = BASELINE_2022 + 3` (unchanged — year 2022 was not in this delete scope).

### 5d. Archive Delta for 2021 still intact

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2021\`" \
  --profile DEFAULT
```

**Expect:** `cnt = ARCHIVED_2021`. The archive is the durable copy.

---

## Phase 6 — Delete-job run #2: live, scoped to year 2022 (drift — should refuse)

### 6a. Run delete job live, scoped to year 2022 only

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'",years='"2022"'
```

### 6b. Audit: `VERIFY_FAILED / source_drift`

```sql
SELECT year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 300) AS message_excerpt,
       archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
  AND status = 'VERIFY_FAILED'
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Latest row:

- `status = 'VERIFY_FAILED'`
- `archive_mode IS NULL` (not a DELETE-mode row; the delete was refused)
- `record_count = ARCHIVED_2022` (the archive row count that was compared against source)
- `message_excerpt` contains the phrase `"source count"` or `"source drift"`, and both numbers: `ARCHIVED_2022` and `ARCHIVED_2022 + 3`. It should also reference `docs/runbooks/verify-failed.md`.

### 6c. Source for 2022 still has the drift rows — nothing was deleted

```sql
SELECT COUNT(*) AS source_2022_after_drift_refusal
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2022
```

**Expect:** `source_2022_after_drift_refusal = BASELINE_2022 + 3`. The delete was refused.

### 6d. Drift rows still present

```sql
SELECT COUNT(*) AS drift_rows_present
FROM sandeep_manocha.source_data_samples.providers
WHERE provider_id LIKE 'PRV-T31-%'
```

**Expect:** `drift_rows_present = 3`.

### 6e. Archive for 2022 untouched

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2022\`" \
  --profile DEFAULT
```

**Expect:** `cnt = ARCHIVED_2022` (unchanged). The drift check runs before any mutation.

---

## Phase 7 — Cleanup

### 7a. Clear audit rows for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
```

### 7b. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 7c. Reset `delete_after_archive`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 31'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 7d. Restore source data

Re-run `generate_test_data` to restore `providers`. Phase 5 emptied year 2021 and Phase 4 added three `PRV-T31-*` rows to year 2022, so the table will need rebuilding before the next test.
