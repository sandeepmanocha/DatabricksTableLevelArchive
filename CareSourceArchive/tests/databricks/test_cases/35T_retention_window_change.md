# 35 — Retention Window Change (shrink and grow)

**Goal:** When the customer changes `default_retention_years` in `global_settings` (or sets a per-table override in `table_configs.retention_years`), the archiver picks up the new boundary on the next run with no schema or audit migration needed. Two sub-scenarios are tested:

1. **Shrink the window** (e.g. 7 → 5): more years become archive-eligible. New years that were previously "too recent" now get archived for the first time.
2. **Grow the window** (e.g. 5 → 7): fewer years are eligible. Already-archived years that fall back inside the window are no longer touched by future runs (their folders stay untouched).

> **Retention window** — a year `YYYY` is **inside the window** (still hot in source, not yet archive-eligible) when `YYYY >= current_year - retention_years`. Eligible years for archiving are the inverse: `YYYY <= current_year - retention_years`. Increasing `retention_years` makes the window bigger and shrinks the eligible set. Decreasing it makes the window smaller and grows the eligible set.

> **Per-table override.** `table_configs.retention_years` overrides `global_settings.default_retention_years` for that one table. A NULL value falls back to global. We exercise both surfaces.

**Depends on:** 05_archive_live_create (claims, members, providers archived once at the seeded retention)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/35R_retention_window_change_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log (claims):** Should have `ARCHIVED` rows for years 2018-2025 (per test 05 with `retention_years = 0`). If absent, run test 05 first.
>    - **table_configs (claims):** Note current `retention_years` (could be NULL or an integer). Phase 4 restores it.
>    - **global_settings:** Note current `default_retention_years`. Phase 4 restores it.
>    - **Archive volume (claims):** Year folders 2018-2025 should exist with valid Delta data.
>    - **Source table (claims):** Should still have rows for years 2018-2025 (delete_after_archive should be false).
>    - **Other tables:** Do NOT touch `members`, `providers`, or their config rows.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for the placeholder → value mapping per workspace.

---

## Phase 1 — Establish baseline

### 1a. Capture the current retention setting

```sql
SELECT default_retention_years FROM dev2_archive.metadata.global_settings
```

**Note** the value as `BASELINE_GLOBAL`. Typical seeded value in the dev workspace is `0` (everything eligible). The steps below assume `BASELINE_GLOBAL = 0`; if it is non-zero, adjust the year math when reading the expectations (the **shape** of the test still holds).

```sql
SELECT table_id, retention_years
FROM dev2_archive.metadata.table_configs
WHERE table_id = 'dev2_archive.source_data_samples.claims'
```

**Note** the value as `BASELINE_TABLE` (could be NULL — meaning "inherit from global").

### 1b. Capture which years are currently archived for claims

```sql
SELECT year, status, record_count, MAX(created_at) AS latest
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND status = 'ARCHIVED'
GROUP BY year, status, record_count
ORDER BY year
```

**Expect:** Rows for the years claims data covers (e.g. 2018–2025 for the synthetic dataset). Note this list as `BASELINE_YEARS`.

---

## Phase 2 — Shrink the window (more years become eligible)

> This phase only **adds** new archive work; it never undoes prior archives. Already-archived years stay untouched (rule G: VALID + ARCHIVED + no new data → SKIP).

This phase is meaningful only when the **baseline** has some years still in the source that were not yet eligible. With `BASELINE_GLOBAL = 0` (everything eligible), there is nothing to shrink toward — skip Phase 2 and document this in the results file.

If `BASELINE_GLOBAL >= 7`, run the steps below. If `BASELINE_GLOBAL` is between 1 and 6, adjust the numbers so that the new value is at least 2 less than the old.

### 2a. (Setup, only if baseline is 0) Temporarily widen the window before shrinking

To make this phase exercise the shrink, we first widen, then shrink.

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 7,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 phase 2: widen baseline so shrink is visible'
```

After this, eligible years for claims = `YYYY <= current_year - 7`. With `current_year = 2026`, that is years 2018-2019. Years 2020-2025 are inside the window (not eligible).

> **Important.** Existing archive folders for 2020–2025 (created at baseline retention 0) **stay on disk**. The archiver does not delete them when retention grows; it just stops looking at them. We verify this in step 2b.

### 2b. Run archive (claims only) and confirm only 2018-2019 are touched

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="false",source_catalog="dev2_archive",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:**
- Only years 2018 and 2019 have new `STARTED → SKIPPED` rows for this run (already archived under baseline 0, no new data → rule G → SKIP).
- Years 2020-2025 have **no new audit rows** for this `archive_run_id` — they are no longer in the eligible list.
- Folders for 2020-2025 still exist on the volume (verify with `dbutils.fs.ls(...)` on any one). They are now "frozen archives".

### 2c. Now shrink the window: 7 → 5

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 5,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 phase 2: shrink window from 7 to 5'
```

After this, eligible years = `YYYY <= current_year - 5`. With `current_year = 2026`, that is years 2018-2021. Years 2020 and 2021 just **entered** the eligible set.

### 2d. Re-run archive (claims only)

