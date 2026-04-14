# 02 — Scanner First Run

**Goal:** Run the scanner to auto-discover tables and populate `table_configs`.

**Depends on:** 01_setup_and_deploy
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/02R_scanner_first_run_results.md` (below the H1 title). Do not overwrite previous runs.

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

---

## Before

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM sandeep_manocha.caresource_audit.scanner_log" \
  --profile DEFAULT

databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM sandeep_manocha.caresource_audit.table_configs" \
  --profile DEFAULT
```

## Steps

### 1. Run the scanner

```bash
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

**Expect:** Job succeeds within ~60 seconds.

### 2. Check scanner_log

```bash
databricks experimental aitools tools query \
  "SELECT source_table, match_status, matched_column, is_active, merge_action
   FROM sandeep_manocha.caresource_audit.scanner_log
   ORDER BY created_at DESC LIMIT 20" \
  --profile DEFAULT
```

**Expect:**
- `claims` → matched on `event_date`, active, merge_action = added
- `members` → unmatched (watermark patterns don't include `start_date`)
- `providers` → unmatched (watermark patterns don't include `effective_date`)

> **Note:** If `members` and `providers` show as unmatched, update `schema_templates.watermark_column_patterns` to include `start_date` and `effective_date`, then re-run scanner.

### 3. Check table_configs

```bash
databricks experimental aitools tools query \
  "SELECT table_id, watermark_column, is_active, reason
   FROM sandeep_manocha.caresource_audit.table_configs
   ORDER BY table_id" \
  --profile DEFAULT
```

**Expect:** One row per discovered table. Active tables have `watermark_column` set. Inactive tables have a `reason`.

---

## Manual Steps

- If watermark patterns need updating:

```sql
UPDATE sandeep_manocha.caresource_audit.schema_templates
SET watermark_column_patterns = ARRAY('event_date', 'start_time', 'query_date', 'start_date', 'effective_date')
WHERE schema_id = 'sandeep_manocha__caresource_data_samples'
```

Then re-run the scanner.
