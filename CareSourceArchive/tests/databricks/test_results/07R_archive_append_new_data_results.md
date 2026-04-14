# 07 — Archive Append (new data above watermark) Results

## Run — 2026-04-10 13:35 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

### Before — Current Watermarks

| Table | Year | Watermark |
|-------|------|-----------|
| claims | 2020 | 2020-12-31 |
| claims | 2021 | 2021-12-30 |

---

### Step 1 — Insert New Claims

**Attempt 1 — year 2020 (FAIL):** Inserted 2 rows with `event_date` 2020-12-30 and 2020-12-31. Neither is strictly after the existing watermark (2020-12-31), so the archiver correctly SKIPPED year 2020. The test plan's hardcoded dates assumed an earlier watermark — confirms the archiver uses strict `>` watermark comparison, not `>=`.

**Attempt 2 — year 2021 (success):** Watermark for 2021 is `2021-12-30`, so `2021-12-31` is strictly after.

```sql
INSERT INTO sandeep_manocha.caresource_data_samples.claims VALUES
  ('CLM-NEW-003', 'MBR-00003', 'PRV-0003', 'Medical', 'J06.9', 175.00, 'Closed', DATE'2021-12-31', current_timestamp()),
  ('CLM-NEW-004', 'MBR-00004', 'PRV-0004', 'Dental',  'E11.9', 225.00, 'Active', DATE'2021-12-31', current_timestamp())
```

| Field | Value |
|-------|-------|
| Rows inserted | 2 |
| Target year | 2021 |
| event_date | 2021-12-31 (after watermark 2021-12-30) |

---

### Step 2 — Re-run Archive

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~129 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/541575007635836 |

---

### Step 3 — Check Audit Log

#### Claims 2021 — APPEND

| Field | Before | After | Result |
|-------|--------|-------|--------|
| status | — | ARCHIVED | **PASS** |
| archive_mode | — | APPEND | **PASS** |
| record_count | 623 | 625 (+2 new rows) | **PASS** |
| watermark_value | 2021-12-30 | 2021-12-31 | **PASS** — advanced by 1 day |

#### All other table+years — SKIPPED (23 entries)

| Table | Years Skipped |
|-------|--------------|
| claims | 2018, 2019, 2020, 2022–2025 (7) |
| members | 2019–2025 (7) |
| providers | 2020–2025 (6) |
| bronze_table_lineage | 2026 (1) |
| gold_daily_access_trends | 2026 (1) |

Note: `silver_query_table_access` had APPEND (2 entries) due to new lineage data accumulating between runs — same behavior as test 06.

#### silver_query_table_access — APPEND (new lineage data)

| Year | Record Count | Watermark | Mode |
|------|-------------|-----------|------|
| 2025 | 7,180 | 2025-12-31 | APPEND |
| 2026 | 8,622,514 | 2026-03-31 | APPEND |

---

### Observations

- The archiver uses **strict `>` comparison** against the stored watermark, not `>=`. Rows at or below the watermark are not detected as new data.
- APPEND `record_count` = 625 = all rows in the year partition (623 original + 2 new), suggesting the archiver re-writes the full year partition on APPEND.

---

### Overall Result: PASS

Inserting new rows above the watermark correctly triggered APPEND mode for claims/2021 only. The watermark advanced from `2021-12-30` to `2021-12-31`. Record count updated from 623 to 625. All other claims years correctly skipped.

---
---

**Test Date:** 2026-04-07
**Test Time:** 12:49 – 12:52 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Before — Current Watermark

| Table | Year | Watermark |
|-------|------|-----------|
| claims | 2020 | 2020-12-30 |

---

## Step 1 — Insert New Claims for Year 2020

```sql
INSERT INTO sandeep_manocha.caresource_data_samples.claims VALUES
  ('CLM-NEW-001', ..., DATE'2020-12-30', ...),
  ('CLM-NEW-002', ..., DATE'2020-12-31', ...)
```

| Field | Value |
|-------|-------|
| Rows inserted | 2 |
| Dates | 2020-12-30 (at watermark), 2020-12-31 (above watermark) |

---

## Step 2 — Re-run Archive

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~138 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/932269194530558 |

---

## Step 3 — Check Audit Log

### Claims 2020 — APPEND

| Field | Value | Result |
|-------|-------|--------|
| status | ARCHIVED | **PASS** |
| archive_mode | APPEND | **PASS** |
| record_count | 624 | **PASS** — includes new rows above old watermark |
| watermark_value | 2020-12-31 | **PASS** — updated from 2020-12-30 |

### All other table+years — SKIPPED

All 24 remaining entries have `status = SKIPPED`, `archive_mode = SKIP`, `record_count = 0`. **PASS**

| Table | Years Skipped |
|-------|--------------|
| claims | 2018, 2019, 2021–2025 (7) |
| members | 2019–2025 (7) |
| providers | 2020–2025 (6) |
| bronze_table_lineage | 2026 (1) |
| gold_daily_access_trends | 2026 (1) |
| silver_query_table_access | 2025–2026 (2) |

---

## Overall Result: PASS

Inserting new rows above the watermark correctly triggered APPEND mode for claims/2020 only. The watermark advanced from `2020-12-30` to `2020-12-31`. All other table+year combinations were correctly skipped.
