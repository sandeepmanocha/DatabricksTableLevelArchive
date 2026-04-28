# 32 — Delete All After Archive (Schema-Wide)

**Goal:** Validate the broadest live delete the system permits — a single run with only `source_catalog + source_schema` filters — correctly deletes every eligible archived year across every active `table_configs` row in a schema, without the operator enumerating tables or years. This is the canonical "delete everything I've archived" path and exercises the scope-guard as a usability contract, not just a safety net.

**Covers:**

- `generate_parameters.py` — scope guard accepts `source_catalog + source_schema` alone as a sufficient filter for a live delete.
- `generate_parameters.py._resolve_delete_years` — falls back to `calculate_eligible_years` when `years` widget is empty; per-table resolution.
- Multi-table ForEach fan-out on the delete job (N tables × M eligible years).
- `DeleteJob.run` (in `src/delete_job.py`, extends `ArchiveBase`) — per-`(table, year)` invocation.
- `AuditLogger.is_eligible_for_delete` — D13 eligibility enforced per `(table, year)`.
- `archive_mode = 'DELETE'` signature on every row written by the delete job (not `CREATE`, not `APPEND`).
- Source dated rows removed only for years that had a prior `ARCHIVED` row.
- Archive Deltas untouched — the delete job does not rewrite the archive.

**Depends on:** 01_setup_and_deploy (fresh test data). **Touches every table in the schema.** Do not run in parallel with 25T/29T/30T.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/32R_delete_all_after_archive_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. This test touches **every** table in `source_data_samples` (claims, members, providers). Check:
>    - **Source tables:** All three must have data per `generate_test_data` baseline. If any year is empty or the source is missing, re-run `generate_test_data` before Phase 1.
>    - **Audit log (schema-wide):** For a clean seed, all three tables' audit rows must be cleared in Phase 1 together.
>    - **Archive volume (schema-wide):** No `year_*` folders should exist under `providers/`, `claims/`, or `members/`. If folders exist from prior tests, Phase 1 clears them.
>    - **`table_configs` (all three):** `delete_after_archive = false` (Phase 1 sets it explicitly so the **dedicated delete job** path is exercised, not the inline post-archive delete).
>    - **Concurrent tests:** Confirm no other 25T/29T/30T/09T run is in flight. This test takes full ownership of the schema.
>    - **Bundle deploy:** Both `caresource_archive_run` and `caresource_delete_source_after_archive` jobs must be deployed.

**Workspace parameters.** This test was authored against `dev2_archive` catalog, `dev2_archive.metadata.*` audit/config, and the `dev-serverless` bundle target with the `fe-sandbox-manocha` profile. Substitute workspace/catalog as needed.

---

## Phase 0 — Discovery (record scope and baselines)

### 0a. List every active `table_configs` row in the schema

```sql
SELECT table_id, source_table, watermark_column, delete_after_archive, is_active
FROM dev2_archive.metadata.table_configs
WHERE source_catalog = 'dev2_archive'
  AND source_schema  = 'source_data_samples'
  AND is_active = true
ORDER BY table_id
```

**Expect:** `claims`, `members`, `providers` — three rows, all `is_active = true`, `delete_after_archive = false`. Record this list as `TARGET_TABLES`.

### 0b. Per-year source baselines

```sql
SELECT 'claims'    AS tbl, YEAR(event_date)     AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.claims    GROUP BY 1,2
UNION ALL
SELECT 'members'   AS tbl, YEAR(start_date)     AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.members   GROUP BY 1,2
UNION ALL
SELECT 'providers' AS tbl, YEAR(effective_date) AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.providers GROUP BY 1,2
ORDER BY tbl, yr
```

**Expect:** A row per `(table, year)` plus one row per table where `yr IS NULL` (these are NULL-watermark rows and are **not** touched by the per-year delete path). Record as `BASELINE_{tbl}_{yr}` and `BASELINE_{tbl}_NULL`.

### 0c. Retention window

```sql
SELECT default_retention_years FROM dev2_archive.metadata.global_settings LIMIT 1
```

**Expect:** Record as `RETENTION`. With `RETENTION = 0`, every integer year ≤ current_year is eligible. If `RETENTION > 0`, compute `ELIGIBLE_BOUNDARY = current_year - RETENTION` — only years ≤ boundary are in scope for Phase 4.

---

## Phase 1 — Clean preconditions across the schema

### 1a. Clear audit rows for all three tables

```sql
DELETE FROM dev2_archive.metadata.archive_audit_log
WHERE table_name IN (
  'dev2_archive.source_data_samples.claims',
  'dev2_archive.source_data_samples.members',
  'dev2_archive.source_data_samples.providers'
)
```

### 1b. Remove every archive folder in the schema

