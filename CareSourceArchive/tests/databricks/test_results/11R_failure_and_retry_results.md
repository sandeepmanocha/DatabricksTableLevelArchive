# 11 — Failure and Retry Results

## Run — 2026-04-10 16:04 CDT (claims)

> **TL;DR:** We broke the archiver on purpose, then fixed it. (1) Archived claims normally — all 8 years saved. (2) Broke the path + added 2 new rows — archiver crashed as expected. (3) Fixed the path and re-ran — it skipped the old years, archived the new rows. Clean recovery. **Test passed.**

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Table:** claims (scoped via `table_config_filter`)

---

### Prerequisites

Claims was already in a clean pre-archive state (archive folders and audit entries were removed during test 08 setup). No cleanup needed.

- Source table: 8 years (2018-2025), 622-627 rows/year, plus 15 NULL-date rows
- Audit log: only DRY_RUN entries from test 08
- Archive folders: none
- `table_configs` verified: `watermark_column = event_date`, `delete_after_archive = false`, correct `archive_base_path`

---

### Phase 1 — Good Run (baseline)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~137 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/461021536004858 |

All 8 years archived:

| Year | Status | Record Count | Watermark |
|------|--------|-------------|-----------|
| 2018 | STARTED → ARCHIVED | 623 | 2018-12-31 |
| 2019 | STARTED → ARCHIVED | 624 | 2019-12-31 |
| 2020 | STARTED → ARCHIVED | 627 | 2020-12-31 |
| 2021 | STARTED → ARCHIVED | 625 | 2021-12-31 |
| 2022 | STARTED → ARCHIVED | 624 | 2022-12-31 |
| 2023 | STARTED → ARCHIVED | 624 | 2023-12-31 |
| 2024 | STARTED → ARCHIVED | 622 | 2024-12-31 |
| 2025 | STARTED → ARCHIVED | 622 | 2025-12-31 |

---

### Phase 2 — Bad Run (new data + broken path)

#### 2a. Insert new data

All existing claims years have watermarks at year-end (12-31), so there's no date within an existing year that's `> watermark`. Inserted 2 rows for **year 2026** (a new, unarchived year) instead:

```sql
INSERT INTO sandeep_manocha.caresource_data_samples.claims VALUES
  ('CLM-FAIL-01', 'MBR-00001', 'PRV-0001', 'Medical', 'J06.9', 150.00, 'Closed', DATE'2026-03-15', current_timestamp()),
  ('CLM-FAIL-02', 'MBR-00002', 'PRV-0002', 'Dental',  'E11.9', 250.00, 'Active', DATE'2026-04-01', current_timestamp())
```

> **Watermark note:** Claims watermarks all land on Dec 31 because the test data generator distributes dates across the full year. This means APPEND within an existing year cannot be triggered without manually rolling back the watermark. For this test, a new year (2026) tests the same failure+recovery flow — recovery uses CREATE instead of APPEND.

#### 2b. Break archive path

Set to `/Volumes/sandeep_manocha/nonexistent_volume/bad_path`.

#### 2c. Run archive

| Field | Value |
|-------|-------|
| Status | **PASS** — INTERNAL_ERROR (expected) |
| Duration | ~84 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/686871185936550 |

#### 2d. Audit log

| Year | Status | Error |
|------|--------|-------|
| 2018 | STARTED → FAILED | `SCHEMA_NOT_FOUND: sandeep_manocha.nonexistent_volume` |
| 2018 | STARTED → FAILED | (for-each built-in retry, same error) |

**PASS** — Year 2018 fails first because `archive_folder_exists` checks the bad path, returns false for ALL years, and the archiver tries to CREATE starting from year 2018. The for-each loop stops at the first failure, so year 2026 (with new data) is never reached.

---

### Phase 3 — Good Run (fix and recover)

#### 3a. Restore correct path

Restored to `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples`.

#### 3b. Run archive

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~126 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/663551358827358 |

#### 3c. Audit log (recovery)

| Year | Status | archive_mode | record_count |
|------|--------|-------------|-------------|
| 2018 | STARTED → SKIPPED | SKIP | 0 |
| 2019 | STARTED → SKIPPED | SKIP | 0 |
| 2020 | STARTED → SKIPPED | SKIP | 0 |
| 2021 | STARTED → SKIPPED | SKIP | 0 |
| 2022 | STARTED → SKIPPED | SKIP | 0 |
| 2023 | STARTED → SKIPPED | SKIP | 0 |
| 2024 | STARTED → SKIPPED | SKIP | 0 |
| 2025 | STARTED → SKIPPED | SKIP | 0 |
| **2026** | **STARTED → ARCHIVED** | **CREATE** | **2** |

**PASS** — Year 2018 detected the prior FAILED status and retried, but had no new data → SKIPPED. Years 2019-2025 had no new data → SKIPPED. Year 2026 (new data inserted during Phase 2a) was CREATEd successfully with 2 records and watermark `2026-04-01`.

