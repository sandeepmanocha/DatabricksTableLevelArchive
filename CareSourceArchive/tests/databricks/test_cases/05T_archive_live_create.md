# 05 — Archive Live Run (CREATE mode)

**Goal:** First live archive creates Delta folders and logs ARCHIVED status.

**Depends on:** 04_archive_dry_run
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/05R_archive_live_create_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Source tables:** `claims`, `members`, `providers` have data with expected row counts per year.
>    - **Audit log:** No `ARCHIVED` entries for these tables (otherwise archiver returns SKIP, not CREATE). If they exist, the audit log AND the corresponding archive folders must both be cleared — never one without the other.
>    - **Archive volume:** No year folders under `.../source_data_samples/{claims,members,providers}/`. Folders without matching audit entries = orphan ERROR. Folders with matching audit entries = SKIP.
>    - **table_configs:** `claims` → `watermark_column = event_date`, `members` → `start_date`, `providers` → `effective_date`. All `is_active = true`. `delete_after_archive = false`.

**Disable These Large Tables for Testing:** `bronze_column_lineage` and `bronze_query_history`

**Enable These Tables for Testing:** `claims` (`event_date`), `members` (`start_date`), and `providers` (`effective_date`).
When enabling, ensure each table's `watermark_column` is set in `table_configs` — the scanner may leave it empty if the column name didn't match its patterns.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for the full placeholder → value mapping per workspace.
> All commands below use `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<AUDIT_TABLE>`, and `<ARCHIVE_VOL>` placeholders.

---

## Before

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM <AUDIT_TABLE> WHERE status = 'ARCHIVED'" \
  --profile <PROFILE>
```

Note source row counts per table+year:

```bash
databricks experimental aitools tools query \
  "SELECT 'claims' AS tbl, YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims GROUP BY 1,2
   UNION ALL
   SELECT 'members', YEAR(start_date), COUNT(*) FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.members GROUP BY 1,2
   UNION ALL
   SELECT 'providers', YEAR(effective_date), COUNT(*) FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.providers GROUP BY 1,2
   ORDER BY 1,2" \
  --profile <PROFILE>
```

## Steps

### 1. Run archive (live)

```bash
databricks bundle run caresource_archive_run -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",dry_run="false",source_catalog="<SOURCE_CATALOG>",source_schema="<SOURCE_SCHEMA>"
```

### 2. Check audit log

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, archive_mode, watermark_value
   FROM <AUDIT_TABLE>
   WHERE status IN ('STARTED', 'ARCHIVED')
   ORDER BY created_at DESC LIMIT 30" \
  --profile <PROFILE>
```

**Expect:**
- Each eligible table+year has a STARTED then ARCHIVED entry
- `archive_mode` = CREATE (first time)
- `record_count` > 0
- `watermark_value` recorded (MAX of watermark column for that year)

### 3. Verify archive folders exist

Check in workspace or via:

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\`" \
  --profile <PROFILE>
```

**Expect:** Count matches the `record_count` from audit log for that table+year.
