# 34 — Archive Folder Deleted While Inside Retention Window (silent re-CREATE)

**Goal:** When an archive year folder is deleted from storage but the audit log still says `ARCHIVED` (NOT `ARCHIVED_AND_DELETED`) and the year is still inside the retention window, the next archive run should silently re-create the folder from source. This proves the system can self-heal from accidental folder deletes when the source has not yet been deleted.

> **Retention eligibility** — a year `YYYY` is **eligible for archive** when `YYYY <= current_year - retention_years` (i.e. older than the hot-window cutoff). With seeded `default_retention_years = 0` on 2026-04-26, the cutoff is 2026 so every year ≤ 2026 is eligible. Eligible years are the ones the archiver actually considers — they may receive APPENDs or, in this scenario, get re-CREATEd. Years strictly newer than the cutoff stay in the hot window and are skipped by the archiver entirely.

> **Why this is different from test 19.** Test 19 covers `MISSING + ARCHIVED_AND_DELETED` → ERROR (data loss risk, source already deleted). This test covers `MISSING + ARCHIVED` → CREATE (safe self-heal, source still intact). Same folder-missing condition, different audit state, different system response.

**Depends on:** 05_archive_live_create (claims must be fully archived; source data still present)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/34R_archive_folder_deleted_within_retention_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims year 2020`. Check:
>    - **Audit log (claims year 2020):** Must end with `ARCHIVED` (not `ARCHIVED_AND_DELETED`). If `ARCHIVED_AND_DELETED`, this is the test 19 scenario, not this one — pick a different year or re-run test 05 to restore.
>    - **Archive volume (claims year 2020):** Folder must exist with valid Delta data (from test 05). Phase 2 deletes it.
>    - **Source table (claims):** Source rows for year 2020 must still exist (count > 0). If source was deleted by an earlier delete-after-archive run, abort — re-run `generate_test_data` first.
>    - **table_configs (claims):** `delete_after_archive` should be `false` (otherwise running archive would also delete source — out of scope here).
>    - **Retention eligibility:** Confirm year 2020 is eligible for archive under the current global `retention_years`. With seeded `retention_years = 0`, cutoff = 2026 and every year ≤ 2026 is eligible. If the workspace seeds something else, pick a year `≤ current_year - retention_years`.
>    - **Other tables/years:** Do NOT touch `members`, `providers`, or other claims years.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for the placeholder → value mapping per workspace.
> All commands below use literal workspace values: profile `fe-sandbox-manocha`, target `dev-serverless`, catalog `dev2_archive`, schema `source_data_samples`.

---

## Phase 1 — Establish Preconditions

### 1a. Confirm year 2020 is ARCHIVED and folder exists

```sql
SELECT status, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND year = 2020
  AND status = 'ARCHIVED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** Exactly one row, `status = ARCHIVED`. Note `record_count` (call it `pre_count`) — we will compare against it after re-CREATE.

### 1b. Confirm folder exists with data

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020")
```

```sql
SELECT COUNT(*) AS archive_rows
FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020`
```

**Expect:** Folder lists Delta files; `archive_rows = pre_count`.

### 1c. Confirm source still has year 2020 data

```sql
SELECT COUNT(*) AS source_rows
FROM dev2_archive.source_data_samples.claims
WHERE YEAR(event_date) = 2020 AND event_date IS NOT NULL
```

**Expect:** `source_rows = pre_count`. If `source_rows = 0`, source was deleted previously — abort and re-seed.

### 1d. Confirm year 2020 is eligible for archive

```sql
SELECT
  YEAR(current_date()) - g.default_retention_years AS retention_cutoff_year,
  CASE WHEN 2020 <= YEAR(current_date()) - g.default_retention_years
       THEN 'eligible_for_archive'
       ELSE 'still_in_hot_window_skipped_by_archiver'
  END AS year_2020_eligibility
FROM dev2_archive.metadata.global_settings g
```

