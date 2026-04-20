# 25 — End-to-End: Archive then Rehydrate

**Goal:** Verify the full round-trip — archive live source data, then rehydrate it back into views and confirm row counts match the originals.

**Depends on:** 01_setup_and_deploy (bundle deployed, config tables seeded), 02_scanner_first_run (tables scanned)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/25R_e2e_archive_then_rehydrate_results.md` (below the H1 title). Do not overwrite previous runs.

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
>    - **Source tables:** `claims` has data with expected row counts per year (at minimum years 2020 and 2021).
>    - **Audit log:** No existing `ARCHIVED` entries for `claims`. If they exist, the audit log AND the corresponding archive folders must both be cleared — never one without the other.
>    - **Archive volume:** No year folders under `.../source_data_samples/claims/`. Folders without matching audit entries = orphan ERROR.
>    - **Target schema:** `<SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>` should **not** exist. If it does, tell the user — it may need `DROP SCHEMA ... CASCADE` cleanup.
>    - **table_configs:** `claims` → `watermark_column = event_date`, `is_active = true`, `delete_after_archive = false`.

**Disable These Large Tables for Testing:** `bronze_column_lineage` and `bronze_query_history`

**Enable These Tables for Testing:** `claims` (`event_date`).
When enabling, ensure the `watermark_column` is set in `table_configs`.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md) for `<PROFILE>`, `<TARGET>`, `<CONFIG_TABLE>`, `<SOURCE_CATALOG>`, `<SOURCE_SCHEMA>`, `<ARCHIVE_VOL>`, `<AUDIT_TABLE>`, and `<CONFIG_TABLES_PREFIX>`.
>
> **This test also uses:**
>
> | Placeholder | Description |
> | --- | --- |
> | `<REHYDRATE_TARGET_SCHEMA>` | Schema where rehydrated views are created (e.g. `caresource_rehydrated`). Must be safe to drop and recreate. |
> | `<REHYDRATION_AUDIT_TABLE>` | Full name of `rehydration_audit_log` — typically `<CONFIG_TABLES_PREFIX>.rehydration_audit_log`. |

---

## Before (pre-flight)

Record source row counts for `claims` by year:

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

Confirm no prior archives exist for `claims`:

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status FROM <AUDIT_TABLE>
   WHERE table_name = 'claims' AND status = 'ARCHIVED'
   ORDER BY year" \
  --profile <PROFILE>
```

**Expect:** Zero rows. If rows exist, tell the user — both audit entries and archive folders must be cleared before proceeding.

Confirm rehydration target schema does not exist:

```bash
databricks experimental aitools tools query \
  "SHOW SCHEMAS IN <SOURCE_CATALOG> LIKE '<REHYDRATE_TARGET_SCHEMA>'" \
  --profile <PROFILE>
```

**Expect:** Zero rows, or tell the user about the existing schema.

---

## Steps

### 1. Archive claims (live run)

```bash
databricks bundle run caresource_archive_run -t <TARGET> --profile <PROFILE> \
  --params config_table="<CONFIG_TABLE>",dry_run="false",source_catalog="<SOURCE_CATALOG>",source_schema="<SOURCE_SCHEMA>"
```

**Expect:** Job completes successfully.

---

### 2. Verify archive audit log

```bash
databricks experimental aitools tools query \
  "SELECT table_name, year, status, record_count, archive_mode, watermark_value
   FROM <AUDIT_TABLE>
   WHERE table_name = 'claims' AND status IN ('STARTED', 'ARCHIVED')
   ORDER BY year, created_at" \
  --profile <PROFILE>
```

**Expect:**
- Each year has a STARTED then ARCHIVED entry.
- `archive_mode` = CREATE.
- `record_count` > 0 for each year.
- Save the `record_count` per year — these are the baseline for rehydration comparison.

---

### 3. Verify archive Delta folders

Spot-check one or two years:

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2020\`" \
  --profile <PROFILE>
```

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`<ARCHIVE_VOL>/claims/year_2021\`" \
  --profile <PROFILE>
```

**Expect:** Counts match the `record_count` from step 2 for the corresponding year.

---

### 4. Rehydrate archived claims

Drop the target schema to ensure a clean slate:

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

Run rehydration:

```bash
databricks bundle run caresource_rehydrate -t <TARGET> --profile <PROFILE> \
  --params config_table=<CONFIG_TABLE> \
  --params archive_base_path=<ARCHIVE_VOL> \
  --params source_table=<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims \
  --params target_catalog=<SOURCE_CATALOG> \
  --params target_schema=<REHYDRATE_TARGET_SCHEMA> \
  --params 'years="2020,2021"'
```

**Expect:** Job completes successfully.

---

### 5. Verify rehydrated views exist

```bash
databricks experimental aitools tools query \
  "SHOW TABLES IN <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>" \
  --profile <PROFILE>
```

**Expect:**
- `claims_year_2020` (view)
- `claims_year_2021` (view)
- `claims_unified` (view)

---

### 6. Verify row counts match the archive

```bash
databricks experimental aitools tools query \
  "SELECT YEAR(event_date) AS yr, COUNT(*) AS cnt
   FROM <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA>.claims_unified
   GROUP BY 1 ORDER BY 1" \
  --profile <PROFILE>
```

**Expect:**
- Row counts per year match the `record_count` values from step 2.
- This is the core round-trip assertion: source rows archived → rehydrated views → same counts.

---

### 7. Verify rehydration audit log

```bash
databricks experimental aitools tools query \
  "SELECT archive_path, source_table, target_catalog, target_schema, years, tables_created, status, error_message, created_at
   FROM <REHYDRATION_AUDIT_TABLE>
   ORDER BY created_at DESC
   LIMIT 5" \
  --profile <PROFILE>
```

**Expect:**
- Latest row: `status` = **COMPLETED**, `tables_created` = **2**, `error_message` is null.
- `archive_path` matches `<ARCHIVE_VOL>`, `source_table` matches `<SOURCE_CATALOG>.<SOURCE_SCHEMA>.claims`.

---

### 8. Cleanup

```bash
databricks experimental aitools tools query \
  "DROP SCHEMA IF EXISTS <SOURCE_CATALOG>.<REHYDRATE_TARGET_SCHEMA> CASCADE" \
  --profile <PROFILE>
```

**Expect:** Schema dropped, no leftover objects.
