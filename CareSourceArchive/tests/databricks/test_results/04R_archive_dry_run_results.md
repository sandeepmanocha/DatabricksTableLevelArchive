# 04 — Archive Dry Run — Results

## Run — 2026-04-15 16:19 CDT

**TL;DR:** Archive dry run FAILED — all 3 ForEach iterations failed before audit logging. Expected failure: SP `caresource-archive-dev` lacks Volume permissions on `fe-sandbox-manocha`. Source data untouched. Validates that `ArchiveError.is_not_found` refactor properly surfaces permission errors instead of silently swallowing them.

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** fe-sandbox-manocha
**Target:** dev-serverless
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Pre-flight State

| Metric | Value |
|--------|-------|
| archive_audit_log rows | 21 |
| last archive_run_id | de6723ea-63cd-461f-9e3e-a65ec137156d |
| table_configs active | 3 (claims, members, providers) |
| Source: claims | 5,000 rows |
| Source: members | 3,000 rows |
| Source: providers | 1,000 rows |

All 3 tables active with valid watermark columns (event_date, start_date, effective_date).

### Step 1 — Run archive dry run: **FAIL**

```
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table=dev2_archive.metadata.global_settings,dry_run=true
```

- Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/917535404414134/run/816531891254881
- Status: INTERNAL_ERROR FAILED
- Duration: ~95 seconds
- `generate_parameters` task: SUCCESS
- `run_archive` ForEach task: FAILED — all 3 iterations failed

| ForEach Stat | Value |
|---|---|
| total_iterations | 3 |
| succeeded_iterations | 0 |
| failed_iterations | 3 |
| error_message | Workload failed, see run output for details |
| termination_category | RunExecutionError |

The job runs as SP `caresource-archive-dev` (application ID `ac94d080-96a0-4866-a720-3c60ab629326`), which does not have `READ VOLUME` / `WRITE VOLUME` grants on the archive volume. The `generate_parameters` task succeeded (reads table metadata only), but each `run_archive_iteration` failed when attempting to check/write the archive volume path.

### Step 2 — Check audit log: **FAIL**

No new audit entries were written. Count remains 21 (same as before). The failure occurs before the archiver reaches the audit-logging stage — it fails during the `archive_folder_exists` check when the SP gets a `PERMISSION_DENIED` error accessing the Volume.

With the `ArchiveError.is_not_found` refactor (from plan `move_is_not_found_to_archiveerror`), `PERMISSION_DENIED` is correctly **not** classified as a "not found" error. The exception propagates and the task fails visibly — which is the desired behavior. Previously, a bare `except` would have silently returned `False` and the archiver would have continued with incorrect assumptions.

### Step 3 — Verify source data untouched: **PASS**

| Table | Before | After |
|-------|--------|-------|
| claims | 5,000 | 5,000 |
| members | 3,000 | 3,000 |
| providers | 1,000 | 1,000 |

Source data unchanged. The failure occurred before any data modification.

## What Happened

Deployed the bundle with the `ArchiveError.is_not_found` refactor to `fe-sandbox-manocha` workspace (dev-serverless target). The SP `caresource-archive-dev` exists and is ACTIVE but lacks Volume permissions. The scanner passed (it only reads table metadata). The archive dry run failed all 3 iterations because the SP cannot access the archive volume. The `is_not_found` refactor correctly surfaces the `PERMISSION_DENIED` error instead of silently treating it as "path not found."

## Next Steps

- **Grant Volume permissions** to the SP: `GRANT READ VOLUME, WRITE VOLUME ON VOLUME dev2_archive.<schema>.<volume> TO caresource-archive-dev`
- After granting permissions, re-run this test (04T) — expect PASS with DRY_RUN audit entries
- Alternatively, document this as a required setup step in the service-principals runbook

---

## Run — 2026-04-12 17:33 CDT

**TL;DR:** Dry run succeeded. 16 DRY_RUN entries created across 6 active tables. Source data untouched. Post secret-scope removal code works end-to-end.

**Branch:** `feat/delta_config_build_v3_code_reduce` (post secret-scope removal — commit `04d8fcb`)
**Profile:** DEFAULT
**archive_run_id:** `00ef5b28-b036-487a-b020-9d8c0ee3c594`

### Pre-flight Check

