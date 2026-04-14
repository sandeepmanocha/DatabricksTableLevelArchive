# Incremental Archive Runs — Design Spec

**Date:** 2026-04-06
**Status:** FINAL
**Design branch:** `feat/delta_config_build`
**Implementation branch:** `feat/delta_config_build_v2_reruns`

---

## 1. Problem Statement

Users need to run the archive process for the same table and year multiple times. New data arrives in the source table for a year that was already archived. The system must handle every run identically — detect what's new via watermark, archive it, and never duplicate data.

---

## 2. Context and Decisions Made

### 2.1 Primary use case

New data appears in source for previously-archived years. The system runs, finds new records above the watermark, and appends them. No special "re-run" mode — every run is the same logic.

### 2.2 Table mode split

Whether each table uses `delete_after_archive=true` or `false` is decided per table during onboarding. The design works for both modes.

### 2.3 Archive format

**Delta only.** Parquet was considered and rejected:

- Parquet loses ACID safety. For `delete_after_archive=true` tables, a Parquet overwrite would destroy previously-archived records that were deleted from source.
- Parquet has no atomic append — a failure midway leaves partial data with no rollback.
- Parquet has no time travel, no schema evolution, no merge-on-read.
- Decision: keep Delta. Document Parquet as "considered and deferred."

### 2.4 Archive structure

**One Delta table per year per source table** (unchanged). Each year gets its own folder:

```
claims_archive/
  year_2020/   ← independent Delta table
  year_2021/   ← independent Delta table
  year_2022/   ← independent Delta table
```

Rehydration creates one external table per year folder, then a unified view with UNION ALL.

### 2.5 Rename: date_column → watermark_column

The existing `date_column` is renamed to `watermark_column`. It serves as:
- The **year bucketing** column: `YEAR(watermark_column)` determines which year folder a record goes into
- The **incremental filter**: `watermark_column > last_watermark` determines what's new since last run

Full rename across the codebase:

- `schema_templates.date_column_patterns` → `schema_templates.watermark_column_patterns`
- `table_configs.date_column` → `table_configs.watermark_column`
- All code references in `src/`, `tests/`, `notebooks/`, `docs/` updated
- Breaking change to existing config data — requires migration of config table rows

### 2.6 Watermark column prerequisite

**The watermark column MUST be monotonically increasing.** Suitable columns: `created_date`, `updated_date`, `etl_load_date`. This is a prerequisite for any table onboarded to the archive system.

Because the column is monotonically increasing, the watermark filter reliably catches all new records. There is no "late arrivals below watermark" scenario.

### 2.7 Archive is append-only

**No REPLACE mode. No `force_rerun` parameter.** The archive only grows — it never overwrites.

