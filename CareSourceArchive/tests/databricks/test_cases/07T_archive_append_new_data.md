# 07 — Archive Append (new data above watermark)

**Goal:** Inserting new rows with dates after the existing watermark triggers APPEND mode.

**Depends on:** 05_archive_live_create
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/07R_archive_append_new_data_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Audit log:** `claims` must have `ARCHIVED` entries with `watermark_value` set for at least one year (from test 05). The test inserts data for that year after the watermark.
>    - **Archive volume:** The claims year folder for the target year (e.g. year_2020) must exist with valid Delta data. If folder is missing but audit says ARCHIVED, the APPEND will fail with PATH_NOT_FOUND.
>    - **Source table:** `claims` has its original data. Check if any test rows (`CLM-NEW-*`) already exist from a prior run — if so, tell user to clean them up or the APPEND count will be off.

---

## Before — Note current watermark

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, watermark_value
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE table_name LIKE '%claims%' AND status = 'ARCHIVED'
   ORDER BY year DESC LIMIT 5" \
  --profile DEFAULT
```

Pick an already-archived year (e.g. 2020). Note its `watermark_value`.

## Steps

### 1. Insert new claims for an already-archived year

**Manual step** — run this SQL in the workspace:

```sql
INSERT INTO sandeep_manocha.caresource_data_samples.claims VALUES
  ('CLM-NEW-001', 'MBR-00001', 'PRV-0001', 'Medical', 'J06.9', 150.00, 'Closed', DATE'2020-12-30', current_timestamp()),
  ('CLM-NEW-002', 'MBR-00002', 'PRV-0002', 'Dental',  'E11.9', 250.00, 'Active', DATE'2020-12-31', current_timestamp())
```

> The `event_date` values must be AFTER the watermark from step above.

### 2. Re-run archive

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```

### 3. Check audit log

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, archive_mode, watermark_value
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE archive_run_id = (SELECT archive_run_id FROM sandeep_manocha.caresource_audit.archive_audit_log ORDER BY created_at DESC LIMIT 1)
   ORDER BY table_name, year" \
  --profile DEFAULT
```

**Expect:**
- Year 2020 for claims: `archive_mode` = APPEND, `record_count` includes new rows
- Other years: SKIPPED
- Watermark updated to new MAX date
