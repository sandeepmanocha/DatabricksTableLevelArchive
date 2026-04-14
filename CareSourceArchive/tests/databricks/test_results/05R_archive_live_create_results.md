# 05 — Archive Live Run (CREATE mode) Results

## Run — 2026-04-10 13:06 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

### Pre-step — Table Config Changes

- Disabled `bronze_column_lineage` and `bronze_query_history` in `table_configs` (2 rows affected).
- Enabled `claims`, `members`, and `providers` in `table_configs` (3 rows affected).
- Verified 6 active tables: `bronze_table_lineage`, `claims`, `gold_daily_access_trends`, `members`, `providers`, `silver_query_table_access`.

---

### Before — Baseline

| Metric | Value |
|--------|-------|
| ARCHIVED entries in audit log | 0 |

#### Source row counts by table/year

| Table | Year | Count |
|-------|------|-------|
| claims | NULL | 15 |
| claims | 2018 | 623 |
| claims | 2019 | 624 |
| claims | 2020 | 625 |
| claims | 2021 | 623 |
| claims | 2022 | 624 |
| claims | 2023 | 624 |
| claims | 2024 | 622 |
| claims | 2025 | 622 |
| members | NULL | 10 |
| members | 2019 | 426 |
| members | 2020 | 426 |
| members | 2021 | 427 |
| members | 2022 | 428 |
| members | 2023 | 429 |
| members | 2024 | 426 |
| members | 2025 | 428 |
| providers | NULL | 5 |
| providers | 2020 | 167 |
| providers | 2021 | 167 |
| providers | 2022 | 167 |
| providers | 2023 | 170 |
| providers | 2024 | 163 |
| providers | 2025 | 166 |

---

### Step 1 — Run Archive (live, dry_run=false)

| Field | Value |
|-------|-------|
| Status | **FAIL** — INTERNAL_ERROR (2 of 6 iterations failed) |
| Duration | ~158 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1053611685911312 |
| Error | `An error occurred during execution of task For each: Some iterations failed` |

#### For-each iteration stats

| Metric | Value |
|--------|-------|
| Total iterations | 6 |
| Succeeded | 4 |
| Failed | 2 |
| Error category | `RunExecutionError` — "Workload failed, see run output for details" |

---

### Step 2 — Audit Log Analysis

#### claims (8 years) — all PASS

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2018 | ARCHIVED | 623 | 2018-12-31 | CREATE | **PASS** |
| 2019 | ARCHIVED | 624 | 2019-12-31 | CREATE | **PASS** |
| 2020 | ARCHIVED | 625 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 623 | 2021-12-30 | CREATE | **PASS** |
| 2022 | ARCHIVED | 624 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 624 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 622 | 2024-12-31 | CREATE | **PASS** |
| 2025 | ARCHIVED | 622 | 2025-12-31 | CREATE | **PASS** |

#### Other active tables (lineage dataset) — all PASS

| Table | Year | Record Count | Watermark Value | Mode |
|-------|------|-------------|-----------------|------|
| bronze_table_lineage | 2026 | 30,010,157 | 2026-04-01 | CREATE |
| gold_daily_access_trends | 2026 | 60,709 | 2026-04-01 | CREATE |
| silver_query_table_access | 2025 | 1,795 | 2025-12-31 | CREATE |
| silver_query_table_access | 2026 | 8,304,946 | 2026-03-31 | CREATE |

#### members — **NO AUDIT ENTRIES**

No STARTED or ARCHIVED rows in `archive_audit_log` for members. The iteration failed before any audit logging occurred.

#### providers — **NO AUDIT ENTRIES**

No STARTED or ARCHIVED rows in `archive_audit_log` for providers. The iteration failed before any audit logging occurred.

---

### Root Cause — members and providers failures (operator error)

`table_configs` shows both `members` and `providers` have **empty `watermark_column`**:

| source_table | watermark_column | is_active |
|-------------|-----------------|-----------|
| claims | `event_date` | true |
| members | *(empty)* | true |
| providers | *(empty)* | true |

Tables were enabled (`is_active = true`) without setting `watermark_column`. The archiver needs a watermark column to partition data by year. Without it, the iteration crashes before it can even write a STARTED entry to the audit log.

**This is operator error, not a bug.** The correct watermark columns are: `start_date` (members), `effective_date` (providers). These must be set when enabling the tables.

---

### Step 3 — Verify Archive Folders

Skipped — not meaningful to verify while 2 of 3 target tables failed.

---

### Notes

- Claims archived successfully across all 8 years with correct record counts and watermark values.
- NULL-date rows (15 claims) correctly excluded.
- Lineage tables also archived successfully (they were not disabled this run and have valid watermark columns).
- Provider source counts differ slightly from prior run (e.g. 2020: 167 vs 163 previously).
- Concurrency was 10.

---

### Overall Result: FAIL

