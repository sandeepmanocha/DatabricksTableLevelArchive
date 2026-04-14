# CareSource Archive E2E Test Results

**Test Date:** 2026-04-07  
**Test Time:** 16:10 – 16:17 UTC (11:10 – 11:17 CDT)  
**Profile:** DEFAULT  
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com  
**Bundle Target:** dev  
**Config Table:** `sandeep_manocha.caresource_audit.global_settings`  
**Source:** `sandeep_manocha.caresource_data_samples`  
**Audit:** `sandeep_manocha.caresource_audit`

---

## 1. Bundle Validation & Deploy

| Step | Result |
|------|--------|
| `bundle validate -t dev` | PASS — "Validation OK!" |
| `bundle deploy -t dev` | PASS — files uploaded, resources deployed |

Deployed to: `/Workspace/Users/sandeep.manocha@databricks.com/.bundle/caresource-archive/dev`

---

## 2. Log Table Snapshots (Before / After)

| Table | Before | After | Delta |
|-------|--------|-------|-------|
| `archive_audit_log` | 295 rows | 370 rows | +75 |
| `scanner_log` | 64 rows | 78 rows | +14 |

---

## 3. Scanner Run

| Field | Value |
|-------|-------|
| Status | **SUCCESS** |
| Duration | ~60 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/32061410300731 |
| New scanner_log rows | +14 (64 -> 78) |
| Scan Run ID | `d28a139b-bc57-4ae6-be61-f2ac424c2ef9` |

### Scanner Results by Table

| Table | Match Status | Matched Column | Merge Action | Active |
|-------|-------------|----------------|--------------|--------|
| bronze_column_lineage | matched | event_date | preserved | yes |
| bronze_query_history | matched | start_time | preserved | yes |
| bronze_table_lineage | matched | event_date | updated | yes |
| claims | matched | event_date | updated | yes |
| gold_column_usage | unmatched | — | updated | no |
| gold_consumer_summary | unmatched | — | updated | no |
| gold_daily_access_trends | matched | query_date | updated | yes |
| gold_impact_blast_radius | unmatched | — | updated | no |
| gold_table_access_summary | unmatched | — | updated | no |
| gold_table_lineage_paths | unmatched | — | updated | no |
| members | unmatched | — | preserved | no |
| providers | unmatched | — | preserved | no |
| silver_query_table_access | matched | start_time | updated | yes |
| silver_table_dependencies | unmatched | — | updated | no |

**Summary:** 6 tables matched (active), 8 unmatched (inactive). All merge actions completed without errors.

---

## 4. Archive Dry Run

| Field | Value |
|-------|-------|
| Status | **SUCCESS** |
| Duration | ~107 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1019071979714466 |
| New archive_audit_log rows | +25 (295 -> 320, all DRY_RUN status) |
| Archive Run ID | `5bd78ced-db7d-4e86-aa95-9027e41d5565` |

### Dry Run Results by Table/Year

| Table | Year | Would Archive |
|-------|------|---------------|
| claims | 2018 | 623 |
| claims | 2019 | 624 |
| claims | 2020 | 623 |
| claims | 2021 | 623 |
| claims | 2022 | 624 |
| claims | 2023 | 624 |
| claims | 2024 | 622 |
| claims | 2025 | 622 |
| members | 2019 | 426 |
| members | 2020 | 426 |
| members | 2021 | 427 |
| members | 2022 | 428 |
| members | 2023 | 429 |
| members | 2024 | 426 |
| members | 2025 | 428 |
| providers | 2020 | 163 |
| providers | 2021 | 168 |
| providers | 2022 | 166 |
| providers | 2023 | 167 |
| providers | 2024 | 166 |
| providers | 2025 | 165 |
| bronze_table_lineage | 2026 | 30,010,157 |
| silver_query_table_access | 2025 | 1,795 |
| silver_query_table_access | 2026 | 8,304,946 |
| gold_daily_access_trends | 2026 | 60,709 |

**Summary:** Dry run identified eligible rows across 6 tables, 25 table-year combinations. No exclusion conditions filtered any rows (`would_archive` = `total_eligible` in all cases). No null date issues detected.

---

## 5. Archive Live Run

| Field | Value |
|-------|-------|
| Status | **SUCCESS** |
| Duration | ~147 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/911136001195549 |
| New archive_audit_log rows | +50 (320 -> 370) |
| Archive Run ID | `1e5bf745-f01e-4181-bb65-b9edb4c2699c` |

### Live Archive Results

All 25 table-year combinations were processed as **STARTED** then **SKIPPED** (archive_mode = "SKIP"):

| Table | Years Processed | Outcome |
|-------|----------------|---------|
| bronze_table_lineage | 2026 | SKIPPED |
| claims | 2018–2025 (8 years) | SKIPPED |
| gold_daily_access_trends | 2026 | SKIPPED |
| members | 2019–2025 (7 years) | SKIPPED |
| providers | 2020–2025 (6 years) | SKIPPED |
| silver_query_table_access | 2025–2026 (2 years) | SKIPPED |

**Summary:** The live archive ran without errors but all tables were SKIPPED with `archive_mode=SKIP` and `record_count=0`. This means the table configurations have their archive mode set to SKIP, preventing actual data movement. Each table-year pair logged a STARTED + SKIPPED audit pair (25 pairs = 50 rows).

---

## Overall Summary

1. **Bundle:** Validates and deploys cleanly to the dev target.
2. **Scanner:** Successfully scans 14 tables in `caresource_data_samples`, correctly identifying 6 with date columns (active) and 8 without (inactive). MERGE operations work correctly (preserved existing, updated changed).
3. **Dry Run:** Correctly counts eligible rows without modifying data. All 6 active tables have eligible rows spanning multiple years. Large tables like `bronze_table_lineage` (30M rows) and `silver_query_table_access` (8.3M rows) are identified.
4. **Live Archive:** Runs end-to-end without errors. All tables are SKIPPED because the current `table_configs` have `archive_mode` set to SKIP. To perform actual archival, the table configurations need their `archive_mode` changed from SKIP to an active mode.