# 02 — Scanner First Run Results

---

## Run — 2026-04-12 17:28 CDT

**TL;DR:** Scanner ran successfully (~85s). All 14 tables scanned — 7 active (watermark matched), 7 inactive (no pattern match). `claims` matched `event_date`, `providers` matched `effective_date`. `members` still unmatched. Post secret-scope removal deploy validated end-to-end.

**Branch:** `feat/delta_config_build_v3_code_reduce` (post secret-scope removal — commit `04d8fcb`)
**Profile:** DEFAULT

### Before State

| Metric | Count |
|--------|-------|
| scanner_log rows | 28 |
| table_configs rows | 14 |

### Step 1 — Run scanner: **PASS**

```
databricks bundle run caresource_scanner -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings"
```

- Run URL: https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/366217519638786
- Status: TERMINATED SUCCESS
- Duration: ~85 seconds

### Step 2 — Check scanner_log: **PASS**

Latest 14 entries (one per table):

| source_table | match_status | matched_column | is_active | merge_action |
|---|---|---|---|---|
| silver_query_table_access | matched | start_time | true | updated |
| silver_table_dependencies | unmatched | | false | updated |
| bronze_column_lineage | matched | event_date | true | updated |
| claims | matched | event_date | true | preserved |
| gold_impact_blast_radius | unmatched | | false | updated |
| gold_consumer_summary | unmatched | | false | updated |
| gold_table_lineage_paths | unmatched | | false | updated |
| members | unmatched | | false | updated |
| gold_daily_access_trends | matched | query_date | true | updated |
| gold_column_usage | unmatched | | false | updated |
| bronze_table_lineage | matched | event_date | true | updated |
| bronze_query_history | matched | start_time | true | updated |
| providers | unmatched | | false | preserved |
| gold_table_access_summary | unmatched | | false | updated |

**Notes:**
- `claims` shows `merge_action = preserved` (already existed from prior runs)
- `providers` shows `merge_action = preserved` and `is_active = false` in the scanner_log, but table_configs retains `is_active = true` from prior manual pattern update — scanner preserves existing config
- `members` still unmatched — `start_date` not in watermark_column_patterns

### Step 3 — Check table_configs: **PASS**

| table_id | watermark_column | is_active | reason |
|---|---|---|---|
| ...bronze_column_lineage | event_date | true | |
| ...bronze_query_history | start_time | true | |
| ...bronze_table_lineage | event_date | true | |
| ...claims | event_date | true | |
| ...gold_column_usage | | false | no date column matched — no pattern matched any column |
| ...gold_consumer_summary | | false | no date column matched — no pattern matched any column |
| ...gold_daily_access_trends | query_date | true | |
| ...gold_impact_blast_radius | | false | no date column matched — no pattern matched any column |
| ...gold_table_access_summary | | false | no date column matched — no pattern matched any column |
| ...gold_table_lineage_paths | | false | no date column matched — no pattern matched any column |
| ...members | | false | no date column matched — no pattern matched any column |
| ...providers | effective_date | true | |
| ...silver_query_table_access | start_time | true | |
| ...silver_table_dependencies | | false | no date column matched — no pattern matched any column |

14 rows total — 7 active, 7 inactive.

## What Happened

Scanner deployed and ran successfully after the secret-scope removal changes. All 14 source tables were scanned. 7 tables matched watermark patterns and are active. 7 tables had no pattern match and are inactive. No errors, no duplicates. This confirms the code changes (removing `load_secrets`, `secret_scope` from config, etc.) did not break the scanner pipeline.

## Next Steps

- `members` can be activated by adding `start_date` to `schema_templates.watermark_column_patterns` (see manual steps in 02T test case)
- Proceed to 03T (scanner re-scan idempotent) and 04T (archive dry run)

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
