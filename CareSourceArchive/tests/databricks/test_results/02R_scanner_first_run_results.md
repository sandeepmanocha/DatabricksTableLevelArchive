# 02 — Scanner First Run Results

---

## Run — 2026-04-07

**Test Date:** 2026-04-07
**Test Time:** 12:16 – 12:20 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Config:** `sandeep_manocha.caresource_audit`
**Source:** `sandeep_manocha.caresource_data_samples`

### Before — Baseline Counts

| Table | Rows |
|-------|------|
| scanner_log | 0 |
| table_configs | 0 |

### Step 1 — Run Scanner (first run)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/1011741787587035 |

### Step 2 — Check scanner_log (first run)

14 rows logged (all merge_action = `added`).

| Source Table | Match Status | Matched Column | Active | Result |
|-------------|-------------|----------------|--------|--------|
| claims | matched | event_date | true | **PASS** |
| members | unmatched | — | false | **PASS** (expected) |
| providers | unmatched | — | false | **PASS** (expected) |
| bronze_column_lineage | matched | event_date | true | — |
| bronze_query_history | matched | start_time | true | — |
| bronze_table_lineage | matched | event_date | true | — |
| gold_daily_access_trends | matched | query_date | true | — |
| silver_query_table_access | matched | start_time | true | — |
| gold_column_usage | unmatched | — | false | — |
| gold_consumer_summary | unmatched | — | false | — |
| gold_impact_blast_radius | unmatched | — | false | — |
| gold_table_access_summary | unmatched | — | false | — |
| gold_table_lineage_paths | unmatched | — | false | — |
| silver_table_dependencies | unmatched | — | false | — |

### Step 3 — Check table_configs (first run)

| Metric | Value |
|--------|-------|
| Total rows | 14 |
| Active | 6 |
| Inactive | 8 |

Active tables have `watermark_column` set. Inactive tables have reason = "no date column matched — no pattern matched any column."

**Result:** **PASS**

### Manual Fix — Update Watermark Patterns

`members` and `providers` were unmatched because `start_date` and `effective_date` were not in the watermark patterns.

```sql
UPDATE sandeep_manocha.caresource_audit.schema_templates
SET watermark_column_patterns = ARRAY('event_date', 'start_time', 'query_date', 'start_date', 'effective_date')
WHERE schema_id = 'sandeep_manocha__caresource_data_samples'
```

| Field | Value |
|-------|-------|
| Rows affected | 1 |
| Verified patterns | `["event_date","start_time","query_date","start_date","effective_date"]` |

### Re-run Scanner (after pattern fix)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~52 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/402308378668412 |

#### scanner_log after re-run (key tables)

| Source Table | Match Status | Matched Column | Active | merge_action | Result |
|-------------|-------------|----------------|--------|-------------|--------|
| claims | matched | event_date | true | updated | **PASS** |
| members | matched | start_date | true | updated | **PASS** |
| providers | matched | effective_date | true | updated | **PASS** |

#### table_configs after re-run (key tables)

| Table ID | Watermark Column | Active | Result |
|----------|-----------------|--------|--------|
| ...claims | event_date | true | **PASS** |
| ...members | start_date | true | **PASS** |
| ...providers | effective_date | true | **PASS** |

#### Final counts

| Table | Rows |
|-------|------|
| scanner_log | 28 (14 from run 1 + 14 from run 2) |
| table_configs | 14 (8 active, 6 inactive) |

### Overall Result: PASS

Scanner correctly discovers all 14 tables in the source schema. On first run, `claims` matched immediately via `event_date`. After adding `start_date` and `effective_date` to watermark patterns, `members` and `providers` also matched on re-run. The merge logic correctly used `added` on first run and `updated` on re-run.

---

## Run — 2026-04-09

**Test Date:** 2026-04-09
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Bundle Target:** dev

### Before — Baseline Counts

| Table | Rows |
|-------|------|
| scanner_log | 0 |
| table_configs | 0 |

### Step 1 — Run Scanner

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~62 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/946027027016902 |

### Step 2 — Check scanner_log

14 rows logged (all merge_action = `added`).

| Source Table | Match Status | Matched Column | Active | Result |
|-------------|-------------|----------------|--------|--------|
| claims | matched | event_date | true | **PASS** |
| members | unmatched | — | false | **PASS** (expected) |
| providers | unmatched | — | false | **PASS** (expected) |
| bronze_column_lineage | matched | event_date | true | — |
| bronze_table_lineage | matched | event_date | true | — |
| bronze_query_history | matched | start_time | true | — |
| gold_daily_access_trends | matched | query_date | true | — |
| silver_query_table_access | matched | start_time | true | — |
| gold_column_usage | unmatched | — | false | — |
| gold_consumer_summary | unmatched | — | false | — |
| gold_impact_blast_radius | unmatched | — | false | — |
| gold_table_access_summary | unmatched | — | false | — |
| gold_table_lineage_paths | unmatched | — | false | — |
| silver_table_dependencies | unmatched | — | false | — |

### Step 3 — Check table_configs

14 rows created. 6 active, 8 inactive.

| Table ID | Watermark Column | Active | Reason |
|----------|-----------------|--------|--------|
| ...claims | event_date | true | |
| ...members | — | false | no date column matched |
| ...providers | — | false | no date column matched |
| ...bronze_column_lineage | event_date | true | |
| ...bronze_table_lineage | event_date | true | |
| ...bronze_query_history | start_time | true | |
| ...gold_daily_access_trends | query_date | true | |
| ...silver_query_table_access | start_time | true | |
| ...gold_column_usage | — | false | no date column matched |
| ...gold_consumer_summary | — | false | no date column matched |
| ...gold_impact_blast_radius | — | false | no date column matched |
| ...gold_table_access_summary | — | false | no date column matched |
| ...gold_table_lineage_paths | — | false | no date column matched |
| ...silver_table_dependencies | — | false | no date column matched |

### Overall Result: PASS

Scanner discovered 14 tables (3 test + 11 lineage/gold). `claims` matched on `event_date`. `members` and `providers` unmatched as expected (`start_date`/`effective_date` not in watermark patterns). To activate them, update `schema_templates.watermark_column_patterns` and re-run scanner.
