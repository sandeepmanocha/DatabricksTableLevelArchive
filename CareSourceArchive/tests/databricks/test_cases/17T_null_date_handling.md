# 17 — NULL Date Handling

**Goal:** Rows with NULL watermark columns are counted but never archived.

**Depends on:** 01_setup_and_deploy (test data has intentional NULL dates)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/17R_null_date_handling_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Source tables:** `claims`, `members`, `providers` must have rows with NULL watermark columns (~15, ~10, ~5 respectively from test data). If providers source was deleted by test 09, NULLs may be gone — only 5 NULL-date rows would remain.
>    - **Audit log / Archive volume:** Same as test 04 — existing ARCHIVED entries will cause SKIPs. Orphan folders (folders without audit) will cause ERRORs. For a clean dry run with CREATE actions, both must be absent. But SKIP actions are acceptable too — the `null_date_count` field is reported regardless of action.
>    - **table_configs:** All three tables are `is_active = true` with valid `watermark_column`.

---

## Before

Verify NULL dates exist:

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, COUNT(*) AS nulls FROM sandeep_manocha.caresource_data_samples.claims WHERE event_date IS NULL
   UNION ALL SELECT 'members', COUNT(*) FROM sandeep_manocha.caresource_data_samples.members WHERE start_date IS NULL
   UNION ALL SELECT 'providers', COUNT(*) FROM sandeep_manocha.caresource_data_samples.providers WHERE effective_date IS NULL" \
  --profile DEFAULT
```

**Expect:** claims ~15, members ~10, providers ~5 NULLs.

## Steps

### 1. Run dry run

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```

### 2. Check null_date_count in audit

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, record_count, null_date_count, conditions_applied
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE status = 'DRY_RUN'
   ORDER BY created_at DESC LIMIT 20" \
  --profile DEFAULT
```

**Expect:**
- `null_date_count` is reported but those rows are NOT included in `would_archive`
- NULLs are never moved to archive folders
- Job completes without error despite NULL values
