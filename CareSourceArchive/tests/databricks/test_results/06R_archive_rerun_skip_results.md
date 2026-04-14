# 06 — Archive Re-run (SKIP — no new data) Results

## Run — 2026-04-10 13:23 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Run ID:** `2fbb9354-06cb-431c-a2ea-a2d0cd51e804`

---

### Context

Test 05 (April 10) failed for `members` and `providers` because `watermark_column` was empty. Those columns were subsequently set (`start_date` for members, `effective_date` for providers). This re-run is therefore the first successful archive for those two tables, and `silver_query_table_access` accumulated new lineage data between test 05 and this run.

---

### Step 1 — Re-run Archive (live, same params)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~146 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1004981543395453 |

---

### Step 2 — Check Audit Log

25 STARTED entries, 10 SKIPPED, 15 ARCHIVED.

#### SKIPPED entries (10) — tables already archived, no new data

| Table | Year | Status | Record Count | Mode | Result |
|-------|------|--------|-------------|------|--------|
| bronze_table_lineage | 2026 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2018 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2019 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2020 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2021 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2022 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2023 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2024 | SKIPPED | 0 | SKIP | **PASS** |
| claims | 2025 | SKIPPED | 0 | SKIP | **PASS** |
| gold_daily_access_trends | 2026 | SKIPPED | 0 | SKIP | **PASS** |

#### ARCHIVED — CREATE entries (13) — members & providers first successful archive

| Table | Year | Status | Record Count | Mode | Result |
|-------|------|--------|-------------|------|--------|
| members | 2019 | ARCHIVED | 426 | CREATE | **PASS** |
| members | 2020 | ARCHIVED | 426 | CREATE | **PASS** |
| members | 2021 | ARCHIVED | 427 | CREATE | **PASS** |
| members | 2022 | ARCHIVED | 428 | CREATE | **PASS** |
| members | 2023 | ARCHIVED | 429 | CREATE | **PASS** |
| members | 2024 | ARCHIVED | 426 | CREATE | **PASS** |
| members | 2025 | ARCHIVED | 428 | CREATE | **PASS** |
| providers | 2020 | ARCHIVED | 167 | CREATE | **PASS** |
| providers | 2021 | ARCHIVED | 167 | CREATE | **PASS** |
| providers | 2022 | ARCHIVED | 167 | CREATE | **PASS** |
| providers | 2023 | ARCHIVED | 170 | CREATE | **PASS** |
| providers | 2024 | ARCHIVED | 163 | CREATE | **PASS** |
| providers | 2025 | ARCHIVED | 166 | CREATE | **PASS** |

#### ARCHIVED — APPEND entries (2) — new lineage data since test 05

| Table | Year | Status | Record Count | Mode | Result |
|-------|------|--------|-------------|------|--------|
| silver_query_table_access | 2025 | ARCHIVED | 3,590 | APPEND | **PASS** |
| silver_query_table_access | 2026 | ARCHIVED | 8,410,802 | APPEND | **PASS** |

---

### Summary

| Behavior | Tables | Count | Result |
|----------|--------|-------|--------|
| SKIP (no new data) | claims (8), bronze_table_lineage (1), gold_daily_access_trends (1) | 10 | **PASS** |
| CREATE (first archive after watermark fix) | members (7), providers (6) | 13 | **PASS** |
| APPEND (new data arrived) | silver_query_table_access (2) | 2 | **PASS** |
| **Total** | | **25** | |

---

### Overall Result: PASS

The archiver correctly distinguishes three scenarios in a single run: SKIP for already-archived tables with no new data, CREATE for tables being archived for the first time (members/providers after watermark_column fix), and APPEND for tables with new data since the last archive (silver_query_table_access). All 25 table+year entries behaved as expected given the state left by test 05.

---
---

**Test Date:** 2026-04-07
**Test Time:** 12:43 – 12:45 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Step 1 — Re-run Archive (live, same params, no new data)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~128 sec (vs ~167 sec for CREATE run — faster) |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/850493504696708 |

---

## Step 2 — Check Audit Log

All 25 table+year combinations logged STARTED → SKIPPED. **PASS**

| Check | Expected | Actual | Result |
|-------|----------|--------|--------|
| All status values | SKIPPED | SKIPPED | **PASS** |
| All archive_mode values | SKIP | SKIP | **PASS** |
| All record_count values | 0 | 0 | **PASS** |

### Skipped entries by table

| Table | Years Skipped |
|-------|--------------|
| claims | 2018–2025 (8) |
| members | 2019–2025 (7) |
| providers | 2020–2025 (6) |
| bronze_table_lineage | 2026 (1) |
| gold_daily_access_trends | 2026 (1) |
| silver_query_table_access | 2025–2026 (2) |
| **Total** | **25 table+year entries** |

---

## Overall Result: PASS

Re-running the archive with no new data correctly skips all table+year combinations. Every entry has `status = SKIPPED`, `archive_mode = SKIP`, and `record_count = 0`. The job completed faster than the initial CREATE run (128s vs 167s).