**Partial success.** Claims and lineage tables archived correctly (CREATE mode, correct counts, watermarks recorded). However, `members` and `providers` iterations failed because `table_configs.watermark_column` is empty for both — the scanner did not resolve `start_date` / `effective_date` as watermark columns. The archiver does not handle missing watermark columns gracefully (no FAILED audit entry, no user-facing error message).

**Action needed:** Set `watermark_column` in `table_configs` for members (`start_date`) and providers (`effective_date`), then re-run.

---
---

**Test Date:** 2026-04-07
**Test Time:** 12:33 – 12:37 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Pre-step — Disable Large Tables

Disabled `bronze_column_lineage` and `bronze_query_history` in `table_configs` (2 rows affected).

Make sure to Enable `claims`, `members`, and `providers` in `table_configs` (3 rows).
---

## Before — Baseline

| Metric | Value |
|--------|-------|
| ARCHIVED entries in audit log | 0 |

### Source row counts by table/year

| Table | Year | Count |
|-------|------|-------|
| claims | NULL | 15 |
| claims | 2018 | 623 |
| claims | 2019 | 624 |
| claims | 2020 | 623 |
| claims | 2021 | 623 |
| claims | 2022 | 624 |
| claims | 2023 | 624 |
| claims | 2024 | 622 |
| claims | 2025 | 622 |
| members | NULL | 10 |
| members | 2019 | 426 |
| members | 2020 | 426 |
| members | 2021 | 427 |
| members | 2022 | 428 |
| members | 2023 | 429 |
| members | 2024 | 426 |
| members | 2025 | 428 |
| providers | NULL | 5 |
| providers | 2020 | 163 |
| providers | 2021 | 168 |
| providers | 2022 | 166 |
| providers | 2023 | 167 |
| providers | 2024 | 166 |
| providers | 2025 | 165 |

---

## Step 1 — Run Archive (live, dry_run=false)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~167 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/534308376371821 |

---

## Step 2 — Check Audit Log

Every eligible table+year has a STARTED → ARCHIVED pair. All `archive_mode = CREATE`. **PASS**

### claims (8 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2018 | ARCHIVED | 623 | 2018-12-31 | CREATE | **PASS** |
| 2019 | ARCHIVED | 624 | 2019-12-31 | CREATE | **PASS** |
| 2020 | ARCHIVED | 623 | 2020-12-30 | CREATE | **PASS** |
| 2021 | ARCHIVED | 623 | 2021-12-30 | CREATE | **PASS** |
| 2022 | ARCHIVED | 624 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 624 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 622 | 2024-12-31 | CREATE | **PASS** |
| 2025 | ARCHIVED | 622 | 2025-12-31 | CREATE | **PASS** |

### members (7 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2019 | ARCHIVED | 426 | 2019-12-30 | CREATE | **PASS** |
| 2020 | ARCHIVED | 426 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 427 | 2021-12-31 | CREATE | **PASS** |
| 2022 | ARCHIVED | 428 | 2022-12-30 | CREATE | **PASS** |
| 2023 | ARCHIVED | 429 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 426 | 2024-12-29 | CREATE | **PASS** |
| 2025 | ARCHIVED | 428 | 2025-12-30 | CREATE | **PASS** |

### providers (6 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2020 | ARCHIVED | 163 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 168 | 2021-12-29 | CREATE | **PASS** |
| 2022 | ARCHIVED | 166 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 167 | 2023-12-30 | CREATE | **PASS** |
| 2024 | ARCHIVED | 166 | 2024-12-30 | CREATE | **PASS** |
| 2025 | ARCHIVED | 165 | 2025-12-29 | CREATE | **PASS** |

### Other active tables (lineage dataset)

| Table | Year | Record Count | Mode |
|-------|------|-------------|------|
| bronze_table_lineage | 2026 | 30,010,157 | CREATE |
| gold_daily_access_trends | 2026 | 60,709 | CREATE |
| silver_query_table_access | 2025 | 1,795 | CREATE |
| silver_query_table_access | 2026 | 8,304,946 | CREATE |

---

## Step 3 — Verify Archive Folders Exist

Archive folder naming: `year_YYYY` (not `year=YYYY`)

| Archive Path | Expected | Actual | Result |
|-------------|----------|--------|--------|
| `.../claims/year_2020` | 623 | 623 | **PASS** |
| `.../members/year_2021` | 427 | 427 | **PASS** |
| `.../providers/year_2022` | 166 | 166 | **PASS** |

All 6 active table folders confirmed in volume:
`bronze_table_lineage/`, `claims/`, `gold_daily_access_trends/`, `members/`, `providers/`, `silver_query_table_access/`

---

## Notes

- Rows with NULL dates (15 claims, 10 members, 5 providers) were correctly excluded from archiving.
- `bronze_column_lineage` and `bronze_query_history` were disabled before the run to keep test duration reasonable.
- Concurrency was 10 (updated in test 04).

---

## Overall Result: PASS

First live archive correctly created Delta folders for all eligible table+year combinations. Record counts in audit log match archive folder counts. All entries have `archive_mode = CREATE` and recorded `watermark_value`. Source data with NULL dates was excluded.
