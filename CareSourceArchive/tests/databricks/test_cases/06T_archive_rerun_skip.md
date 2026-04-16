# 06 — Archive Re-run (SKIP — no new data)

**Goal:** Re-running archive when no new data has arrived skips all years.

**Depends on:** 05_archive_live_create
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/06R_archive_rerun_skip_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log:** All three tables (`claims`, `members`, `providers`) must have `ARCHIVED` entries for their eligible years (from test 05). If missing, run test 05 first.
>    - **Archive volume:** Year folders must exist for all archived years. If folders are missing but audit says ARCHIVED, the archiver will try to APPEND/verify and fail with PATH_NOT_FOUND.
>    - **Source tables:** No new data inserted since test 05 (otherwise archiver will APPEND instead of SKIP).

---

## Steps

### 1. Re-run archive (live, same params)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

### 2. Check audit log

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, archive_mode
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE archive_run_id = (SELECT MAX(archive_run_id) FROM sandeep_manocha.caresource_audit.archive_audit_log WHERE status = 'SKIPPED')
   ORDER BY table_name, year" \
  --profile DEFAULT
```

**Expect:**
- All entries have status = `SKIPPED`
- `archive_mode` = SKIP
- `record_count` = 0
- Job completes faster than the initial CREATE run
