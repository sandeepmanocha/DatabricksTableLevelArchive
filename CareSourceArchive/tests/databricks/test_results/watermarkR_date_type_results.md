# 12 — Watermark DATE Type Migration Results

**Test Date:** 2026-04-08
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com

---

## Change Summary

Changed `watermark_value` column in `archive_audit_log` from `STRING` to `DATE`.

**Rationale:** Eliminated the implicit string→date coercion in SQL comparisons. `watermark_value` is always a date — now it is typed as one end-to-end.

**Files modified:**
- `src/utils.py` — added `sql_date_or_null` helper
- `src/audit.py` — `watermark_value: Optional[datetime_module.date]`, uses `sql_date_or_null`
- `src/archiver.py` — `CAST(MAX AS DATE)`, `sql_date_or_null` in comparisons, `_write_metadata` serializes via `.isoformat()`
- `notebooks/setup_config_tables.py` — DDL updated to `DATE`
- `tests/unit/test_utils.py` — 4 new tests for `sql_date_or_null`
- `tests/unit/test_audit.py` — `date` objects, `DATE '...'` SQL assertions
- `tests/unit/test_archiver.py` — all watermark mocks updated to `date` objects

---

## Unit Test Results

**Command:** `python3 -m pytest tests/unit/ -q`

**Result:** 259 passed, 1 pre-existing failure (unrelated — `test_scn16_merge_insert_includes_scan_run_id` was failing before this change)

---

## Database Migration

**Method:** Column mapping fallback (simple `ALTER COLUMN TYPE` not supported by Delta for STRING→DATE)

| Step | SQL | Result |
|------|-----|--------|
| Enable column mapping | `SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name', ...)` | OK |
| Add new column | `ADD COLUMN watermark_date DATE` | OK |
| Backfill | `UPDATE ... SET watermark_date = TRY_CAST(watermark_value AS DATE)` | 27 rows affected |
| Drop old | `DROP COLUMN watermark_value` | OK |
| Rename | `RENAME COLUMN watermark_date TO watermark_value` | OK |

**Verification:** `DESCRIBE TABLE` → `watermark_value date` ✓

---

## Integration Test 02 — Scanner First Run

**Run URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/163591081560593

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~60 sec |

Scanner log `merge_action = updated/preserved` for all tables. `table_configs` count: 14 rows. Active tables have `watermark_column` set.

---

## Integration Test 03 — Scanner Re-scan (Idempotent)

**Run URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/297158067384911/run/770324683560932

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~60 sec |

- `table_configs` row count unchanged: 14
- `merge_action = updated` (28 rows) and `preserved` (4 rows)
- No duplicate `table_id` entries

---

## Integration Test 04 — Archive Dry Run

**Run URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/514255614646571

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~135 sec |

- `DRY_RUN` entries written for all active tables/years
- `watermark_value = NULL` for all DRY_RUN rows (expected — dry runs don't compute watermarks)
- Column type confirmed as `date` via `DESCRIBE TABLE`
- Source table row counts unchanged (no data written)
