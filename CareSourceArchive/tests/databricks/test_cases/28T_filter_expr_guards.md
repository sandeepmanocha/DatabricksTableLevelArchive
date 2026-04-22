# 28 — `filter_expr` Typo Guards and Typed Params

**Goal:** Verify the hardened `load_table_configs` surface:
1. `table_config_filter` containing forbidden tokens (`;`, `--`, `/*`, `*/`) is rejected at **config-load time** with a clear `ArchiveConfigError` — the job never enters the archive loop and no SQL runs against source tables.
2. A syntactically-wrong `table_config_filter` (e.g. bad column name) is rejected at **launch time** with the Spark parse error surfaced as an `ArchiveConfigError(field="filter_expr", ...)`, not a mid-run failure.
3. The new typed widgets `source_catalog` / `source_schema` resolve the same configs as the equivalent raw-SQL filter.

**Covers findings:** H10 (free-text `filter_expr` hardening + typed params).

**Depends on:** 01_setup_and_deploy (config tables seeded with at least `claims`, `members`, `providers`).

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/28R_filter_expr_guards_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings:
>    - **table_configs:** `claims`, `members`, `providers` all `is_active = true`.
>    - **dry_run:** All sub-phases use `dry_run="true"` — **no writes to source tables or archive volumes**. No cleanup required for passing runs.
>    - **Audit log:** Phases may still log `STARTED` rows for dry-run tasks; that is expected. Cleanup in Phase 5 is optional.

---

## Phase 1 — Forbidden tokens rejected at launch

Each sub-case must fail **before** the per-table archive loop starts. The signal is: (a) the job TERMINATES with FAILURE, (b) the error names `filter_expr` and cites the forbidden-token rule, (c) no STARTED audit row is written for any specific `(table, year)` pair beyond (at most) the top-level run marker.

### 1a. Semicolon in filter_expr

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'; DROP TABLE sandeep_manocha.source_data_samples.members"
```

**Expect:** TERMINATED FAILURE. Error message contains `filter_expr` and one of: `"must not contain SQL comments or statement separators"`, `"forbidden"`, or `"semicolon"`. The `members` table is still present after the run (verify with `DESCRIBE TABLE sandeep_manocha.source_data_samples.members` — should still return its normal schema).

### 1b. Inline SQL comment `--` in filter_expr

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims' -- AND is_active = false"
```

**Expect:** TERMINATED FAILURE with the same `filter_expr` rejection message. Key point: the operator probably intended to test a commented-out condition; the guard catches this mistake **before** the run silently widens scope to include inactive rows.

### 1c. Block-comment `/* ... */` in filter_expr

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table /* evil */ = 'claims'"
```

**Expect:** TERMINATED FAILURE with the `filter_expr` rejection message.

---

## Phase 2 — Parse error surfaces as ArchiveConfigError

### 2a. Unknown column in filter_expr

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="nonexistent_column = 'foo'"
```

**Expect:** TERMINATED FAILURE **at config-load time** (before any per-table archive work starts). Error message:
- contains `filter_expr` (clear attribution to the widget)
- surfaces the underlying Spark parse / analysis message (e.g. `UNRESOLVED_COLUMN`, `cannot resolve 'nonexistent_column'`, or similar)
- is readable enough that an operator can fix the widget without reading logs

### 2b. Missing quote in filter_expr

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims"
```

**Expect:** TERMINATED FAILURE **at config-load time**. Error message contains `filter_expr` and the Spark parser's unterminated-string complaint.

### 2c. Valid filter_expr should still succeed

Sanity check — the guard must not false-positive on valid SQL:

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

**Expect:** TERMINATED SUCCESS. Dry-run produces SKIP/CREATE/APPEND decisions for `claims` only (no `members`, no `providers`).

---

## Phase 3 — Typed params (`source_catalog` / `source_schema`) work standalone

The notebook now prefers typed widgets. Verify they resolve the same configs as the equivalent raw filter.

### 3a. Run with typed widgets only, no `table_config_filter`

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples"
```

**Expect:** TERMINATED SUCCESS. All three tables (`claims`, `members`, `providers`) are considered. Note the resolved config count in the notebook's `display()` / log output — call it `N_TYPED`.

### 3b. Equivalent raw-filter run (legacy style)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",table_config_filter="source_catalog = 'sandeep_manocha' AND source_schema = 'source_data_samples'"
```

**Expect:** TERMINATED SUCCESS. Same three tables considered. Resolved config count `N_RAW` must equal `N_TYPED` from 3a.

### 3c. Typed params combined with a narrowing filter

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

**Expect:** TERMINATED SUCCESS. Only `claims` is considered. This confirms typed params AND `filter_expr` AND-together correctly (the hardened filter is still an escape hatch, not a replacement).

### 3d. Mismatched typed params pair

The notebook enforces that `source_catalog` and `source_schema` are both set or both unset:

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha"
```

**Expect:** TERMINATED FAILURE with a clear message `"source_catalog and source_schema must both be provided together"` (or equivalent).

---

## Phase 4 — Empty-result error when all filters exclude everything

### 4a. Filter that matches no config

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="no_such_catalog",source_schema="no_such_schema"
```

**Expect:** TERMINATED FAILURE with a readable `ArchiveConfigError` that names the supplied filter values. The notebook must NOT silently succeed with zero configs — that would be a trap.

---

## Phase 5 — Cleanup (optional)

Dry-run phases may have written `STARTED` audit rows. They are harmless but if you want to clear them:

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE archive_run_id IN (
  SELECT DISTINCT archive_run_id
  FROM sandeep_manocha.caresource_audit.archive_audit_log
  WHERE created_at > current_timestamp() - INTERVAL 1 HOUR
    AND status = 'STARTED'
)
```

(Adjust the time window to fit the actual run time.)
