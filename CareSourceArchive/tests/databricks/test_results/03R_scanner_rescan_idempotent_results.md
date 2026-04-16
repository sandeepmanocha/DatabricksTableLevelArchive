# 03 — Scanner Re-scan (Idempotent) Results

## Run — 2026-04-12 17:31 CDT

**TL;DR:** Re-scan idempotent — PASS. Row count unchanged at 14, scan_run_id updated for 12 tables, 2 manual entries preserved. No duplicates, no watermark/is_active drift.

**Branch:** `feat/delta_config_build_v3_code_reduce` (post secret-scope removal — commit `04d8fcb`)
**Profile:** DEFAULT

### Before State

| Metric | Value |
|--------|-------|
| scanner_log rows | 42 |
| table_configs rows | 14 |
| Previous scan_run_id (12 tables) | `66cc37f7-267d-4e44-846d-8c0f75b785aa` |
| Previous scan_run_id (claims, providers — manual) | `a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b` |

### Step 1 — Re-run scanner: **PASS**

- Run URL: https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/77161359918706
- Status: TERMINATED SUCCESS
- Duration: ~60 seconds

### Step 2 — Verify table_configs unchanged: **PASS**

| table_id | watermark_column | is_active | scan_run_id |
|---|---|---|---|
| ...bronze_column_lineage | event_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...bronze_query_history | start_time | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...bronze_table_lineage | event_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...claims | event_date | true | a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b |
| ...gold_column_usage | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_consumer_summary | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_daily_access_trends | query_date | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_impact_blast_radius | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_table_access_summary | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...gold_table_lineage_paths | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...members | | false | d5138b61-da92-4021-9f37-405383ca93ce |
| ...providers | effective_date | true | a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b |
| ...silver_query_table_access | start_time | true | d5138b61-da92-4021-9f37-405383ca93ce |
| ...silver_table_dependencies | | false | d5138b61-da92-4021-9f37-405383ca93ce |

**Verification checklist:**
- Same row count (14 before → 14 after): **PASS**
- scan_run_id updated to `d5138b61...` for 12 scanner-managed tables: **PASS**
- claims & providers retained `a03b2884...` (manual entries, merge_action = preserved): **PASS**
- merge_action in scanner_log = "updated" for 12 tables, "preserved" for 2: **PASS**
- watermark_column and is_active unchanged: **PASS**
- No duplicate table_id entries: **PASS**

## What Happened

Re-running the scanner after a full deploy produced identical table_configs — same 14 rows, same watermark columns, same active status. The scanner correctly used MERGE with "updated" action for scanner-managed tables and "preserved" for manually modified entries. Idempotency confirmed.

## Next Steps

- Proceed to 04T (archive dry run)

---

## Run — 2026-04-10 12:53 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

### Before — Baseline

| Metric | Value |
|--------|-------|
| scanner_log rows | 14 |
| table_configs rows | 14 |
| scan_run_id (all rows) | `502ed67e-0253-4f4b-bfbb-2bbacc0d7e7a` |

---

### Step 1 — Re-run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/286903978633816 |

---

### Step 2 — Verify Idempotency

#### Row count unchanged

| Table | Before | After | Result |
|-------|--------|-------|--------|
| table_configs | 14 | 14 | **PASS** |

#### scan_run_id updated

| Field | Value | Result |
|-------|-------|--------|
| Old scan_run_id | `502ed67e-0253-4f4b-bfbb-2bbacc0d7e7a` | — |
| New scan_run_id | `a03b2884-0cb5-4ac8-8bdb-1e1acdaf858b` | **PASS** — changed |
| All 14 rows have new ID | Yes | **PASS** |

#### merge_action = "updated" (not "added")

All 14 scanner_log entries from this run have `merge_action = "updated"`. **PASS**

#### watermark_column and is_active unchanged