**Expect:** `year_2020_eligibility = 'eligible_for_archive'`. With seeded `default_retention_years = 0`, cutoff = 2026, so every year ≤ 2026 is eligible. If you see `still_in_hot_window_skipped_by_archiver`, pick a year ≤ cutoff (e.g. an earlier year) and substitute in every step below.

---

## Phase 2 — Delete the folder + Re-run (expect silent re-CREATE)

### 2a. Delete the archive folder for claims year 2020

**Manual step** — run in workspace notebook:

```python
dbutils.fs.rm("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020", recurse=True)
```

This creates the condition: audit says `ARCHIVED`, folder is gone, source data still present.

### 2b. Verify folder is gone

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020")
```

**Expect:** `FileNotFoundError` (or empty listing depending on Databricks runtime).

### 2c. Run archive (claims only)

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="false",source_catalog="dev2_archive",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

### 2d. Check audit — new ARCHIVED row should appear

```sql
SELECT table_name, year, status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE '%claims%'
  AND year = 2020
ORDER BY created_at DESC LIMIT 5
```

**Expect:**
- Newest row: `ARCHIVED` with `archive_mode = CREATE` and a brand-new `archive_run_id`.
- Right above it (older): the `STARTED` row for the same new run.
- Older history: the original `ARCHIVED` row from test 05 is still present.
- New `record_count` should equal `pre_count` (source did not change).

### 2e. Verify folder was recreated

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020")
```

```sql
SELECT COUNT(*) AS archive_rows
FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2020`
```

**Expect:** Folder exists again with Delta files; `archive_rows = pre_count`.

### 2f. Confirm no FAILED rows

```sql
SELECT COUNT(*) AS failed_rows
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND year = 2020
  AND status = 'FAILED'
  AND created_at >= current_timestamp() - INTERVAL 1 HOUR
```

**Expect:** `failed_rows = 0`. The system did **not** raise `archive_folder_missing` — `MISSING + ARCHIVED` is a safe state, not an error.

---

## Phase 3 — Re-run again (expect SKIP)

### 3a. Re-run archive

Same command as Phase 2c.

### 3b. Check audit

```sql
SELECT status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND year = 2020
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Newest row is `SKIPPED` (`archive_mode = SKIP`, `record_count = 0`). System is back to steady state.

---

## Phase 4 — Cleanup

No cleanup needed. The folder has been re-created with the same content; the audit log carries the recovery history. Subsequent tests can run normally.

If you want to fully reset for a re-run of this test:

```sql
-- Optional: trim the audit history added by this test (keeps the original ARCHIVED row from test 05 only).
DELETE FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.claims'
  AND year = 2020
  AND created_at > (
    SELECT MIN(created_at)
    FROM dev2_archive.metadata.archive_audit_log
    WHERE table_name = 'dev2_archive.source_data_samples.claims'
      AND year = 2020
      AND status = 'ARCHIVED'
  )
```

---

## What this proves

| Behavior | Pass criteria |
|----------|---------------|
| Folder deleted from storage while source still has data | Detected as `MISSING` |
| Audit shows `ARCHIVED` (not `ARCHIVED_AND_DELETED`) | Falls into rule F: `MISSING + other → CREATE` |
| Re-run rebuilds the folder from source | New `ARCHIVED` row, `archive_mode = CREATE`, same `record_count` |
| No FAILED row, no operator action needed | The system self-heals silently |
| Year is **inside** the retention window | Year is still in `_calculate_eligible_years` output |

## What this does NOT cover

- Folder deleted **and** source already deleted (`ARCHIVED_AND_DELETED`) — see test 19.
- Folder partially corrupted (Delta history present but unreadable) — see test 12 (ORPHAN).
- Year is **outside** the retention window when the folder gets deleted — outside-window years are not in the eligible list, so the archiver does not look at them. The folder stays gone until an operator re-runs with explicit year override or extends retention.