- **CREATE**: First run for a year (folder doesn't exist)
- **APPEND**: Subsequent runs add only new records (watermark-filtered)
- **SKIP**: No new records above watermark
- **No REPLACE**: Overwriting an archive risks destroying the only copy of data that was deleted from source.

`force_rerun` was considered and **deferred**. The risk of data loss from an accidental REPLACE outweighs the convenience.

---

## 3. Approach Selected: Delta + Incremental ETL

### 3.1 Core mental model

The archive process is an **incremental ETL job**. Each run reads from source, identifies what's new using a watermark, and appends it to the archive. Every run is identical — the system decides CREATE, APPEND, or SKIP automatically.

### 3.2 Three approaches evaluated

| Approach | Summary | Verdict |
|----------|---------|---------|
| **A: Delta + Incremental ETL** | Keep Delta. Use watermark filter to find new records. APPEND only. Count comparison for drift detection. | **Selected** |
| B: Parquet + Full-Replace | Switch to Parquet. Loses ACID for re-runs (data loss risk). | Rejected |
| C: Delta + Run-Partitioned Appends | Stamp every row with `_archive_run_id`. Always append, dedup at query time. | Rejected — storage bloat, complex queries |

---

## 4. Run Decision Logic

### 4.1 Unified model — both modes use the same archive logic

Every run follows the same steps regardless of `delete_after_archive`:

1. Query audit for the last watermark value (`MAX(watermark_column)`) for this table+year
2. Check folder exists
3. No folder → **CREATE**: `SELECT * FROM source WHERE YEAR(wm_col) = Y AND exclusions ...`
4. Folder exists → filter source: `WHERE YEAR(wm_col) = Y AND wm_col > last_watermark AND exclusions ...`
   - Records found → **APPEND**
   - No records → **SKIP**
5. Store `watermark_value`, `source_year_count`, `archive_mode` in audit

**The ONLY difference between the two modes is what happens AFTER the archive write:**

| Mode | After archive write |
|------|---------------------|
| `delete_after_archive=false` | Done. Source keeps data. |
| `delete_after_archive=true` | VERIFY counts → DELETE archived records from source. |

### 4.2 Accidental double-run protection

- Run 1: Archives all records for 2022, stores watermark = `2022-12-31`
- Run 2 (moments later): Watermark filter `wm_col > '2022-12-31'` finds zero records → SKIP
- Safe. No duplicates. No wasted work.

### 4.3 Count drift detection

On every run, store `source_year_count = SELECT COUNT(*) FROM source WHERE YEAR(wm_col) = Y`.

Compare against the last stored value:

| Drift | Meaning | Action |
|-------|---------|--------|
| Count unchanged | No new data (confirms SKIP) | No-op |
| Count increased, watermark found records | New data above watermark | Normal APPEND |
| Count decreased | Source records removed by another process | WARNING: "Source count dropped from N to M." |

Count drift is **informational logging**, not a trigger for automated action.

### 4.4 Multiple years in one run

Each eligible year is processed independently. A single run may CREATE year_2020, APPEND to year_2021, and SKIP year_2022 — each based on its own watermark state.

---

## 5. Deleted Data Scenarios

### Scenario A: New data arrives after prior archive+delete (normal)

Standard case for `delete_after_archive=true`:
- Old records archived and deleted from source
- New records arrive in source with watermark above last stored value
- Watermark filter applies → finds records above high-water mark → APPEND

### Scenario B: Archive folder accidentally deleted from storage

- **`delete_after_archive=false`:** Safe — source has everything. Folder doesn't exist → treated as first run → CREATE.
- **`delete_after_archive=true`:** Dangerous — old records gone from both source and archive.
  - Detection: audit says `ARCHIVED_AND_DELETED` but folder doesn't exist
  - Action: **ERROR**, halt. "Archive folder missing for year 2022 but records were previously archived and deleted from source. Data may be lost. Check cloud storage recycle bin or Delta time travel on source table."
  - Process does NOT proceed with a CREATE — that would only capture whatever's currently in source.

### Scenario C: Source records deleted by another process (external)

- Source count may be lower than last audit's `source_year_count`
- Detection: count comparison shows decrease
- Action: WARNING logged. Archive is unaffected (append-only).

---

## 6. Rehydration (unchanged)

```sql
CREATE TABLE IF NOT EXISTS target.schema.claims_year_2022
  USING DELTA LOCATION 'base_path/year_2022'

CREATE OR REPLACE VIEW target.schema.claims_unified AS
  SELECT * FROM source.schema.claims
  UNION ALL SELECT * FROM target.schema.claims_year_2022
  UNION ALL SELECT * FROM target.schema.claims_year_2023
```

Incremental appends are invisible to rehydration — the external table always sees the latest state.

---

## 7. Configuration and Schema Changes

### 7.1 Rename: date_column → watermark_column

Full rename across codebase:

| Location | Old name | New name |
|----------|----------|----------|
| `schema_templates` DDL | `date_column_patterns` | `watermark_column_patterns` |
| `table_configs` DDL | `date_column` | `watermark_column` |
| `src/scanner.py` | all `date_column` refs | `watermark_column` |
| `src/archiver.py` | all `date_column` refs | `watermark_column` |
| `src/config.py` | all `date_column` refs | `watermark_column` |
| `src/conditions.py` | all `date_column` refs (if any) | `watermark_column` |
| `tests/` | all `date_column` refs | `watermark_column` |
| `notebooks/` | all `date_column` refs | `watermark_column` |
| `docs/` | all `date_column` refs | `watermark_column` |

### 7.2 New audit columns

New columns on `archive_audit_log`:

| Column | Type | Purpose |
|--------|------|---------|
| `watermark_value` | STRING | `MAX(watermark_column)` from archived records this run. Stored as string for flexibility across date/timestamp types. |
| `source_year_count` | BIGINT | Total `COUNT(*)` from source for this year at time of run. Enables count drift detection. |
| `archive_mode` | STRING | How the write was performed: `CREATE`, `APPEND`, `SKIP`. |

### 7.3 Updated metadata sidecar

`_archive_metadata.json` gains `watermark_value`, `source_year_count`, and `archive_mode` alongside existing fields.

### 7.4 What's NOT added

| Item | Status | Reason |
|------|--------|--------|
| `force_rerun` parameter | Deferred | Risk of data loss from accidental REPLACE outweighs convenience |
| `archive_format` config | Deferred | Delta only for now; Parquet adds complexity without safety |

---

## 8. Archiver Code Structure

### 8.1 `_process_year_live` flow

```
_process_year_live(merged, year, exclusion_clause, conditions, dbutils):

  1. Log STARTED
  2. Concurrent check → SKIPPED_CONCURRENT, return

  3. Query audit: last status, watermark_value, source_year_count
  4. Query source: current source_year_count
  5. Check folder exists

  --- Safety ---
  6. Folder missing + last status ARCHIVED_AND_DELETED → ERROR, halt
  7. Count drift: current < last source_year_count → WARNING

  --- Resume (incomplete prior run) ---
  8. Last status = ARCHIVED + delete_after=true → verify + delete + return

  --- Normal flow ---
  9.  No folder → mode = CREATE (no watermark filter)
  10. Folder exists → filter wm_col > last_watermark
      - Records found → mode = APPEND
      - No records → SKIP, return

  11. Archive (CREATE or APPEND)
  12. Verify counts
  13. Log ARCHIVED with watermark_value, source_year_count, archive_mode
  14. If delete_after=true → ownership check → delete → log ARCHIVED_AND_DELETED
  15. Write metadata
```

### 8.2 Changes vs current code

- **Watermark replaces folder-exists + resume branching** as the primary decision driver
- **`year_override` / `replace` mode removed** (deferred per Section 2.7)
- **Resume narrows to one case:** crashed between archive and delete
- **New audit method:** `get_last_run_state(table, year)` returns `(status, watermark_value, source_year_count)`
- **Source count + watermark stored in audit on every write**

---

## 9. Test Scenarios

### Per-year outcomes (system decides automatically):

1. Folder doesn't exist → CREATE
2. Folder exists + new data above watermark → APPEND
3. Folder exists + no new data above watermark → SKIP
4. Multiple years eligible in one run → each year handled independently

### With `delete_after=true`:

5. After CREATE or APPEND → verify counts → delete from source

### Safety:

6. Source count dropped since last audit → WARNING logged
7. Folder missing but audit says `ARCHIVED_AND_DELETED` → ERROR, halt

---

## 10. Files Changed

| File | Change |
|------|--------|
| `notebooks/setup_config_tables.py` | DDL: rename `date_column` → `watermark_column` in `table_configs`; rename `date_column_patterns` → `watermark_column_patterns` in `schema_templates`; add `watermark_value`, `source_year_count`, `archive_mode` to `archive_audit_log` |
| `src/archiver.py` | Watermark-driven flow: query last watermark, filter source, CREATE/APPEND/SKIP. Count drift detection. Remove `year_override`/`replace` mode. Rename all `date_column` refs. |
| `src/audit.py` | New columns in `ARCHIVE_AUDIT_COLUMNS`. New `get_last_run_state(table, year)` method. Updated `log_archive` signature for new fields. |
| `src/config.py` | Rename `date_column` → `watermark_column`. Update validation. |
| `src/scanner.py` | Rename `date_column` → `watermark_column`, `date_column_patterns` → `watermark_column_patterns` throughout. |
| `src/utils.py` | Rename `date_column` refs if any. |
| `src/rehydrator.py` | Rename `date_column` refs if any. |
| `src/conditions.py` | Rename `date_column` refs if any. |
| `notebooks/*.py` | Rename all `date_column` refs. |
| `tests/unit/*.py` | Rename all `date_column` refs. New tests for watermark logic (Section 9). |
| `docs/requirements-summary.md` | Update terminology and incremental archive behavior. |
| `docs/development-rules.md` | Update any `date_column` references. |
