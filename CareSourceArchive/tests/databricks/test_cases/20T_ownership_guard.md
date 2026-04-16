# 20 — Ownership Guard (Delete Blocked by Foreign Run)

**Goal:** When `delete_after_archive=true`, the delete step only proceeds if **this** run owns the `ARCHIVED` row. A fake `ARCHIVED` row from a different `archive_run_id` blocks the delete with a rich `error_message` containing a diagnostic query.

**Depends on:** 01_setup_and_deploy (fresh test data), providers must NOT already have archive state (run cleanup first)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/20R_ownership_guard_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log (providers):** Must be empty (Phase 1 deletes all providers audit entries). If entries remain from a prior test 20 run (especially `fake-foreign-run-00000`), note them — they need cleanup.
>    - **Archive volume (providers):** Must have no folders (Phase 1 removes them). If folders exist from prior tests, tell the user to delete via `dbutils.fs.rm` or `databricks fs rm`.
>    - **Source table (providers):** Must have data for all years (2020–2025). If rows were deleted by test 09 and not restored, the archive-and-delete in Phase 2a won't have source data to work with. Tell user to re-run `generate_test_data` to restore.
>    - **table_configs (providers):** `delete_after_archive` should be `false` (Phase 1 sets it to `true`). Note current `archive_base_path` — must point to the correct volume.
>    - **Other tables:** Do NOT touch `claims` or `members` data, audit entries, or archive folders.

---

## Phase 1 — Establish Preconditions

Start clean: no existing archive folders or audit rows for providers.

### 1a. Remove existing archive folders for providers

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

(OK if this returns "not found" — folder may not exist.)

### 1b. Clear providers entries from audit log

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

### 1c. Enable delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 20: ownership guard test'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 1d. Verify clean state

```sql
SELECT COUNT(*) AS audit_entries
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

**Expect:** `audit_entries = 0`

### 1e. Note source count (baseline)

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
   FROM sandeep_manocha.source_data_samples.providers
   GROUP BY 1 ORDER BY 1" \
  --profile DEFAULT
```

Record counts — source should be untouched after this test.

---

## Phase 2 — Inject fake ARCHIVED + Run (expect ownership failure)

### 2a. Run archive (providers only, live) — let it create the archive

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

**Wait for completion.** This should produce STARTED → ARCHIVED → ARCHIVED_AND_DELETED for eligible years (the run owns its own ARCHIVED row, so delete proceeds).

### 2b. Verify it worked

```sql
SELECT table_name, year, status, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
ORDER BY created_at DESC LIMIT 20
```

**Expect:** STARTED → ARCHIVED → ARCHIVED_AND_DELETED for eligible years. Note the `archive_run_id` — this is the legitimate run.

### 2c. Reset for ownership test

Now we need to re-create a scenario where the archive exists but a *different* run owns the ARCHIVED row. Reset year 2020:

1. Re-seed source data for providers (or use a year that still has source data).
2. Delete only the ARCHIVED_AND_DELETED row for year 2020, keep the ARCHIVED row, then change its `archive_run_id` to a fake value:

```sql
-- Remove the ARCHIVED_AND_DELETED row (so resume-delete triggers)
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED_AND_DELETED'
```

```sql
-- Change the ARCHIVED row's archive_run_id to a fake foreign run
UPDATE sandeep_manocha.caresource_audit.archive_audit_log
SET archive_run_id = 'fake-foreign-run-00000'
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED'
```

### 2d. Verify setup

```sql
SELECT table_name, year, status, archive_run_id
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
ORDER BY created_at DESC
```

**Expect:** One row: `status = ARCHIVED`, `archive_run_id = 'fake-foreign-run-00000'`. No `ARCHIVED_AND_DELETED` row. Archive folder still exists on storage.

This creates the condition: `ARCHIVED` + `delete_after_archive=true` → RESUME_DELETE path → ownership check → **this** run's `archive_run_id` does not match → should fail.

### 2e. Run archive again (triggers resume-delete for year 2020)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 2f. Check audit — status

```sql
SELECT table_name, year, status, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND year = 2020
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2020: STARTED → FAILED (ownership check rejected)
- Source data for year 2020 is **not deleted** (ownership guard protected it)

### 2g. Check audit — error_message content

```sql
SELECT table_name, year, status, error_message
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
  AND year = 2020
  AND status = 'FAILED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** `error_message` contains ALL of:
- `"Delete blocked"` or `"ownership"`
- The current run's `archive_run_id` (not `fake-foreign-run-00000`)
- `"does not own the ARCHIVED row"`
- A diagnostic SELECT query referencing `archive_audit_log`

### 2h. Verify source data is safe

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
   FROM sandeep_manocha.source_data_samples.providers
   WHERE YEAR(effective_date) = 2020" \
  --profile DEFAULT
```

**Expect:** Source rows still present (ownership guard prevented deletion).

---

## Phase 3 — Cleanup

### 3a. Delete all test audit rows for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

### 3b. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 3c. Reset delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 20'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 3d. Verify clean state

```sql
SELECT COUNT(*) AS providers_audit_rows
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

**Expect:** `providers_audit_rows = 0`

### 3e. Restore source data if needed

If providers source data was deleted by step 2a, re-run `generate_test_data` to restore.
