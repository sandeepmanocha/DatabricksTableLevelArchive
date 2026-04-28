# 29 — Append Then Same-Run Delete (D14 foundation)

**Goal:** When the first archive run captured a year with `delete_after_archive = false`, and a later run (with `delete_after_archive = true`) adds a small batch of new rows above the watermark, the main archive job must:

1. Run in **APPEND** mode for that year (not CREATE, not SKIP, not RESUME_DELETE).
2. Archive **only** the new rows above the prior watermark.
3. Delete **only** those new rows from source — scoped by the same-run watermark window `[MIN(new_wm), MAX(new_wm)]`, never by `YEAR(wm) = year`.
4. Leave every other row (originals archived by the prior run) **untouched in source**.

This is the foundation guarantee of the D14 rule: the main archive job never touches cross-run rows. Rows that pre-date this run's archive are cleaned up only by the dedicated delete job (see tests 30, 31).

We use the existing `claims` seed as the baseline — whatever `generate_test_data` currently writes for year 2020 is what we archive in Phase 2. We then add 20 new rows on top of that watermark and re-run in Phase 4 to prove the archiver touches only those 20.

**Covers:** D14 resolver rule G (VALID + ARCHIVED cross-run + new data → APPEND, not RESUME_DELETE), `_run_watermark_window` with `after_watermark = last_wm`, `_delete_archived` scoped delete.

**Depends on:** 01_setup_and_deploy (fresh test data). Uses `claims` in isolation. **Two archiver runs** only — the whole flow is intentionally packed into a single test.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/29R_append_same_run_delete_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims`. Check:
>    - **Source table (`claims`):** Must exist and have data for years 2018–2025 (as seeded by `generate_test_data`). If rows were deleted by prior tests, re-run `generate_test_data` to restore before starting.
>    - **Audit log (`claims` only):** Must be empty (Phase 1 clears it). If entries remain, note them.
>    - **Archive volume (`claims` only):** No `year_*` folders should exist under `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims`. If folders exist, tell the user to delete via `databricks fs rm`.
>    - **`table_configs` (`claims`):** `delete_after_archive` starts `false` (Phase 1 sets it explicitly); `archive_base_path` must be correct.
>    - **Other tables:** Do NOT touch `providers` or `members`. Their audit, archives, and sources must remain in whatever state the prior test left them.

---

## Phase 1 — Clean preconditions on `claims`

### 1a. Clear audit log for claims

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
```

### 1b. Remove any existing archive folders for claims

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims \
  --profile DEFAULT
```

(OK if this returns "not found".)

### 1c. Set `delete_after_archive = false` for claims

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 29: baseline archive without delete'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

### 1d. Trim the tail of year 2020 so the new rows have headroom above the watermark

The seeded `claims` table spreads `event_date` uniformly across each year, so `MAX(event_date)` for year 2020 is often `2020-12-31` already. To guarantee that Phase 3 can insert 20 rows *strictly above* the watermark (and still land them inside year 2020), trim everything on the last two days of 2020 **before** we archive:

```sql
DELETE FROM sandeep_manocha.source_data_samples.claims
WHERE YEAR(event_date) = 2020
  AND event_date >= DATE'2020-12-30'
```

This is a pre-test data setup step (like clearing the archive volume), not a mid-test "fix." Every other year and every other day of 2020 is untouched.

### 1e. Record baseline source state (per year)

```sql
SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.claims
WHERE event_date IS NOT NULL
GROUP BY 1 ORDER BY 1
```

**Expect:** One row per year 2018–2025. Record these counts as `BASELINE_YYYY` — the test relies on year 2020 in Phase 3, so at minimum note `BASELINE_2020` (typically ~620 rows after `generate_test_data`, minus whatever Step 1d trimmed). Also record the current MAX watermark for year 2020:

```sql
SELECT CAST(MAX(event_date) AS DATE) AS max_wm_2020
FROM sandeep_manocha.source_data_samples.claims
WHERE YEAR(event_date) = 2020
```

Record as `MAX_WM_2020`. After Step 1d this must be `<= 2020-12-29`. If the query returns a later date, Step 1d did not run — redo it before continuing.

---

## Phase 2 — Run #1: archive with `delete_after_archive = false`

### 2a. Run archive (claims only, live)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

### 2b. Verify audit — every eligible year reached `ARCHIVED` (no `ARCHIVED_AND_DELETED`)

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
ORDER BY year, created_at
```

**Expect:**