Same command as 2b.

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:**
- Years 2018, 2019, 2020, 2021 have rows for this run.
- For 2020 and 2021: `status = SKIPPED` with `archive_mode = SKIP` (folder already exists from the baseline run, no new source data → rule G → SKIP).
  - **Note:** This is the correct behavior because folders for these years already existed from before. If the volume had been clean (folders absent), 2020 and 2021 would be `archive_mode = CREATE` here — that is the more typical "first archive of newly eligible year" path. The audit row still proves the year is now in scope.
- Years 2022-2025 still have no rows — still inside the (now smaller) window.
- No FAILED rows.

### 2e. Verify by checking ALL eligible years for the run

```sql
SELECT DISTINCT year
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:** Exactly `[2018, 2019, 2020, 2021]`.

---

## Phase 3 — Grow the window (fewer years become eligible)

> Growing the window is the riskier change for operators. Years that were eligible before are now **not** eligible. Their folders stay on disk but the archiver stops appending to them. If new rows arrive in source for those years, they will sit in source unarchived until either (a) retention is shrunk back, (b) a manual override is used, or (c) the year ages back below the cutoff naturally.

### 3a. Grow the window: 5 → 7

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = 7,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 phase 3: grow window from 5 to 7'
```

After this, eligible years = `YYYY <= 2019`. Years 2020 and 2021 just **left** the eligible set.

### 3b. Re-run archive (claims only)

Same command as 2b.

```sql
SELECT DISTINCT year
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:** Exactly `[2018, 2019]`. Years 2020 and 2021 are not in this run's audit rows even though their folders still exist.

### 3c. Confirm year 2020 and 2021 folders are untouched

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020")
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2021")
```

**Expect:** Both folders still present with their Delta files. The archiver did not delete them — it simply stopped looking at them.

### 3d. Operator visibility check

Document in the results that an operator viewing `archive_audit_log` for the most recent run will **not** see year 2020 or 2021 entries. This is an important UX point: a grown retention window silently narrows the audit footprint per run.

---

## Phase 4 — Per-table override (overrides global)

This phase proves that `table_configs.retention_years` overrides `global_settings.default_retention_years` for that one table.

### 4a. Set a stricter per-table retention for claims

Global is currently 7 (years <= 2019 eligible). Set claims to 3 (years <= 2023 eligible) — claims-only override.

```sql
UPDATE dev2_archive.metadata.table_configs
SET retention_years = 3,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 phase 4: per-table override to 3'
WHERE table_id = 'dev2_archive.source_data_samples.claims'
```

### 4b. Re-run archive (claims only)

Same command as 2b.

```sql
SELECT DISTINCT year
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND archive_run_id = (
    SELECT archive_run_id
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY year
```

**Expect:** `[2018, 2019, 2020, 2021, 2022, 2023]`. The claims override (`retention_years = 3`) wins over the global (`default_retention_years = 7`).

### 4c. Confirm the global setting did NOT change

```sql
SELECT default_retention_years FROM dev2_archive.metadata.global_settings
```

**Expect:** Still `7`. Per-table override does not mutate the global value.

### 4d. (Optional) Confirm members and providers still respect the global

If members and providers also have `ARCHIVED` history, run the archive without a table filter and confirm only their years 2018–2019 are touched (global 7), while claims gets 2018–2023 (override 3). This confirms the override is scoped to the one row.

---

## Phase 5 — Restore baseline

### 5a. Restore the per-table override

```sql
UPDATE dev2_archive.metadata.table_configs
SET retention_years = <BASELINE_TABLE>,   -- substitute the value captured in 1a; use NULL if it was NULL
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 cleanup: restore baseline per-table retention'
WHERE table_id = 'dev2_archive.source_data_samples.claims'
```

> If `BASELINE_TABLE` was NULL, use `retention_years = NULL` (do not quote).

### 5b. Restore the global

```sql
UPDATE dev2_archive.metadata.global_settings
SET default_retention_years = <BASELINE_GLOBAL>,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 35 cleanup: restore baseline global retention'
```

### 5c. Verify

```sql
SELECT default_retention_years FROM dev2_archive.metadata.global_settings;

SELECT table_id, retention_years
FROM dev2_archive.metadata.table_configs
WHERE table_id = 'dev2_archive.source_data_samples.claims';
```

**Expect:** Both values match the captured baseline.

---

## What this proves

| Behavior | Pass criteria |
|----------|---------------|
| Shrink global retention | New years enter the eligible set on next run; new audit rows appear for them |
| Grow global retention | Years above the new cutoff stop receiving audit rows; their folders are not deleted |
| Per-table override | `table_configs.retention_years` wins over `global_settings.default_retention_years` for that one table; siblings still follow global |
| Restore baseline | Setting values back returns the eligible set to the original list |
| No FAILED rows in any phase | A retention change is a config-only operation — it never errors a year |

## What this does NOT cover

- **Auto-archiving years that left the source** — if a year's source data has already been deleted (delete-after-archive ran), shrinking retention does not bring its source back. The folder is the only copy.
- **Out-of-order retention drops** — going from 7 directly to 1 is the same as 7 → 5 → 3 → 1 in terms of the eligible set; we do not test multi-step jumps separately because the result is identical.
- **Negative retention values** — `default_retention_years` is validated as a non-negative number by `src.config.validate_settings`. Negative values would be rejected at config load time.
- **Changing retention while a run is in flight** — the archive job reads merged settings once at start; mid-run mutation is not exercised here.
