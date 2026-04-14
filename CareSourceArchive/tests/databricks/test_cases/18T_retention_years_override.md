# 18 — Retention Years Override (per-table)

**Goal:** Per-table `retention_years` overrides the global default.

**Depends on:** 02_scanner_first_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/18R_retention_years_override_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **global_settings:** Confirm `default_retention_years = 7`. The test compares per-table override (3 years → years <= 2023 eligible) against global default (7 years → years <= 2019 eligible).
>    - **table_configs (claims):** Note current `retention_years`. Should be `NULL` (using global default). If already set to `3` from a prior test 18 run, note it. The test will set it to `3` and expects more years to become eligible.
>    - **Audit log (claims):** If `ARCHIVED` or `DRY_RUN` entries exist, the dry run will produce SKIP/DRY_RUN results for those years. This still validates retention logic — the key check is which years appear at all.
>    - **Source table (claims):** Must have data spanning years 2018–2025 for the year-range comparison to be meaningful.

---

## Before

Check the global default:

```bash
databricks experimental aitools tools query \
  "SELECT default_retention_years FROM sandeep_manocha.caresource_audit.global_settings" \
  --profile DEFAULT
```

**Expect:** 7 years.

### 1. Set a per-table override

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET retention_years = 3,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test: override retention to 3 years for claims'
WHERE table_id = 'sandeep_manocha.caresource_data_samples.claims'
```

With `retention_years = 3` and current year 2026, years <= 2023 are eligible.
With global default of 7, years <= 2019 would be eligible.

## Steps

### 2. Run dry run for claims only

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",table_config_filter="source_table = 'claims'",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```

### 3. Check which years are eligible

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, record_count
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE status = 'DRY_RUN' AND table_name LIKE '%claims%'
   ORDER BY created_at DESC LIMIT 10" \
  --profile DEFAULT
```

**Expect:** Years 2018–2023 appear (not just 2018–2019).

## Cleanup

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET retention_years = NULL,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset: use global default retention'
WHERE table_id = 'sandeep_manocha.caresource_data_samples.claims'
```