- Each eligible year (2018–2024 with default retention; 2025 is current and excluded) shows `STARTED` → `ARCHIVED` with `archive_mode = CREATE`.
- **No `ARCHIVED_AND_DELETED` row exists for any year.** This is the critical invariant for this phase.
- Record the `archive_run_id` — call it `RUN_1_ID`.
- Record the `ARCHIVED` row's `record_count` for year 2020 — call it `ARCHIVED_COUNT_2020_RUN_1` (should equal `BASELINE_2020`).

### 2c. Verify source untouched

```sql
SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.claims
WHERE event_date IS NOT NULL
GROUP BY 1 ORDER BY 1
```

**Expect:** Every `cnt` equals its `BASELINE_YYYY`. Source was not modified (delete was off).

### 2d. Verify archive matches source for year 2020

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = ARCHIVED_COUNT_2020_RUN_1 = BASELINE_2020`.

---

## Phase 3 — Drift: add 20 new rows above the year-2020 watermark, flip `delete_after_archive = true`

### 3a. Insert 20 new claim rows for year 2020, **all with `event_date > MAX_WM_2020`**

10 rows land on `DATE'2020-12-30'` and 10 on `DATE'2020-12-31'`. Both dates are guaranteed to be `> MAX_WM_2020` because Step 1d deleted everything from `2020-12-30` onward.

```sql
INSERT INTO sandeep_manocha.source_data_samples.claims VALUES
  ('CLM-T29-01', 'MBR-00001', 'PRV-0001', 'Medical',  'J06.9',   150.00, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-02', 'MBR-00002', 'PRV-0002', 'Dental',   'E11.9',   250.50, 'Active', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-03', 'MBR-00003', 'PRV-0003', 'Pharmacy', 'I10',      75.25, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-04', 'MBR-00004', 'PRV-0004', 'Vision',   'M54.5',   420.00, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-05', 'MBR-00005', 'PRV-0005', 'Medical',  'K21.0',   180.75, 'Active', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-06', 'MBR-00006', 'PRV-0006', 'Medical',  'J45.909', 310.10, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-07', 'MBR-00007', 'PRV-0007', 'Pharmacy', 'E78.5',    95.00, 'Pending', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-08', 'MBR-00008', 'PRV-0008', 'Dental',   'G43.909', 540.80, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-09', 'MBR-00009', 'PRV-0009', 'Vision',   'F41.1',   120.40, 'Active', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-10', 'MBR-00010', 'PRV-0010', 'Medical',  'N39.0',   275.90, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-T29-11', 'MBR-00011', 'PRV-0011', 'Medical',  'Z23',      65.00, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-12', 'MBR-00012', 'PRV-0012', 'Dental',   'R10.9',   145.30, 'Active', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-13', 'MBR-00013', 'PRV-0013', 'Pharmacy', 'M79.3',    88.75, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-14', 'MBR-00014', 'PRV-0014', 'Vision',   'J02.9',   390.20, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-15', 'MBR-00015', 'PRV-0015', 'Medical',  'L30.9',   210.00, 'Active', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-16', 'MBR-00016', 'PRV-0016', 'Medical',  'J06.9',   165.45, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-17', 'MBR-00017', 'PRV-0017', 'Pharmacy', 'E11.9',    72.30, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-18', 'MBR-00018', 'PRV-0018', 'Dental',   'I10',     185.60, 'Pending', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-19', 'MBR-00019', 'PRV-0019', 'Vision',   'M54.5',   330.00, 'Closed', DATE'2020-12-31', current_timestamp()),
  ('CLM-T29-20', 'MBR-00020', 'PRV-0020', 'Medical',  'K21.0',   245.80, 'Active', DATE'2020-12-31', current_timestamp())
```

> **Why 20 rows on two dates?** A two-day spread (2020-12-30 and 2020-12-31) lets us later verify that the same-run watermark window is `[MIN = 2020-12-30, MAX = 2020-12-31]` — not simply `YEAR(wm) = 2020`. That's the distinction between the correct D14 delete predicate and the legacy whole-year one.

### 3b. Confirm source now has `BASELINE_2020 + 20` rows for year 2020

```sql
SELECT COUNT(*) AS source_2020_after_insert
FROM sandeep_manocha.source_data_samples.claims
WHERE YEAR(event_date) = 2020
```

**Expect:** `source_2020_after_insert = BASELINE_2020 + 20`.

### 3c. Flip `delete_after_archive = true`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 29: flip delete_after_archive for append+delete run'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

---

## Phase 4 — Run #2: archive with `delete_after_archive = true` (expect APPEND + same-run delete on year 2020, SKIP on every other year)

### 4a. Re-run archive (claims only, live)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

### 4b. Verify audit for this run — year 2020 APPEND + ARCHIVED_AND_DELETED, other years SKIPPED

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM sandeep_manocha.caresource_audit.archive_audit_log
    WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year, created_at
```