| Table | Watermark | Active | Result |
|-------|-----------|--------|--------|
| bronze_column_lineage | event_date | true | **PASS** |
| bronze_query_history | start_time | true | **PASS** |
| bronze_table_lineage | event_date | true | **PASS** |
| claims | event_date | true | **PASS** |
| gold_column_usage | — | false | **PASS** |
| gold_consumer_summary | — | false | **PASS** |
| gold_daily_access_trends | query_date | true | **PASS** |
| gold_impact_blast_radius | — | false | **PASS** |
| gold_table_access_summary | — | false | **PASS** |
| gold_table_lineage_paths | — | false | **PASS** |
| members | — | false | **PASS** |
| providers | — | false | **PASS** |
| silver_query_table_access | start_time | true | **PASS** |
| silver_table_dependencies | — | false | **PASS** |

> **Note:** `members` and `providers` now show `is_active=false` with no watermark — differs from the 2026-04-07 run where they were active. This reflects code changes between runs; within this run, values are unchanged by rescan.

#### No duplicate table_id entries

Duplicate check query returned empty — **PASS**

#### scanner_log growth

| Metric | Value |
|--------|-------|
| scanner_log before | 14 |
| scanner_log after | 28 (+14 new entries) |

---

### Overall Result: PASS

Re-scanning is fully idempotent. The scanner updated all 14 existing configs with a new `scan_run_id` without creating duplicates or changing `watermark_column` / `is_active` values. All `merge_action` values were "updated" (not "added").

---

## Run — 2026-04-07 12:23 CDT (previous)

**Test Date:** 2026-04-07
**Test Time:** 12:23 – 12:24 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Before — Baseline

| Metric | Value |
|--------|-------|
| scanner_log rows | 28 |
| table_configs rows | 14 |
| scan_run_id (all rows) | `fb097bd0-a64c-4252-92de-72a2ff4b9746` |

---

## Step 1 — Re-run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~52 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/914988743708788 |

---

## Step 2 — Verify Idempotency

### Row count unchanged

| Table | Before | After | Result |
|-------|--------|-------|--------|
| table_configs | 14 | 14 | **PASS** |

### scan_run_id updated

| Field | Value | Result |
|-------|-------|--------|
| Old scan_run_id | `fb097bd0-a64c-4252-92de-72a2ff4b9746` | — |
| New scan_run_id | `5d56e8b8-85c3-4f51-bd47-65456f81a2c0` | **PASS** — changed |
| All 14 rows have new ID | Yes | **PASS** |

### merge_action = "updated" (not "added")

All 14 scanner_log entries from this run have `merge_action = "updated"`. **PASS**

### watermark_column and is_active unchanged

| Table | Watermark | Active | Changed? | Result |
|-------|-----------|--------|----------|--------|
| claims | event_date | true | No | **PASS** |
| members | start_date | true | No | **PASS** |
| providers | effective_date | true | No | **PASS** |
| bronze_column_lineage | event_date | true | No | **PASS** |
| bronze_query_history | start_time | true | No | **PASS** |
| bronze_table_lineage | event_date | true | No | **PASS** |
| gold_daily_access_trends | query_date | true | No | **PASS** |
| silver_query_table_access | start_time | true | No | **PASS** |
| gold_column_usage | — | false | No | **PASS** |
| gold_consumer_summary | — | false | No | **PASS** |
| gold_impact_blast_radius | — | false | No | **PASS** |
| gold_table_access_summary | — | false | No | **PASS** |
| gold_table_lineage_paths | — | false | No | **PASS** |
| silver_table_dependencies | — | false | No | **PASS** |

### No duplicate table_id entries

Duplicate check query returned empty — **PASS**

### scanner_log growth

| Metric | Value |
|--------|-------|
| scanner_log before | 28 |
| scanner_log after | 42 (+14 new entries) |

---

## Overall Result: PASS

Re-scanning is fully idempotent. The scanner updated all 14 existing configs with a new `scan_run_id` without creating duplicates or changing `watermark_column` / `is_active` values. All `merge_action` values were "updated" (not "added").
