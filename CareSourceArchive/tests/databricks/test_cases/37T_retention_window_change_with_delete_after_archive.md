# 37 — Retention Window Change with `delete_after_archive = true`

**Goal:** Variant of test 35 with `delete_after_archive = true`. Proves three things:

1. **Shrink retention with DAA on** → newly eligible years get archived **and** their source rows deleted in the same run (`CREATE → ARCHIVED → ARCHIVED_AND_DELETED`).
2. **Grow retention past `ARCHIVED_AND_DELETED` years** → those years drop out of the eligible list silently. Their folders stay, source is already empty, no new audit rows. Operators see fewer years in the run summary.
3. **Shrink retention back so those years are eligible again** → rule D (`VALID + ARCHIVED_AND_DELETED → SKIP`) keeps the system safe. The archiver does not try to re-archive an empty source year.

> **Why this is its own test.** Test 35 keeps DAA off so source rows stay put — we only watch the eligible-year list move. Test 37 keeps DAA on so source rows get deleted as years enter scope, and we verify the system handles the back-and-forth without re-archiving an already-deleted year or losing data.

> **Retention recap.** A year `YYYY` is eligible for archiving when `YYYY <= current_year - retention_years`. Increasing `retention_years` shrinks the eligible set; decreasing it grows the set.

**Depends on:** 09_archive_delete_after (providers must have at least one `ARCHIVED_AND_DELETED` year). Easier path: start from a clean providers state and run this test end-to-end.
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/37R_retention_window_change_with_delete_after_archive_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Source table (providers):** Should have rows for years 2020–2025 (full set per `generate_test_data`). If a prior test 09/36 run already deleted some years, run `generate_test_data` to restore.
>    - **Audit log (providers):** No partial state. If `ARCHIVED_AND_DELETED` rows already exist for some years, document them; the test still works but the row counts in expectations need adjustment.
>    - **Archive volume (providers):** Folders for any prior runs are fine. The test does not delete them.
>    - **table_configs (providers):** Note the current `retention_years`. The body assumes NULL (inherit global). Phase 5 restores whatever was captured.
>    - **global_settings:** Note current `default_retention_years` as `BASELINE_GLOBAL`. Phase 5 restores it.
>    - **Other tables:** Do NOT touch `claims` or `members`.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md). The body assumes `current_year = 2026`; if running in a different year, adjust the year math.

---

## Phase 1 — Establish baseline (DAA on, retention 7)

### 1a. Capture baseline retention values

```sql
SELECT default_retention_years FROM dev2_archive.metadata.global_settings
```

Note as `BASELINE_GLOBAL`.

```sql
SELECT retention_years
FROM dev2_archive.metadata.table_configs
WHERE table_id = 'dev2_archive.source_data_samples.providers'
```

Note as `BASELINE_TABLE` (could be NULL).

### 1b. Set retention to 7 (so only year 2018-2019 would be eligible) and enable DAA

> Year 2026 - 7 = 2019. Eligible = years ≤ 2019. Providers data starts at 2020, so initially **no years are eligible**. This is the starting condition we want.

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 7,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 phase 1: set retention to 7 as starting baseline';

UPDATE dev2_archive.metadata.table_configs
SET retention_years = NULL,
    delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 phase 1: inherit global retention; enable DAA'
WHERE table_id = 'dev2_archive.source_data_samples.providers';
```

### 1c. Run archive (providers only) — expect 0 eligible years

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="false",source_catalog="dev2_archive",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

```sql
SELECT DISTINCT year
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.providers'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:** Empty result (no audit rows for this run). Eligible-year list is empty because providers data starts at 2020 and the cutoff is 2019.

### 1d. Capture source counts per year (baseline)

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM dev2_archive.source_data_samples.providers
WHERE effective_date IS NOT NULL
GROUP BY 1 ORDER BY 1
```

**Note** the counts. Call them `src_2020_pre`, `src_2021_pre`, etc. After the shrink phase we will check that 2020 and 2021 hit zero in source.

---

## Phase 2 — Shrink retention to 5 (DAA on)

> Year 2026 - 5 = 2021. Eligible = years ≤ 2021. Years 2020 and 2021 just entered scope. With DAA on, expect each to go through `CREATE → ARCHIVED → ARCHIVED_AND_DELETED` in this run.

### 2a. Shrink retention

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 5,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 phase 2: shrink window from 7 to 5'
```

### 2b. Run archive (providers only)

Same command as 1c.

### 2c. Check audit — full lifecycle for both years

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.providers'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year, created_at
```

**Expect (per year, in order):**
- Year 2020: `STARTED → ARCHIVED (mode=CREATE, count=src_2020_pre) → ARCHIVED_AND_DELETED (count=src_2020_pre)`.
- Year 2021: `STARTED → ARCHIVED (mode=CREATE, count=src_2021_pre) → ARCHIVED_AND_DELETED (count=src_2021_pre)`.
- No other years in this run.
- No FAILED rows.

### 2d. Verify source for those years is now empty

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM dev2_archive.source_data_samples.providers
WHERE effective_date IS NOT NULL
GROUP BY 1 ORDER BY 1
```

