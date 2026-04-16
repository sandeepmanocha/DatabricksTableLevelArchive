# 10 — Archive with Table Config Filter

**Goal:** Run archive for a single table using `table_config_filter` parameter.

**Depends on:** 02_scanner_first_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/10R_archive_table_filter_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log (claims):** If `ARCHIVED` entries exist with matching archive folders, the dry run will produce SKIP actions (still valid for testing the filter). If ARCHIVED entries exist but folders are missing = PATH_NOT_FOUND errors.
>    - **Archive volume (claims):** Folder state must be consistent with audit log.
>    - **table_configs:** `claims` is `is_active = true`. Other tables (`members`, `providers`) should also be active so we can verify the filter excludes them.

---

## Steps

### 1. Run archive for claims only

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",table_config_filter="source_table = 'claims'",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

### 2. Check audit log

```bash
databricks experimental aitools tools query \
  "SELECT DISTINCT table_name
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE archive_run_id = (SELECT archive_run_id FROM sandeep_manocha.caresource_audit.archive_audit_log ORDER BY created_at DESC LIMIT 1)" \
  --profile DEFAULT
```

**Expect:**
- Only `claims` appears — no `members` or `providers`
- Useful for targeted re-runs after a partial failure
