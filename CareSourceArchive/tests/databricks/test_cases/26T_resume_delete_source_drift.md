# 26 — RESUME_DELETE Source Drift (VERIFY_FAILED)

**Goal:** When `delete_after_archive=true` and the source table gains rows (or changes row counts) between the ARCHIVE phase and the RESUME_DELETE phase, the archiver must detect the drift, write a `VERIFY_FAILED` audit row, and refuse to execute `DELETE FROM` on the source. A subsequent APPEND retry for the same `(table, year)` must also refuse until the operator reconciles the drift manually.

**Covers findings:** H4 (vacuous RESUME_DELETE self-verification), H7 (no distinction between FAILED and verify-failed).

**Depends on:** 01_setup_and_deploy (fresh test data). Uses `providers` in isolation.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/26R_resume_delete_source_drift_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log (providers only):** Must be empty (Phase 1 clears it). If entries remain from a prior run, note them.
>    - **Archive volume (providers only):** No `year_*` folders should exist. If folders exist, tell the user to delete via `databricks fs rm`.
>    - **Source table (providers):** Must have data for all years (2020–2025). If rows were deleted by prior tests, re-run `generate_test_data`.
>    - **table_configs (providers):** `delete_after_archive` should be `false` (Phase 1 sets it to `true`); `archive_base_path` must be correct.
>    - **Other tables:** Do NOT touch `claims` or `members`.

---

## Phase 1 — Establish preconditions (clean providers)

### 1a. Clear audit log for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

### 1b. Remove existing archive folders for providers

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

(OK if this returns "not found".)

### 1c. Enable delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 26: source drift test'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 1d. Record baseline source counts

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
GROUP BY 1 ORDER BY 1
```

**Expect:** One row per year 2020–2025 with counts matching test-data generation. Record the count for the year you'll target in Phase 2 (the test uses **year 2022** below; adjust if 2022 is missing).

---

## Phase 2 — Create a clean ARCHIVED state for year 2022 only

The goal is to reach the state **after** ARCHIVE but **before** the matching DELETE, so a retry lands on the RESUME_DELETE branch. The easiest way to do that is to archive once with `delete_after_archive=false`, then flip it on.

### 2a. Temporarily disable delete, then archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

**Expect:** all eligible years ARCHIVED. No `ARCHIVED_AND_DELETED` rows (delete was off). Source counts unchanged.

### 2b. Re-enable delete

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 26: re-enable delete for RESUME_DELETE branch'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 2c. Verify we have a clean ARCHIVED row for 2022

```sql
SELECT table_name, year, status, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
ORDER BY created_at DESC LIMIT 5
```

**Expect:** Latest row is `status = ARCHIVED`. No `ARCHIVED_AND_DELETED`. Note `record_count` (call it `ARCHIVE_COUNT_2022`).

---

## Phase 3 — Induce source drift, trigger RESUME_DELETE, expect VERIFY_FAILED

### 3a. Insert new rows for year 2022 (drift the source)

```sql
INSERT INTO sandeep_manocha.source_data_samples.providers
  (provider_id, provider_name, specialty, npi, effective_date, state, is_active)
VALUES
  ('PRV-DRIFT-01', 'Dr. Drift1', 'Cardiology',  '9998880001', '2022-06-15', 'OH', true),
  ('PRV-DRIFT-02', 'Dr. Drift2', 'Neurology',   '9998880002', '2022-07-22', 'KY', true),
  ('PRV-DRIFT-03', 'Dr. Drift3', 'Pediatrics',  '9998880003', '2022-11-09', 'IN', true)
```

### 3b. Confirm drift

```sql
SELECT COUNT(*) AS source_2022
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2022
```

**Expect:** `source_2022 = ARCHIVE_COUNT_2022 + 3`.

### 3c. Re-run archive (RESUME_DELETE will fire for 2022)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

**Expect:** Job TERMINATES with FAILURE (or the year-2022 task fails while others succeed). The for-each loop behaviour is that the per-year task logs its own VERIFY_FAILED row and raises.

### 3d. Check audit — expect VERIFY_FAILED for year 2022

```sql
SELECT table_name, year, status, record_count, archive_run_id,
       SUBSTRING(error_message, 1, 200) AS error_excerpt, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
ORDER BY created_at DESC LIMIT 10
```

**Expect:** A new row with `status = VERIFY_FAILED`. `error_message` contains:
- the phrase `"source count"` or `"source drift"`
- both `ARCHIVE_COUNT_2022` and `ARCHIVE_COUNT_2022 + 3`
- a pointer to `docs/runbooks/verify-failed.md`

### 3e. Verify source rows for 2022 are still present

```sql
SELECT COUNT(*) AS source_2022_after_attempt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2022
```

**Expect:** `source_2022_after_attempt = ARCHIVE_COUNT_2022 + 3`. The delete was refused — no rows removed.

### 3f. Verify the archive folder is untouched

```bash
databricks fs ls \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2022 \
  --profile DEFAULT
```

**Expect:** Folder still present with the original Delta files (the ARCHIVE phase was not re-run).

---

## Phase 4 — Retry without reconciling, expect APPEND to refuse

### 4a. Re-run archive as-is (no reconcile)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

**Expect:** Year 2022 task FAILS again **without attempting APPEND or DELETE**. The message must state the prior `VERIFY_FAILED` row blocks further work.

### 4b. Check audit — expect FAILED with verify_failed_requires_reconcile

```sql
SELECT table_name, year, status, SUBSTRING(error_message, 1, 200) AS error_excerpt, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
ORDER BY created_at DESC LIMIT 5
```

**Expect:** A new terminal row (typically `FAILED`) whose `error_message` contains:
- `"verify_failed_requires_reconcile"` (reason string) OR `"previous run left VERIFY_FAILED"`
- a reference to `docs/runbooks/verify-failed.md`

### 4c. Verify: no new archive data, no source deletion

```sql
SELECT COUNT(*) AS source_2022_still = COUNT(*)
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2022
```

**Expect:** Unchanged — `ARCHIVE_COUNT_2022 + 3`.

---

## Phase 5 — Reconcile and recover

Simulate operator intervention per the runbook.

### 5a. Reconcile by marking the VERIFY_FAILED row as handled

One legitimate recovery is to re-archive the delta (update the archive + its ARCHIVED row to match current source), then mark the VERIFY_FAILED row with a terminal status the APPEND gate ignores. For this test we take the simplest reconcile path: delete both the `VERIFY_FAILED` and the original `ARCHIVED` rows for 2022, then also delete the archive folder, and re-archive clean.

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
```

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2022 \
  --profile DEFAULT
```

### 5b. Re-run archive

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 5c. Verify recovery

```sql
SELECT table_name, year, status, record_count, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2022
ORDER BY created_at DESC LIMIT 5
```

**Expect:** STARTED → ARCHIVED → ARCHIVED_AND_DELETED for 2022. `record_count` equals the drifted source count (`ARCHIVE_COUNT_2022 + 3`). No more `VERIFY_FAILED` rows.

---

## Phase 6 — Cleanup

### 6a. Clear audit rows for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

### 6b. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 6c. Reset delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 26'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 6d. Restore source data

Re-run `generate_test_data` to restore providers (Phase 5b DELETE-AFTER removed the drift rows plus the original 2022 rows).