**Expect:** Years 2020 and 2021 are absent (count = 0). Years 2022-2025 still match `src_*_pre` from 1d.

### 2e. Verify the archive folders exist with the right counts

```sql
SELECT
  (SELECT COUNT(*) FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020`) AS arch_2020,
  (SELECT COUNT(*) FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2021`) AS arch_2021
```

**Expect:** `arch_2020 = src_2020_pre`, `arch_2021 = src_2021_pre`.

---

## Phase 3 — Grow retention back to 7 (DAA still on)

> Year 2026 - 7 = 2019. Eligible = years ≤ 2019. Years 2020 and 2021 just **left** scope. Their archive folders stay; source is already empty for them.

### 3a. Grow retention

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 7,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 phase 3: grow window back to 7'
```

### 3b. Run archive (providers only)

Same command as 1c.

### 3c. Check audit — no rows for 2020/2021 in this run

```sql
SELECT DISTINCT year
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.providers'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:** Empty (no eligible years again — providers data starts at 2020, cutoff is 2019). Years 2020 and 2021 are silently out of scope.

### 3d. Verify folders for 2020/2021 are still there

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020")
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2021")
```

**Expect:** Both folders still present. The archiver did not delete them — it just stopped looking.

### 3e. Verify the original `ARCHIVED_AND_DELETED` rows are intact

```sql
SELECT year, status, MAX(created_at) AS latest
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND year IN (2020, 2021)
  AND status = 'ARCHIVED_AND_DELETED'
GROUP BY year, status
ORDER BY year
```

**Expect:** One row each for 2020 and 2021 with the timestamps from Phase 2.

---

## Phase 4 — Shrink retention back to 5 (DAA on, years already deleted)

> Year 2020 and 2021 enter scope again. Source for them is empty. Folders exist. The archiver must hit rule D (`VALID + ARCHIVED_AND_DELETED → SKIP`) and not try to re-archive an empty source.

### 4a. Shrink retention

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 5,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 phase 4: shrink back to 5 to re-include 2020/2021'
```

### 4b. Run archive (providers only)

Same command as 1c.

### 4c. Check audit — SKIP for 2020 and 2021

```sql
SELECT year, status, archive_mode, record_count, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.providers'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:**
- Year 2020: `STARTED → SKIPPED (mode=SKIP, count=0)`.
- Year 2021: `STARTED → SKIPPED (mode=SKIP, count=0)`.
- No `ARCHIVED` or `ARCHIVED_AND_DELETED` rows in this run for these years (rule D wins).
- No FAILED rows.

### 4d. Confirm folders unchanged and source still empty

```sql
SELECT
  (SELECT COUNT(*) FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020`) AS arch_2020,
  (SELECT COUNT(*) FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2021`) AS arch_2021,
  (SELECT COUNT(*) FROM dev2_archive.source_data_samples.providers WHERE YEAR(effective_date) IN (2020, 2021) AND effective_date IS NOT NULL) AS src_2020_2021
```

**Expect:** `arch_2020 = src_2020_pre`, `arch_2021 = src_2021_pre`, `src_2020_2021 = 0`. Nothing was rewritten.

---

## Phase 5 — Cleanup

### 5a. Reset DAA

```sql
UPDATE dev2_archive.metadata.table_configs
SET delete_after_archive = false,
    retention_years = <BASELINE_TABLE>,   -- substitute baseline (use NULL if it was NULL)
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 cleanup: reset DAA and per-table retention'
WHERE table_id = 'dev2_archive.source_data_samples.providers'
```

### 5b. Restore global retention

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = <BASELINE_GLOBAL>,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 37 cleanup: restore baseline global retention'
```

### 5c. (Optional) Restore providers source data

After this test, providers years 2020 and 2021 only exist in the archive volume. Re-run `generate_test_data` to restore the full source table for downstream tests, or leave as-is if you intend to run rehydration tests next.

---

## What this proves

| Behavior | Pass criteria |
|----------|---------------|
| Shrink retention with DAA=true | Newly eligible years go through full `CREATE → ARCHIVED → ARCHIVED_AND_DELETED` in one run |
| Source rows for newly eligible years are deleted | Source count = 0 for those years post-run |
| Grow retention past `ARCHIVED_AND_DELETED` years | No new audit rows for them; folders stay; source already empty |
| Shrink retention back so they re-enter scope | Rule D triggers `SKIP`; archiver does NOT try to re-archive an empty source |
| No FAILED rows across all four phases | Retention oscillation under DAA is a safe, idempotent operation |

## What this does NOT cover

- **DAA flipped on per-year mid-test** — DAA is a table-level flag. The system does not support per-year DAA toggles.
- **Source data resurrected for an already `ARCHIVED_AND_DELETED` year** — if rows somehow re-appear in source for a year that has `ARCHIVED_AND_DELETED` audit history, the current archiver hits rule D and SKIPs. The new rows would sit in source unarchived. This is documented behavior; covered by the design spec, not by a test.
- **Retention shrunk during an in-flight run** — settings are read once at run start.
- **Folder deletion + retention change combined in one operator action** — covered separately by tests 19, 34, and 36.
