# Watermark Calculations — How It Works

**Date:** 2026-04-07
**Status:** FINAL
**Branch:** `feat/delta_config_build_v2_reruns`

---

## 1. What Is the Watermark?

The watermark is `MAX(watermark_column)` from the archived Delta table for a given table+year. It is:

- **Calculated from the data itself** — not the date the archive ran
- **Stored as a STRING** in the audit table (`watermark_value` column) for flexibility across DATE/TIMESTAMP types
- **Used as the incremental filter** on the next run to find only new records

The granularity depends on the source column's data type:

| Source column type | Example watermark | Incremental filter |
|---|---|---|
| `DATE` | `2022-11-30` | `created_date > '2022-11-30'` |
| `TIMESTAMP` | `2022-11-30 23:59:59.123` | `load_ts > '2022-11-30 23:59:59.123'` |

---

## 2. How the Watermark Is Calculated

```sql
SELECT CAST(MAX(watermark_column) AS STRING) FROM delta.`archive/claims/year_2022/`
```

This runs against the **archive Delta table** (not source) after a successful write. The result is stored in the audit table alongside the `ARCHIVED` status.

---

## 3. How the Watermark Is Used

On each run, for each eligible year:

1. Query audit for the last successful watermark: `get_last_run_state(table, year)` → returns `watermark_value`
2. If no folder exists → **CREATE** (no watermark filter, archive everything for that year)
3. If folder exists → count new records using the watermark filter:

```sql
SELECT COUNT(*) FROM source
WHERE YEAR(watermark_column) = 2022
  AND watermark_column > '{last_watermark}'
  AND watermark_column IS NOT NULL
  AND (exclusion conditions)
```

4. If count > 0 → **APPEND** only those new records
5. If count = 0 → **SKIP**

---

## 4. Walkthrough Example

**Setup:** Table `claims`, column `created_date` (DATE type), retention = 3 years.

Source table:

| claim_id | amount | created_date |
|---|---|---|
| C001 | 500 | 2022-03-15 |
| C002 | 300 | 2022-07-20 |
| C003 | 800 | 2022-11-30 |
| C004 | 200 | 2023-01-10 |

### Run 1 — First ever run (January 2026)

1. Year 2022 is outside retention window (2026 - 3 = 2023 cutoff)
2. Check folder for `year_2022` → **doesn't exist**
3. Mode = **CREATE**
4. SQL: `SELECT * FROM claims WHERE YEAR(created_date) = 2022 AND created_date IS NOT NULL AND (exclusions)`
5. Writes C001, C002, C003 to `archive/claims/year_2022/`
6. Calculates watermark: `MAX(created_date)` from archive → **`2022-11-30`**
7. Audit: `status=ARCHIVED, watermark_value='2022-11-30', archive_mode='CREATE'`

### Run 2 — No new data (February 2026)

1. Year 2022 eligible
2. Folder exists
3. Last watermark from audit → **`2022-11-30`**
4. Count: `SELECT COUNT(*) FROM claims WHERE YEAR(created_date) = 2022 AND created_date > '2022-11-30'`
5. Result: **0** → Mode = **SKIP**
6. Audit: `status=SKIPPED, archive_mode='SKIP'`

Nothing happens. No data touched.

### Run 3 — New data arrived (March 2026)

A late December claim was loaded into source:

| claim_id | amount | created_date |
|---|---|---|
| C005 | 650 | 2022-12-15 |

1. Year 2022 eligible
2. Folder exists
3. Last watermark from audit → **`2022-11-30`**
4. Count: `SELECT COUNT(*) FROM claims WHERE YEAR(created_date) = 2022 AND created_date > '2022-11-30'`
5. Result: **1** (C005: `2022-12-15 > 2022-11-30` ✓) → Mode = **APPEND**
6. SQL: `INSERT INTO delta.'year_2022' SELECT * FROM claims WHERE YEAR(created_date) = 2022 AND created_date > '2022-11-30'`
7. New watermark: `MAX(created_date)` from archive → **`2022-12-15`**
8. Audit: `status=ARCHIVED, watermark_value='2022-12-15', archive_mode='APPEND'`

### Run 4 — Redundant re-run (same day as Run 3)

1. Year 2022 eligible
2. Folder exists
3. Last watermark from audit → **`2022-12-15`**
4. Count: `SELECT COUNT(*) FROM claims WHERE YEAR(created_date) = 2022 AND created_date > '2022-12-15'`
5. Result: **0** → Mode = **SKIP**
6. Audit: `status=SKIPPED, archive_mode='SKIP'`

Safe. No duplicates. No wasted work.

---

## 5. The Gap — What Gets Missed

If a record arrives in source with a `created_date` **below** the current watermark, the watermark filter will not catch it.

**Example:** After Run 1 (watermark = `2022-11-30`), a new record appears:

| claim_id | amount | created_date |
|---|---|---|
| C006 | 400 | 2022-06-01 |

On the next run: `created_date > '2022-11-30'` → `2022-06-01` is NOT greater → **C006 is invisible**. It stays in source, never archived.

### Why this is by design

The watermark column **must be monotonically increasing** (design spec Section 2.6). Suitable columns:

| Column type | Monotonic? | Good watermark? |
|---|---|---|
| `etl_load_date` (when the row was loaded into the table) | Yes — always increases | **Best choice** |
| `updated_date` (last modified timestamp) | Yes — always increases | Good |
| `created_date` (business event date) | **Not always** — late arrivals can have old dates | Risky |

**Recommendation for onboarding:** Prefer `etl_load_date` or `updated_date` over `created_date` as the watermark column. If only `created_date` is available, the operator must understand that late-arriving records with old dates will not be picked up by incremental runs.

### If late arrivals are a concern

The count drift detection (Section 4.3 of the rerun design spec) provides a safety net:

- Each run stores `source_year_count = COUNT(*) FROM source WHERE YEAR(wm_col) = Y`
- If source count **increases** between runs but watermark finds nothing new → the new records fell below the watermark
- This shows up as a WARNING: "Source count increased from N to M but no new records above watermark"

This doesn't automatically fix the problem, but it makes it visible. The operator can investigate and take manual action.

---

## 6. Audit Trail Example

After all four runs above, the audit table for `claims` year 2022:

| status | archive_mode | watermark_value | source_year_count | created_at |
|---|---|---|---|---|
| STARTED | | | | Jan 10 09:00 |
| ARCHIVED | CREATE | 2022-11-30 | 3 | Jan 10 09:30 |
| STARTED | | | | Feb 10 09:00 |
| SKIPPED | SKIP | | 3 | Feb 10 09:01 |
| STARTED | | | | Mar 10 09:00 |
| ARCHIVED | APPEND | 2022-12-15 | 4 | Mar 10 09:15 |
| STARTED | | | | Mar 10 14:00 |
| SKIPPED | SKIP | | 4 | Mar 10 14:01 |

The full story is readable: CREATE → SKIP (nothing new) → APPEND (one new record, watermark moved) → SKIP (redundant run).
