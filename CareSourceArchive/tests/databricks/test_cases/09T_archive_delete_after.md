# 09 — Archive with Delete After Archive

**Goal:** When `delete_after_archive = true`, source rows are deleted after archiving.

**Depends on:** 01_setup_and_deploy (fresh test data recommended)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/09R_archive_delete_after_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers` data. Check:
>    - **Source table:** `providers` has data for all years (2020–2025 + NULL rows). If rows were deleted by a prior test 09 run, re-run `generate_test_data` to restore.
>    - **Audit log (providers only):** If `ARCHIVED` entries exist, the archiver will try RESUME_DELETE (not fresh CREATE+DELETE). For a clean test, all providers audit entries AND archive folders must be cleared together. Never clear one without the other — that causes PATH_NOT_FOUND (ARCHIVED but no folder) or orphan ERROR (folder but no ARCHIVED).
>    - **Archive volume (providers only):** Check if `providers/year_*` folders exist. Must be consistent with audit log state.
>    - **table_configs:** `providers` has `delete_after_archive = false` (will be set to `true` during the test). Verify `is_active = true`.
>    - **Other tables:** Do NOT delete or modify `claims` or `members` data, audit entries, or archive folders.

---

## Before — Reset providers archive state

If providers was already archived by a prior test (e.g. test 05), the archiver's
ownership check will reject a delete from a different `archive_run_id`.
Reset the archive state so this run does a fresh CREATE → DELETE in one pass.

### 1. Remove existing archive folders for providers

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 2. Clear providers entries from audit log

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%providers%'
```

### 3. Enable delete_after_archive

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test: enable delete after archive for providers'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 4. Note source count (baseline)

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
   FROM sandeep_manocha.source_data_samples.providers
   GROUP BY 1 ORDER BY 1" \
  --profile DEFAULT
```

**Expect:** Same counts as original test data (source rows were never deleted by prior tests).

## Steps

### 1. Run archive (live) — scoped to providers only

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",table_config_filter="source_table = 'providers'",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

### 2. Check audit log

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, archive_mode
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE table_name LIKE '%providers%'
   ORDER BY created_at DESC LIMIT 20" \
  --profile DEFAULT
```

**Expect:** Each eligible year shows STARTED → ARCHIVED → ARCHIVED_AND_DELETED.

### 3. Verify source rows deleted

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
   FROM sandeep_manocha.source_data_samples.providers
   GROUP BY 1 ORDER BY 1" \
  --profile DEFAULT
```

**Expect:** Archived years have 0 rows in source. Recent years (within retention) still present.

### 4. Verify archive has the data

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** Count matches what was deleted from source (e.g. 163 for year 2020 per test 05 baseline).

## Cleanup

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset delete_after_archive after test'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

To restore data, re-run `generate_test_data` or use test 12 (rehydration).