Call this run's id `RUN_2_ID`.

**Expect:**

- **Year 2020:** `STARTED` → `ARCHIVED` with `archive_mode = APPEND` and `record_count = 20` → `ARCHIVED_AND_DELETED` with `record_count = 20` and `archive_run_id = RUN_2_ID`.
- **Every other eligible year (2018, 2019, 2021, 2022, 2023, 2024):** `SKIPPED` (or absent — some builds emit no row for SKIP). This is the D14 guarantee: cross-run `ARCHIVED` rows (owned by `RUN_1_ID`) do **not** trigger auto-delete when this run has no new data for that year.
- `RUN_2_ID != RUN_1_ID`.

### 4c. Verify no year other than 2020 produced an `ARCHIVED_AND_DELETED` row owned by `RUN_2_ID`

```sql
SELECT year, COUNT(*) AS rows_in_run_2
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
  AND status = 'ARCHIVED_AND_DELETED'
  AND archive_run_id = (
    SELECT MAX(archive_run_id)
    FROM sandeep_manocha.caresource_audit.archive_audit_log
    WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
      AND status = 'ARCHIVED_AND_DELETED'
  )
GROUP BY year ORDER BY year
```

**Expect:** Exactly one row: `year = 2020, rows_in_run_2 = 1`. If any other year appears here, the D14 same-run-only guarantee has been violated — record as a FAIL with the full row dump in the results file.

### 4d. Verify source lost exactly 20 rows and only from year 2020

```sql
SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.claims
WHERE event_date IS NOT NULL
GROUP BY 1 ORDER BY 1
```

**Expect:**

- **Year 2020:** `cnt = BASELINE_2020` — exactly equal to the count before the 20 rows were inserted. The 20 T29 rows were archived and deleted; no original rows were touched.
- **Every other year (2018, 2019, 2021, 2022, 2023, 2024, 2025):** `cnt = BASELINE_YYYY`. Untouched.

### 4e. Verify none of the `CLM-T29-*` rows remain in source

```sql
SELECT COUNT(*) AS t29_rows_remaining
FROM sandeep_manocha.source_data_samples.claims
WHERE claim_id LIKE 'CLM-T29-%'
```

**Expect:** `t29_rows_remaining = 0`.

### 4f. Verify originally-archived rows from year 2020 are still in source

Original year-2020 rows all sit at `event_date <= MAX_WM_2020 <= 2020-12-29`:

```sql
SELECT COUNT(*) AS original_2020_rows_still_in_source
FROM sandeep_manocha.source_data_samples.claims
WHERE YEAR(event_date) = 2020
  AND event_date <= DATE'2020-12-29'
```

**Expect:** `original_2020_rows_still_in_source = BASELINE_2020`. These rows were archived by `RUN_1_ID` but **must not** have been deleted by `RUN_2_ID` (D14 rule). Cleanup of these rows belongs to the dedicated delete job (test 30).

### 4g. Verify archive grew by exactly 20 rows for year 2020

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = BASELINE_2020 + 20` = `ARCHIVED_COUNT_2020_RUN_1 + 20`. The APPEND wrote the 20 new rows into the same archive slice; prior data is preserved.

### 4h. Sanity-check the APPEND ownership — the `ARCHIVED` row at `record_count = 20` belongs to `RUN_2_ID`

```sql
SELECT year, status, archive_mode, record_count, archive_run_id
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
  AND year = 2020
  AND archive_mode = 'APPEND'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** `record_count = 20`, `archive_run_id = RUN_2_ID`. Proves the APPEND wrote only the new rows and the same-run watermark window narrowed the `DELETE` predicate to `[MIN(new) = 2020-12-30, MAX(new) = 2020-12-31]`.

---

## Phase 5 — Cleanup

### 5a. Clear audit rows for claims

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
```

### 5b. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims \
  --profile DEFAULT
```

### 5c. Reset `delete_after_archive`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 29'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

### 5d. Restore `claims` source data

Re-run `generate_test_data` to restore claims. Phase 1d trimmed the tail of year 2020 (everything on `2020-12-30` and `2020-12-31`) and Phase 4 deleted the 20 T29 rows, so claims year 2020 is now shorter than its original seed. Restoring via `generate_test_data` is the default unless the next test explicitly does not touch claims.
