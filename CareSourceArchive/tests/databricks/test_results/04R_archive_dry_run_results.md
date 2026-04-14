# 04 — Archive Dry Run — Results

## Run — 2026-04-10 12:56

**Date:** 2026-04-10
**Run ID:** `41232e99-4a59-436b-b63b-e51ccdaf8ae2`
**Job URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/512065461168965
**Status:** PASS

---

### Context

Fresh environment — audit log was empty (0 rows) before this run. The scanner had populated `table_configs` but no archives had been created yet. `members` and `providers` are `is_active = false` in `table_configs`, so they are correctly excluded.

Since no prior archives exist, the dry run predicts **CREATE** for every table+year — the same action a live run would take on first execution.

---

### Before

| Metric | Value |
|--------|-------|
| Audit log rows | 0 |
| claims count | 5,002 |
| members count | 3,000 |
| providers count | 1,005 |

### Dry Run Results

**15 DRY_RUN entries** logged across **6 tables**. All show `action = CREATE`, `would_archive = record_count`.

#### claims (8 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2018 | CREATE | 623 | 623 |
| 2019 | CREATE | 624 | 624 |
| 2020 | CREATE | 625 | 625 |
| 2021 | CREATE | 623 | 623 |
| 2022 | CREATE | 624 | 624 |
| 2023 | CREATE | 624 | 624 |
| 2024 | CREATE | 622 | 622 |
| 2025 | CREATE | 622 | 622 |

#### bronze_column_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 290,984,512 | 290,984,512 |

#### bronze_query_history (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | CREATE | 37,207 | 37,207 |
| 2026 | CREATE | 37,761,839 | 37,761,839 |

#### bronze_table_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 30,010,157 | 30,010,157 |

#### gold_daily_access_trends (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | CREATE | 60,709 | 60,709 |

#### silver_query_table_access (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | CREATE | 1,795 | 1,795 |
| 2026 | CREATE | 8,304,946 | 8,304,946 |

#### Not included

- **members** — `is_active = false` in `table_configs`
- **providers** — `is_active = false` in `table_configs`
- Various gold tables (`gold_column_usage`, `gold_consumer_summary`, etc.) — `is_active = false`

### After

| Metric | Value |
|--------|-------|
| Audit log rows | 15 (+15 DRY_RUN) |
| claims count | 5,002 (unchanged) |
| members count | 3,000 (unchanged) |
| providers count | 1,005 (unchanged) |

### Verification

1. **`action` field present** — every `conditions_applied` JSON includes `"action": "CREATE"` (correct for a fresh environment with no prior archives).
2. **`would_archive` = `total_eligible`** — correctly reflects that all eligible rows would be archived on a live CREATE run.
3. **Source data untouched** — all three table counts match the before state exactly.
4. **No archive folders created** — dry run mode only queries, never writes.
5. **Only DRY_RUN entries** — the run produced only DRY_RUN audit rows, no live operations.

---

## Run — 2026-04-07 (previous)

**Date:** 2026-04-07
**Run ID:** `b7209f76-b411-41fe-b8bd-56d534030f33`
**Job URL:** https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1099249755759883
**Status:** PASS

---

## Context

This dry run was the first after the **Dry Run Action Prediction** refactor:
- `_resolve_year_action` now determines `CREATE / APPEND / SKIP / RESUME_DELETE / ERROR`
- `_dry_run_year` calls `_resolve_year_action` so dry runs predict the same action a live run would take
- The `action` field is now included in the audit `conditions_applied` JSON and in the notebook summary

Since prior tests (05–10) already archived all eligible years, this dry run correctly predicts **SKIP** for every table+year — the same result a live run would produce.

---

## Before

| Metric | Value |
|--------|-------|
| Audit log rows | 192 |
| Last run ID | `d08564fc-b783-439f-8b5b-56dd691015f4` |
| claims count | 5,002 |
| members count | 3,000 |
| providers count | 5 |

## Dry Run Results

**19 DRY_RUN entries** logged across **5 tables**. All show `action = SKIP`, `would_archive = 0`.

### claims (8 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2018 | SKIP | 623 | 0 |
| 2019 | SKIP | 624 | 0 |
| 2020 | SKIP | 625 | 0 |
| 2021 | SKIP | 623 | 0 |
| 2022 | SKIP | 624 | 0 |
| 2023 | SKIP | 624 | 0 |
| 2024 | SKIP | 622 | 0 |
| 2025 | SKIP | 622 | 0 |

### members (7 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2019 | SKIP | 426 | 0 |
| 2020 | SKIP | 426 | 0 |
| 2021 | SKIP | 427 | 0 |
| 2022 | SKIP | 428 | 0 |
| 2023 | SKIP | 429 | 0 |
| 2024 | SKIP | 426 | 0 |
| 2025 | SKIP | 428 | 0 |

### bronze_table_lineage (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | SKIP | 30,010,157 | 0 |

### gold_daily_access_trends (1 year)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2026 | SKIP | 60,709 | 0 |

### silver_query_table_access (2 years)

| Year | Action | Eligible | Would Archive |
|------|--------|----------|---------------|
| 2025 | SKIP | 1,795 | 0 |
| 2026 | SKIP | 8,304,946 | 0 |

### Not included

- **providers** — only 5 source rows remain (deleted in test 09), all in recent years beyond the retention cutoff.
- **bronze_column_lineage**, **bronze_query_history** — not eligible or not configured as active.

## After

| Metric | Value |
|--------|-------|
| Audit log rows | 211 (+19 DRY_RUN) |
| claims count | 5,002 (unchanged) |
| members count | 3,000 (unchanged) |
| providers count | 5 (unchanged) |

## Verification

1. **`action` field present** — every `conditions_applied` JSON includes `"action": "SKIP"` (the new field from the refactor).
2. **`would_archive = 0`** — correctly reflects that existing archives cover all data; a live run would skip these years.
3. **Source data untouched** — all three table counts match the before state exactly.
4. **No archive folders created** — dry run mode only queries, never writes.
5. **No non-DRY_RUN entries** — the run produced only DRY_RUN audit rows.