Look up `archive_base_path` for the schema first:

```sql
SELECT DISTINCT archive_base_path FROM dev2_archive.metadata.table_configs
WHERE source_catalog='dev2_archive' AND source_schema='source_data_samples'
```

Then remove every per-table subfolder under that path (substitute the path you find):

```bash
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims \
  --profile fe-sandbox-manocha || true
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/members \
  --profile fe-sandbox-manocha || true
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers \
  --profile fe-sandbox-manocha || true
```

> `|| true` because individual tables may not yet have a folder. The archive write in Phase 2 creates them.

### 1c. Ensure `delete_after_archive = false` for every target

```sql
UPDATE dev2_archive.metadata.table_configs
SET delete_after_archive = false,
    modified_by = 'test_32',
    modified_at = current_timestamp(),
    change_reason = 'Test 32: baseline archive without delete'
WHERE source_catalog = 'dev2_archive'
  AND source_schema  = 'source_data_samples'
```

### 1d. Restore source data

Re-run `generate_test_data` to restore any rows removed by earlier tests.

```bash
databricks bundle run generate_test_data -t dev-serverless --profile fe-sandbox-manocha
```

### 1e. Re-record baselines (after restore)

Re-run the query from **0b**. These values override the earlier `BASELINE_*` — the live run deltas in Phase 4/5 are asserted against these.

---

## Phase 2 — Seed ARCHIVED state across the entire schema

### 2a. One archive run, schema-scoped, no `table_config_filter`, no `years`

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="false" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples"
```

### 2b. Every eligible `(table, year)` has an `ARCHIVED` row with `archive_mode = 'CREATE'`

```sql
SELECT table_name, year, status, archive_mode, record_count
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'ARCHIVED'
ORDER BY table_name, year
```

**Expect:**

- One `ARCHIVED / CREATE` row per `(table, year)` for every year ≤ `ELIGIBLE_BOUNDARY` that had source rows.
- `record_count` equals `BASELINE_{tbl}_{yr}` (rows-this-run semantic; for a fresh CREATE this equals the full archived slice).
- Record the `archive_run_id` as `SEED_RUN_ID`.
- **No** `ARCHIVED_AND_DELETED` rows.

### 2c. Source unchanged

Re-run the query from **0b**. Every count must equal the value after **1e**.

### 2d. Archive Deltas match archived counts

For each `(table, year)` with an `ARCHIVED` row, verify the Delta at `/Volumes/dev2_archive/caresource_archive/caresource_archive_vol/source_data_samples/{table}/year_{yr}` has the expected row count:

```sql
SELECT COUNT(*) AS cnt
FROM delta.`/Volumes/dev2_archive/caresource_archive/caresource_archive_vol/source_data_samples/{table}/year_{yr}`
```

**Expect:** `cnt = BASELINE_{tbl}_{yr}` for every archived `(table, year)`.

---

## Phase 3 — Dry-run preview for "delete all"

### 3a. Delete job dry-run, **schema scope only**

```bash
databricks bundle run caresource_delete_source_after_archive -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="true" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples"
```

> No `years`, no `table_config_filter`. This is the recommended "Option 3" safety preview before the live Option 1 run.

### 3b. Verify `DRY_RUN / WOULD_DELETE` rows cover every archived `(table, year)`

```sql
SELECT table_name, year, status, record_count, archive_mode, archive_delta_version,
       get_json_object(conditions_applied, '$.action') AS action,
       archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'DRY_RUN'
ORDER BY table_name, year
```

**Expect:**

- One `DRY_RUN` row per `(table, year)` pair that had an `ARCHIVED` row from **Phase 2**.
- `action = 'WOULD_DELETE'` for every row.
- `record_count` equals the current source count for that `(table, year)` — same as `BASELINE_{tbl}_{yr}` because source is untouched at this point.
- `archive_mode IS NULL` and `archive_delta_version IS NULL` on every `DRY_RUN` row.
- All rows share a single `archive_run_id`. Record as `DRY_RUN_ID` — it must differ from `SEED_RUN_ID`.

### 3c. Source untouched by dry-run

Re-run the query from **0b**. Every count must equal the post-**1e** baseline.

---

## Phase 4 — Live "delete all" (Option 1: schema scope only)

### 4a. Run the delete job **live** with only `source_catalog + source_schema` set

```bash
databricks bundle run caresource_delete_source_after_archive -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings" \
  --params dry_run="false" \
  --params source_catalog="dev2_archive" \
  --params source_schema="source_data_samples"