| Check | Result |
|-------|--------|
| Source tables have data | claims=5,008, members=3,000, providers=5 |
| archive_audit_log baseline | 267 entries, last_run `effa5281...` |
| Prior ARCHIVED entries | claims(9), members(7), providers(6+6 deleted), bronze_table_lineage(1), gold_daily_access_trends(1), silver_query_table_access(8) |
| Prior DRY_RUN entries | Yes — harmless, from earlier test runs |
| Prior STARTED entries | Yes — stale from prior runs |
| table_configs active status | claims ✓ (event_date), providers ✓ (effective_date), members ✗ (inactive) |
| 7 active tables total | bronze_column_lineage, bronze_query_history, bronze_table_lineage, claims, gold_daily_access_trends, providers, silver_query_table_access |

### Step 1 — Run archive in dry run mode: **PASS**

```
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="caresource_data_samples"
```

- Run URL: https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/938731712083512
- Status: TERMINATED SUCCESS
- Duration: ~125 seconds

### Step 2 — Check audit log for DRY_RUN entries: **PASS**

| table_name | year | status | record_count |
|---|---|---|---|
| ...bronze_column_lineage | 2026 | DRY_RUN | 290,984,512 |
| ...bronze_query_history | 2025 | DRY_RUN | 37,207 |
| ...bronze_query_history | 2026 | DRY_RUN | 37,761,839 |
| ...bronze_table_lineage | 2026 | DRY_RUN | 0 |
| ...claims | 2018 | DRY_RUN | 0 |
| ...claims | 2019 | DRY_RUN | 0 |
| ...claims | 2020 | DRY_RUN | 0 |
| ...claims | 2021 | DRY_RUN | 0 |
| ...claims | 2022 | DRY_RUN | 0 |
| ...claims | 2023 | DRY_RUN | 0 |
| ...claims | 2024 | DRY_RUN | 0 |
| ...claims | 2025 | DRY_RUN | 0 |
| ...claims | 2026 | DRY_RUN | 0 |
| ...gold_daily_access_trends | 2026 | DRY_RUN | 0 |
| ...silver_query_table_access | 2025 | DRY_RUN | 1,795 |
| ...silver_query_table_access | 2026 | DRY_RUN | 105,856 |

**Observations:**
- All 16 entries have `status = DRY_RUN` ✓
- 6 of 7 active tables processed (providers absent — all data already ARCHIVED_AND_DELETED in prior runs)
- `members` correctly skipped (is_active = false) ✓
- `claims` shows 0 records for all years — prior live archives removed eligible data
- Significant eligible data in: bronze_column_lineage (291M), bronze_query_history (37.8M), silver_query_table_access (108K)

### Step 3 — Verify source data untouched: **PASS**

| Table | Before | After |
|-------|--------|-------|
| claims | 5,008 | 5,008 |
| members | 3,000 | 3,000 |
| providers | 5 | 5 |

Source row counts identical — dry run modified no data. ✓

## What Happened

Archive dry run completed successfully after deploying the secret-scope removal changes. The archiver processed 6 of the 7 active tables in table_configs, creating 16 DRY_RUN audit entries. Providers was skipped because all its data was already archived and deleted in prior test runs. Members was correctly excluded (is_active = false). Claims shows 0 eligible records across all years — prior live archive runs already processed all archivable data. The bronze and silver system tables show substantial eligible record counts. No source data was modified.

## Next Steps

- Proceed to 05T (archive live create) if needed
- Note: bronze_column_lineage has 291M eligible rows — a live archive of this table will take significant time/resources

---

## Run — 2026-04-10 12:56

**Date:** 2026-04-10
**Run ID:** `41232e99-4a59-436b-b63b-e51ccdaf8ae2`
**Job URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/512065461168965
**Status:** PASS

---

### Context

Fresh environment — audit log was empty (0 rows) before this run. The scanner had populated `table_configs` but no archives had been created yet. `members` and `providers` are `is_active = false` in `table_configs`, so they are correctly excluded.

Since no prior archives exist, the dry run predicts **CREATE** for every table+year — the same action a live run would take on first execution.

---

### Before

| Metric | Value |
|--------|-------|
| Audit log rows | 0 |
| claims count | 5,002 |
| members count | 3,000 |
| providers count | 1,005 |

### Dry Run Results

**15 DRY_RUN entries** logged across **6 tables**. All show `action = CREATE`, `would_archive = record_count`.

#### claims (8 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2018 | CREATE | 623 | 623 |
| 2019 | CREATE | 624 | 624 |
| 2020 | CREATE | 625 | 625 |
| 2021 | CREATE | 623 | 623 |
| 2022 | CREATE | 624 | 624 |
| 2023 | CREATE | 624 | 624 |
| 2024 | CREATE | 622 | 622 |
| 2025 | CREATE | 622 | 622 |

