# 15 — Scanner Force Mode

**Goal:** `force=true` overwrites manually-edited table_configs (modified_by != 'scanner').

**Depends on:** 02_scanner_first_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/15R_scanner_force_mode_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **table_configs (claims):** Note current `retention_years`, `modified_by`, and `change_reason`. The test will set `retention_years = 3, modified_by = 'manual'` then verify scanner behavior. If `modified_by` is already `'manual'` from a prior test, the scanner-without-force step may already show the expected behavior — note this.
>    - **Scanner job:** Verify `caresource_scanner` bundle resource exists and is deployed (`databricks bundle validate -t dev`).
>    - **Other tables:** Scanner runs against all tables in the schema. Verify no table_configs are in a broken state that would cause scanner errors.

---

## Before

### 1. Manually edit a table config

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET retention_years = 3,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Manual override: set retention to 3 years'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

Note: `modified_by = 'manual'` means scanner normally won't overwrite this row.

## Steps

### 2. Run scanner WITHOUT force

```bash
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

Check claims config:

```bash
databricks experimental aitools tools query \
  "SELECT table_id, retention_years, modified_by
   FROM sandeep_manocha.caresource_audit.table_configs
   WHERE table_id LIKE '%claims%'" \
  --profile DEFAULT
```

**Expect:** `retention_years` still 3, `modified_by` still 'manual' (preserved).

### 3. Run scanner WITH force

```bash
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",force="true"
```

**Expect:** `retention_years` reset to template default (7), scanner overwrites the manual edit.

## Cleanup

Set retention back to desired value if needed.
