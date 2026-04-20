# 14 — Rehydration

**Goal:** Restore archived data into a new schema with a unified view.

**Depends on:** 05_archive_live_create (archives must exist)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/14R_rehydration_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Archive volume (claims):** Year folders for 2020 and 2021 must exist with valid Delta data (from test 05). The rehydration job reads directly from these paths. If missing, run test 05 first.
>    - **Target schema:** `sandeep_manocha.caresource_rehydrated` should NOT exist (or should be empty). If it exists from a prior run, tell the user — it may need `DROP SCHEMA CASCADE` cleanup.
>    - **Source table:** `claims` should have current-year data so the unified view can combine archive + source.
>    - **Other tables:** This test only reads from claims archives. No writes to audit log, no modifications to other tables.

---

## Before

Ensure target schema exists:

```sql
CREATE SCHEMA IF NOT EXISTS sandeep_manocha.caresource_rehydrated
```

## Steps

### 1. Run rehydration for claims

```bash
databricks bundle run caresource_rehydrate -t dev --profile DEFAULT \
  --params config_table=sandeep_manocha.caresource_audit.global_settings \
  --params archive_base_path=/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples \
  --params source_table=sandeep_manocha.source_data_samples.claims \
  --params target_catalog=sandeep_manocha \
  --params target_schema=caresource_rehydrated \
  --params 'years="2020,2021"'
```

### 2. Verify tables created

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN sandeep_manocha.caresource_rehydrated" \
  --profile DEFAULT
```

**Expect:**
- `claims_year_2020` — external table pointing to archive Delta
- `claims_year_2021` — external table pointing to archive Delta
- `claims_unified` — view combining source + archived years

### 3. Query the unified view

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM sandeep_manocha.caresource_rehydrated.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile DEFAULT
```

**Expect:** All years present (current source + rehydrated archive years).

### 4. Check rehydration audit log

```bash
databricks experimental aitools tools query \
  "SELECT * FROM sandeep_manocha.caresource_audit.rehydration_audit_log ORDER BY created_at DESC LIMIT 5" \
  --profile DEFAULT
```

**Expect:** Status = COMPLETED, `tables_created` = 2.

## Cleanup

```sql
DROP SCHEMA IF EXISTS sandeep_manocha.caresource_rehydrated CASCADE
```