#### bronze_column_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 290,984,512 | 290,984,512 |

#### bronze_query_history (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | CREATE | 37,207 | 37,207 |
| 2026 | CREATE | 37,761,839 | 37,761,839 |

#### bronze_table_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 30,010,157 | 30,010,157 |

#### gold_daily_access_trends (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 60,709 | 60,709 |

#### silver_query_table_access (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | CREATE | 1,795 | 1,795 |
| 2026 | CREATE | 8,304,946 | 8,304,946 |

#### Not included

- **members** — `is_active = false` in `table_configs`
- **providers** — `is_active = false` in `table_configs`
- Various gold tables (`gold_column_usage`, `gold_consumer_summary`, etc.) — `is_active = false`

### After

| Metric | Value |
|--------|-------|
| Audit log rows | 15 (+15 DRY_RUN) |
| claims count | 5,002 (unchanged) |
| members count | 3,000 (unchanged) |
| providers count | 1,005 (unchanged) |

### Verification

1. **`action` field present** — every `conditions_applied` JSON includes `"action": "CREATE"` (correct for a fresh environment with no prior archives).
2. **`would_archive` = `total_eligible`** — correctly reflects that all eligible rows would be archived on a live CREATE run.
3. **Source data untouched** — all three table counts match the before state exactly.
4. **No archive folders created** — dry run mode only queries, never writes.
5. **Only DRY_RUN entries** — the run produced only DRY_RUN audit rows, no live operations.

---

## Run — 2026-04-07 (previous)

**Date:** 2026-04-07
**Run ID:** `b7209f76-b411-41fe-b8bd-56d534030f33`
**Job URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1099249755759883
**Status:** PASS

---

## Context

This dry run was the first after the **Dry Run Action Prediction** refactor:
- `_resolve_year_action` now determines `CREATE / APPEND / SKIP / RESUME_DELETE / ERROR`
- `_dry_run_year` calls `_resolve_year_action` so dry runs predict the same action a live run would take
- The `action` field is now included in the audit `conditions_applied` JSON and in the notebook summary

Since prior tests (05–10) already archived all eligible years, this dry run correctly predicts **SKIP** for every table+year — the same result a live run would produce.

---

## Before

| Metric | Value |
|--------|-------|
| Audit log rows | 192 |
| Last run ID | `d08564fc-b783-439f-8b5b-56dd691015f4` |
| claims count | 5,002 |
| members count | 3,000 |
| providers count | 5 |

## Dry Run Results

**19 DRY_RUN entries** logged across **5 tables**. All show `action = SKIP`, `would_archive = 0`.

### claims (8 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2018 | SKIP | 623 | 0 |
| 2019 | SKIP | 624 | 0 |
| 2020 | SKIP | 625 | 0 |
| 2021 | SKIP | 623 | 0 |
| 2022 | SKIP | 624 | 0 |
| 2023 | SKIP | 624 | 0 |
| 2024 | SKIP | 622 | 0 |
| 2025 | SKIP | 622 | 0 |

### members (7 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2019 | SKIP | 426 | 0 |
| 2020 | SKIP | 426 | 0 |
| 2021 | SKIP | 427 | 0 |
| 2022 | SKIP | 428 | 0 |
| 2023 | SKIP | 429 | 0 |
| 2024 | SKIP | 426 | 0 |
| 2025 | SKIP | 428 | 0 |

### bronze_table_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | SKIP | 30,010,157 | 0 |

### gold_daily_access_trends (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | SKIP | 60,709 | 0 |

### silver_query_table_access (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | SKIP | 1,795 | 0 |
| 2026 | SKIP | 8,304,946 | 0 |

### Not included

- **providers** — only 5 source rows remain (deleted in test 09), all in recent years beyond the retention cutoff.
- **bronze_column_lineage**, **bronze_query_history** — not eligible or not configured as active.

## After

| Metric | Value |
|--------|-------|
| Audit log rows | 211 (+19 DRY_RUN) |
| claims count | 5,002 (unchanged) |
| members count | 3,000 (unchanged) |
| providers count | 5 (unchanged) |

## Verification

1. **`action` field present** — every `conditions_applied` JSON includes `"action": "SKIP"` (the new field from the refactor).
2. **`would_archive = 0`** — correctly reflects that existing archives cover all data; a live run would skip these years.
3. **Source data untouched** — all three table counts match the before state exactly.
4. **No archive folders created** — dry run mode only queries, never writes.
5. **No non-DRY_RUN entries** — the run produced only DRY_RUN audit rows.
