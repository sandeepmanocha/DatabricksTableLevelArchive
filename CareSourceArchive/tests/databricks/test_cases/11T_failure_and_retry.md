# 11 — Failure and Retry

**Goal:** Archive successfully, then simulate a failure on new data, verify FAILED is logged, then fix and re-run to recover.

**Depends on:** The target table must be fully archived first (either via test 05, or by running Phase 1 below if the table is in a clean state).
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/11R_failure_and_retry_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Check:
>    - **Choose a table** (providers, claims, or members) and verify only that table's state.
>    - **Audit log (chosen table):** Must have `ARCHIVED` entries for eligible years (from test 05) for Phase 1 to SKIP. If no ARCHIVED entries, Phase 1 does a fresh CREATE (still valid but takes longer). Archive folders must match.
>    - **Archive volume (chosen table):** Year folders must exist and be consistent with audit log. Folders without audit = orphan ERROR. Audit without folders = PATH_NOT_FOUND.
>    - **table_configs (chosen table):** Note `archive_base_path` (will be broken in Phase 2, restored in Phase 3). Verify `is_active = true`.
>    - **Source table:** Verify data exists and check watermarks to decide whether to INSERT for an existing year (APPEND) or a new year (CREATE) in Phase 2a.
>    - **Other tables:** Do NOT touch any table other than the one chosen for this test.

---

## Choosing a Table

This test can run on **any table** that has been fully archived. Replace `<TABLE>`, `<TABLE_ID>`, `<WATERMARK_COL>`, and the INSERT statement accordingly.

| Table | `table_id` | Watermark Column | Years |
|-------|-----------|-----------------|-------|
| providers | `sandeep_manocha.source_data_samples.providers` | `effective_date` | 2020-2025 |
| claims | `sandeep_manocha.source_data_samples.claims` | `event_date` | 2018-2025 |
| members | `sandeep_manocha.source_data_samples.members` | `start_date` | 2019-2025 |

### Watermark considerations for new data insertion

The APPEND logic uses `WHERE <watermark_col> > watermark_value` to detect new rows. **Check watermarks before choosing how to insert:**

- **Providers** typically has mid-month watermarks (e.g., `2023-12-30`), so inserting same-year data after the watermark works (e.g., `2023-12-31` → triggers APPEND).
- **Claims** has year-end watermarks (`2023-12-31`) because the test data fills through Dec 31. No same-year date is `> 12-31`, so APPEND within an existing year is impossible. Insert data for a **new year** instead (e.g., 2026) — recovery will use CREATE instead of APPEND.
- **Members** — check watermarks before deciding.

> **Rule of thumb:** Query `SELECT year, watermark_value FROM archive_audit_log WHERE table_name LIKE '%<TABLE>%' AND status = 'ARCHIVED' ORDER BY year` before Phase 2a. If all watermarks are at year-end (12-31), insert for a new year. If any watermark is before 12-31, insert same-year data after it.

---

## Phase 1 — Good Run (baseline)

Archive the target table normally. All eligible years should show STARTED → ARCHIVED.

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="table_id = '<TABLE_ID>'"
```

**Expect:**
- TERMINATED SUCCESS
- All eligible years: STARTED → ARCHIVED in audit log

---

## Phase 2 — Bad Run (new data + broken path)

### 2a. Insert new data AFTER the watermark

Check the current watermarks first:

```sql
SELECT year, watermark_value FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%<TABLE>%' AND status = 'ARCHIVED'
ORDER BY year
```

**Option A — APPEND (watermark < year-end):** Insert rows for an existing year with dates after its watermark.

Example for **providers** (watermark `2023-12-30`):

```sql
INSERT INTO sandeep_manocha.source_data_samples.providers
  (provider_id, provider_name, specialty, npi, effective_date, state, is_active)
VALUES
  ('PRV-FAIL-04', 'Dr. FailTest4', 'Cardiology',  '9990000004', '2023-12-31', 'OH', true),
  ('PRV-FAIL-05', 'Dr. FailTest5', 'Neurology',   '9990000005', '2023-12-31', 'KY', true)
```

**Option B — CREATE new year (watermark = year-end):** Insert rows for a year that hasn't been archived yet.

Example for **claims** (all watermarks at 12-31):

```sql
INSERT INTO sandeep_manocha.source_data_samples.claims VALUES
  ('CLM-FAIL-01', 'MBR-00001', 'PRV-0001', 'Medical', 'J06.9', 150.00, 'Closed', DATE'2026-03-15', current_timestamp()),
  ('CLM-FAIL-02', 'MBR-00002', 'PRV-0002', 'Dental',  'E11.9', 250.00, 'Active', DATE'2026-04-01', current_timestamp())
```

> **Important:** The APPEND logic only picks up rows with `<watermark_col> > watermark`. Rows at or before the watermark are invisible to the archiver.

### 2b. Break the archive path

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET archive_base_path = '/Volumes/sandeep_manocha/nonexistent_volume/bad_path',
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test: force failure with bad path'
WHERE table_id = '<TABLE_ID>'
```

### 2c. Run archive

Same command as Phase 1 (scoped to the target table).

### 2d. Check audit log

```sql
SELECT table_name, year, status, SUBSTRING(error_message, 1, 120) AS error_excerpt, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%<TABLE>%'
AND status IN ('STARTED', 'FAILED')
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- First year (earliest): STARTED → FAILED with `SCHEMA_NOT_FOUND` error about the bad volume
- The bad path makes ALL years appear un-archived (since `archive_folder_exists` checks the bad path), so the archiver tries to CREATE starting from the first year and fails immediately
- The year with new data is never reached because the for-each loop stops at the first failure

---

## Phase 3 — Good Run (fix and recover)

### 3a. Restore the correct archive path

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET archive_base_path = '/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples',
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Fix: restore correct archive path'
WHERE table_id = '<TABLE_ID>'
```

### 3b. Re-run archive

Same command as Phase 1.

### 3c. Check audit log

```sql
SELECT table_name, year, status, archive_mode, record_count, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%<TABLE>%'
ORDER BY created_at DESC LIMIT 15
```

**Expect:**
- First year (previously FAILED): retries → SKIPPED (no new data, archive folder exists from Phase 1)
- Year with new data: STARTED → ARCHIVED with `archive_mode = APPEND` (if same-year insert) or `archive_mode = CREATE` (if new-year insert)
- Other years: STARTED → SKIPPED (no new data)
