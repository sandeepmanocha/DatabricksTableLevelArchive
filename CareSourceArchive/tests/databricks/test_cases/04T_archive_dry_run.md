# 04 — Archive Dry Run

**Goal:** Dry run shows eligible rows per table/year without modifying any data.

**Depends on:** 02_scanner_first_run (table_configs populated with active tables)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/04R_archive_dry_run_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Source tables:** `claims`, `members`, `providers` have data (row counts per year).
>    - **Audit log:** Any existing `DRY_RUN` entries (harmless but note them). Any `ARCHIVED` entries for these tables (means prior live runs happened — won't block a dry run but will cause SKIP actions instead of CREATE).
>    - **Archive volume:** Whether folders exist under `.../source_data_samples/{claims,members,providers}/` — existing folders + ARCHIVED audit entries = SKIPs; existing folders + no audit entries = orphan folder ERRORs.
>    - **table_configs:** All three tables are `is_active = true` with valid `watermark_column` set.

---

## Before

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt, MAX(archive_run_id) AS last_run FROM sandeep_manocha.caresource_audit.archive_audit_log" \
  --profile DEFAULT
```

Note the row count.

## Steps

### 1. Run archive in dry run mode

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

### 2. Check audit log for DRY_RUN entries

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE status = 'DRY_RUN'
   ORDER BY table_name, year" \
  --profile DEFAULT
```

**Expect:**
- Status = `DRY_RUN` for every table + year combination
- `record_count` > 0 for eligible years (years <= current_year - retention_years)
- No archive folders created on storage
- Source table row counts unchanged

### 3. Verify source data untouched

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, COUNT(*) AS cnt FROM sandeep_manocha.source_data_samples.claims
   UNION ALL SELECT 'members', COUNT(*) FROM sandeep_manocha.source_data_samples.members
   UNION ALL SELECT 'providers', COUNT(*) FROM sandeep_manocha.source_data_samples.providers" \
  --profile DEFAULT
```

**Expect:** Same counts as after test 01.