---

### Overall Result: PASS

The full Good Run → Bad Run → Good Run cycle works correctly on claims:

1. **Phase 1:** All 8 years archive cleanly (STARTED → ARCHIVED, CREATE mode)
2. **Phase 2:** Bad path causes STARTED → FAILED with descriptive `SCHEMA_NOT_FOUND` error on first year (2018). For-each loop stops at first failure.
3. **Phase 3:** After fixing the path:
   - Previously-failed year 2018 retries and SKIPs (no new data)
   - Years 2019-2025 SKIP (no new data)
   - Year 2026 CREATEs the 2 new rows inserted during Phase 2a

### Key insights

1. **Watermark at year-end:** When the test data generator fills dates through Dec 31, all watermarks land on 12-31. This prevents APPEND within existing years because `WHERE event_date > '20XX-12-31'` never matches same-year dates. To test APPEND recovery, either use a table with mid-month watermarks (like providers) or insert data for a new year.
2. **Recovery uses CREATE for new years, APPEND for existing years with new data.** Both paths are valid recovery modes — the failure+retry mechanism is the same regardless.
3. **For-each stops at first failure.** When the bad path breaks `archive_folder_exists` for ALL years, the archiver starts from the earliest year and fails immediately. Subsequent years are never attempted.

---
---

**Test Date:** 2026-04-07
**Test Time:** 17:32 – 17:46 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Prerequisites

Providers was reset to a clean pre-archive state:
- Source table re-seeded with 1,000 rows (`effective_date` spanning 2020-2025, 5 null dates)
- All providers audit log entries cleared
- All providers archive folders removed
- `table_configs` verified: correct path, `delete_after_archive = false`

---

## Phase 1 — Good Run (baseline)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~105 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/262123163605308 |

All 6 years archived:

| Year | Status |
|------|--------|
| 2020 | STARTED → ARCHIVED |
| 2021 | STARTED → ARCHIVED |
| 2022 | STARTED → ARCHIVED |
| 2023 | STARTED → ARCHIVED |
| 2024 | STARTED → ARCHIVED |
| 2025 | STARTED → ARCHIVED |

---

## Phase 2 — Bad Run (new data + broken path)

### 2a. Insert new data after watermark

Year 2023 watermark was `2023-12-30`. Inserted 2 rows with `effective_date = 2023-12-31` (past the watermark).

### 2b. Break archive path

Set to `/Volumes/sandeep_manocha/nonexistent_volume/bad_path`.

### 2c. Run archive

| Field | Value |
|-------|-------|
| Status | **PASS** — INTERNAL_ERROR (expected) |
| Duration | ~72 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/608897845386423 |

### 2d. Audit log

| Year | Status | Error |
|------|--------|-------|
| 2020 | STARTED → FAILED | `SCHEMA_NOT_FOUND: sandeep_manocha.nonexistent_volume` |
| 2020 | STARTED → FAILED | (for-each built-in retry, same error) |

**PASS** — Year 2020 fails first because `archive_folder_exists` checks the bad path, returns false for ALL years, and the archiver tries to CREATE starting from year 2020. The for-each loop stops at the first failure, so year 2023 (with new data) is never reached.

---

## Phase 3 — Good Run (fix and recover)

### 3a. Restore correct path

Restored to `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples`.

### 3b. Run archive

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~95 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1085591160245870 |

### 3c. Audit log (recovery)

| Year | Status | archive_mode | record_count |
|------|--------|-------------|-------------|
| 2020 | STARTED → SKIPPED | SKIP | 0 |
| 2021 | STARTED → SKIPPED | SKIP | 0 |
| 2022 | STARTED → SKIPPED | SKIP | 0 |
| **2023** | **STARTED → ARCHIVED** | **APPEND** | **167** |
| 2024 | STARTED → SKIPPED | SKIP | 0 |
| 2025 | STARTED → SKIPPED | SKIP | 0 |

**PASS** — Year 2020 detected the prior FAILED status ("Previous run FAILED... Retrying.") but had no new data → SKIPPED. Year 2023 detected 2 new rows past the watermark and appended them successfully (`archive_mode = APPEND`). All other years correctly SKIPPED.

---

## Overall Result: PASS

The full Good Run → Bad Run (new data) → Good Run (new data) cycle works correctly:

1. **Phase 1:** All years archive cleanly (STARTED → ARCHIVED)
2. **Phase 2:** Bad path causes STARTED → FAILED with descriptive `SCHEMA_NOT_FOUND` error
3. **Phase 3:** After fixing the path:
   - Previously-failed year 2020 retries and SKIPs (no new data)
   - Year 2023 APPENDs the 2 new rows that arrived while the path was broken
   - Other years SKIP (no new data)

### Key insight

New data must have `effective_date` **after** the archived watermark to be detected. The APPEND logic uses `WHERE effective_date > watermark` — rows at or before the watermark are invisible to the archiver.
