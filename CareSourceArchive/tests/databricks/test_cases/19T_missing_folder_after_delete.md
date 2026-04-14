# 19 — Missing Folder After ARCHIVED_AND_DELETED

**Goal:** When archive folder is deleted from storage after a successful `delete_after_archive` run, the next archive detects the inconsistency and halts with a rich `error_message` containing recovery steps.

**Depends on:** 09_archive_delete_after (providers must have ARCHIVED_AND_DELETED state for at least one year)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/19R_missing_folder_after_delete_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers year 2020`. Check:
>    - **Audit log (providers year 2020):** Must have an `ARCHIVED_AND_DELETED` entry (from test 09). If missing, run test 09 first. If `STARTED`/`FAILED` entries remain from a prior test 19 run, they need cleanup.
>    - **Archive volume (providers year 2020):** Folder must exist with valid Delta data. The test will delete this folder in Phase 2 to create the "missing folder after delete" condition. If already missing, the precondition setup is different.
>    - **table_configs (providers):** `delete_after_archive` should be `false` (will be set to `true` during the test).
>    - **Source table (providers):** Source data for year 2020 should already be deleted (by test 09's delete-after-archive). If source data still exists, the test behavior may differ.
>    - **Other tables/years:** Do NOT touch claims, members, or other providers years.

---

## Phase 1 — Establish Preconditions

Verify providers has at least one year with `ARCHIVED_AND_DELETED` status and the archive folder exists.

### 1a. Check audit state

```sql
SELECT table_name, year, status, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND status = 'ARCHIVED_AND_DELETED'
ORDER BY created_at DESC LIMIT 5
```

**Expect:** At least one row (e.g. year 2020). Note the year — use it in the steps below. If no rows exist, run test 09 first.

### 1b. Verify folder exists

```python
dbutils.fs.ls("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples/providers/year_2020")
```

**Expect:** Folder exists with Delta files.

### 1c. Enable delete_after_archive for providers

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 19: enable delete_after_archive for missing folder detection'
WHERE table_id = 'sandeep_manocha.caresource_data_samples.providers'
```

---

## Phase 2 — Delete folder + Run (expect failure)

### 2a. Delete the archive folder for providers year 2020

**Manual step** — run in workspace notebook:

```python
dbutils.fs.rm("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples/providers/year_2020", recurse=True)
```

This creates the condition: audit says `ARCHIVED_AND_DELETED` but folder is gone.

### 2b. Run archive (providers only)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="caresource_data_samples",table_config_filter="source_table = 'providers'"
```

### 2c. Check audit — status

```sql
SELECT table_name, year, status, archive_mode, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2020: STARTED → FAILED
- Job halts at the first failure

### 2d. Check audit — error_message content

```sql
SELECT table_name, year, status, error_message
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND status = 'FAILED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** `error_message` contains ALL of:
- `"Archive folder missing"` or `"missing_folder_after_delete"`
- `"ARCHIVED_AND_DELETED"` (explains why this is dangerous)
- `"Data may be lost"`
- `"DESCRIBE HISTORY"` (recovery step)
- `"cloud storage recycle bin"` (recovery step)

---

## Phase 3 — Cleanup

### 3a. Reset delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 19'
WHERE table_id = 'sandeep_manocha.caresource_data_samples.providers'
```

### 3b. Delete test-generated audit rows

Remove only the STARTED/FAILED rows from this test run (preserve the original ARCHIVED_AND_DELETED history):

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND year = 2020
  AND status IN ('STARTED', 'FAILED')
  AND created_at > (
    SELECT MAX(created_at)
    FROM sandeep_manocha.caresource_audit.archive_audit_log
    WHERE table_name LIKE '%providers%'
      AND year = 2020
      AND status = 'ARCHIVED_AND_DELETED'
  )
```

### 3c. Restore the archive folder

To restore the folder, either:
- Re-run test 09 (fresh archive + delete cycle)
- Or re-run `generate_test_data` then test 05 for providers

### 3d. Verify clean state

```sql
SELECT table_name, year, status, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND year = 2020
ORDER BY created_at DESC LIMIT 5
```

**Expect:** Latest row is `ARCHIVED_AND_DELETED` from the original test 09 run. No orphan STARTED or FAILED rows.
