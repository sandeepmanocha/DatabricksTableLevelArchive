# 03 — Scanner Re-scan (Idempotent)

**Goal:** Re-running the scanner doesn't duplicate or break existing configs.

**Depends on:** 02_scanner_first_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/03R_scanner_rescan_idempotent_results.md` (below the H1 title). Do not overwrite previous runs.

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
  "SELECT table_id, modified_by, scan_run_id FROM sandeep_manocha.caresource_audit.table_configs ORDER BY table_id" \
  --profile DEFAULT
```

Note the `scan_run_id` values.

## Steps

### 1. Re-run the scanner

```bash
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

### 2. Verify table_configs unchanged

```bash
databricks experimental aitools tools query \
  "SELECT table_id, watermark_column, is_active, scan_run_id
   FROM sandeep_manocha.caresource_audit.table_configs ORDER BY table_id" \
  --profile DEFAULT
```

**Expect:**
- Same number of rows as before
- `scan_run_id` updated to new value
- `merge_action` in scanner_log = "updated" (not "added")
- `watermark_column` and `is_active` unchanged
- No duplicate `table_id` entries