```

**Expect:** Job succeeds end to end. The ForEach task runs one branch per target table; each branch deletes every eligible year.

### 4b. Scope guard **does not** trip

The job must reach `run_delete`; it must **not** raise `ArchiveConfigError("Delete job refused...")`. If it does, that is a regression in the guard — record the stack trace and stop.

### 4c. Audit: every archived `(table, year)` has an `ARCHIVED_AND_DELETED` row with `archive_mode = 'DELETE'`

```sql
SELECT table_name, year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 200) AS message_excerpt,
       archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'ARCHIVED_AND_DELETED'
ORDER BY table_name, year
```

**Expect:** For every `(table, year)` that appeared in **Phase 2b** as `ARCHIVED / CREATE`:

- `status = 'ARCHIVED_AND_DELETED'`
- `archive_mode = 'DELETE'` (exact string — this is the delete-job signature and must not equal `'CREATE'` or `'APPEND'`)
- `archive_delta_version IS NULL` (delete job does not rewrite the archive)
- `record_count` equals the source count that was deleted, which equals `BASELINE_{tbl}_{yr}`
- `message_excerpt` contains `"Deleted"`, the numeric count, and a reference to `SEED_RUN_ID`. Format: `Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID> at <timestamp>.`
- All rows share a single `archive_run_id`. Record as `DELETE_RUN_ID` — it must differ from both `SEED_RUN_ID` and `DRY_RUN_ID`.

### 4d. Source: every archived year is empty, NULL-year rows remain

```sql
SELECT 'claims'    AS tbl, YEAR(event_date)     AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.claims    GROUP BY 1,2
UNION ALL
SELECT 'members'   AS tbl, YEAR(start_date)     AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.members   GROUP BY 1,2
UNION ALL
SELECT 'providers' AS tbl, YEAR(effective_date) AS yr, COUNT(*) AS cnt FROM dev2_archive.source_data_samples.providers GROUP BY 1,2
ORDER BY tbl, yr
```

**Expect:**

- For every archived `(table, year)`: `cnt = 0`.
- For `yr IS NULL` on every table: `cnt = BASELINE_{tbl}_NULL` (unchanged — per-year delete does not touch NULL-watermark rows).
- No rows for years beyond `ELIGIBLE_BOUNDARY` (none existed in the baseline either).

### 4e. Archive Deltas unchanged on disk

Re-run the Phase 2d query for every archived `(table, year)`. Every `cnt` must still equal `BASELINE_{tbl}_{yr}`. The delete job removes source rows only; the archive is the durable copy.

### 4f. Sanity: original `ARCHIVED / CREATE` rows still present, owned by `SEED_RUN_ID`

```sql
SELECT table_name, year, archive_mode, archive_run_id
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name LIKE 'dev2_archive.source_data_samples.%'
  AND status = 'ARCHIVED'
ORDER BY table_name, year
```

**Expect:** Every `(table, year)` from **Phase 2b** still present with `archive_mode = 'CREATE'` and `archive_run_id = SEED_RUN_ID`. The delete job appends a new `ARCHIVED_AND_DELETED` row; it does not rewrite history.

---

## Phase 5 — Cleanup + restore

### 5a. Clear audit rows for all three tables

```sql
DELETE FROM dev2_archive.metadata.archive_audit_log
WHERE table_name IN (
  'dev2_archive.source_data_samples.claims',
  'dev2_archive.source_data_samples.members',
  'dev2_archive.source_data_samples.providers'
)
```

### 5b. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims \
  --profile fe-sandbox-manocha || true
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/members \
  --profile fe-sandbox-manocha || true
databricks fs rm -r \
  dbfs:/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers \
  --profile fe-sandbox-manocha || true
```

### 5c. Reset `delete_after_archive`

```sql
UPDATE dev2_archive.metadata.table_configs
SET delete_after_archive = false,
    modified_by = 'test_32',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 32'
WHERE source_catalog = 'dev2_archive'
  AND source_schema  = 'source_data_samples'
```

### 5d. Restore source data

```bash
databricks bundle run generate_test_data -t dev-serverless --profile fe-sandbox-manocha
```

---

## What this test proves

1. The live delete path **works without enumerating tables or years** — passing only `source_catalog + source_schema` is sufficient and safe (Phase 4).
2. The scope guard is a **usability contract**, not a gate: named scopes of any breadth are permitted; only nameless scopes are refused (Phase 3 dry-run is broader still, no filter at all, and is permitted).
3. `archive_mode = 'DELETE'` is the unambiguous signature of the dedicated delete job, distinct from `CREATE` (inline archive) and `APPEND` (inline append).
4. Archive durability holds: deleting every archived `(table, year)` does not rewrite or shrink the archive on disk (Phase 4e).
5. NULL-watermark rows are not collected by the per-year delete (Phase 4d) — confirmed as a known scope of the feature, covered separately by 17T.
