# 08 — Archive with Exclusion Conditions

**Goal:** Rows matching exclusion conditions are excluded from archiving.

**Depends on:** 02_scanner_first_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/08R_archive_with_exclusion_conditions_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims` data. Check:
>    - **Audit log (claims only):** If `ARCHIVED` entries exist, the archiver will SKIP those years (exclusion logic only runs on CREATE/APPEND). Both audit entries AND archive folders must be cleared together for a fresh CREATE run. Do NOT touch other tables' audit entries.
>    - **Archive volume (claims only):** If `claims/year_*` folders exist without matching audit entries = orphan ERROR. If they exist with matching audit entries = SKIP (exclusion not tested). For a clean test, both must be absent.
>    - **table_configs:** `claims` has `exclusion_conditions = NULL` (will be set during the test). Verify `is_active = true` and `watermark_column = event_date`.
>    - **Other tables:** Do NOT delete or modify `providers` or `members` data, audit entries, or archive folders — they are not part of this test.

---

## Before — Set exclusion condition on claims

**Manual step** — run in workspace:

> **Note:** The `exclusion_conditions` column is typed `ARRAY<STRUCT<...>>`, not a plain string.
> Use `NAMED_STRUCT` syntax — a JSON string literal will fail with `DATATYPE_MISMATCH.CAST_WITHOUT_SUGGESTION`.

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET exclusion_conditions = ARRAY(NAMED_STRUCT(
      'name', 'active_claims',
      'scope', 'same_table',
      'column', 'status_flag',
      'operator', 'equals',
      'value', 'Active',
      'sql', CAST(NULL AS STRING))),
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test: exclude Active claims from archiving'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

This excludes rows where `status_flag = 'Active'` from archiving.

Note how many Active claims exist per year:

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, status_flag, COUNT(*) AS cnt
   FROM sandeep_manocha.source_data_samples.claims
   WHERE YEAR(event_date) <= YEAR(current_date()) - 7
   GROUP BY 1, 2 ORDER BY 1, 2" \
  --profile DEFAULT
```

## Steps

### 1. Run dry run

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

### 2. Check dry run audit

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, record_count, conditions_applied
   FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE status = 'DRY_RUN' AND table_name LIKE '%claims%'
   ORDER BY created_at DESC LIMIT 10" \
  --profile DEFAULT
```

**Expect:**
- `would_archive` < `total_eligible` (Active claims excluded)
- `conditions_applied` JSON contains per-condition counts
- `per_condition_counts.active_claims` shows how many rows the condition matched

## Cleanup

Reset the exclusion if desired:

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET exclusion_conditions = NULL,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Remove test exclusion'
WHERE table_id = 'sandeep_manocha.source_data_samples.claims'
```

> The cleanup UPDATE works as-is because setting to `NULL` doesn't require struct syntax.
