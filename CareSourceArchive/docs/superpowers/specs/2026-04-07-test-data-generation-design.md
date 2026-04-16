# Test Data Generation — Design Spec

**Date:** 2026-04-07
**Status:** FINAL
**Branch:** `feat/delta_config_build_v2_reruns`

---

## 1. Purpose

Create realistic healthcare test data in `sandeep_manocha.source_data_samples` for end-to-end interactive testing of the archive system. Covers happy path archiving, failure scenarios (UC-1 through UC-6 from the handle-failed-archives spec), manual intervention steps, re-runs, and parallel ForEach behavior.

---

## 2. Approach: 3 Tables

Three source tables with different watermark columns, row counts, and year ranges. This is the minimum set that exercises every scenario:

- **ForEach parallel testing (UC-4):** Requires 3+ tables
- **Scanner pattern matching:** Different watermark column names per table
- **Multi-year failure (UC-1):** Claims has 6 archivable years to walk through sequentially

The failure scenarios (orphan folders, stale STARTED, partial writes) are about **system state** — audit entries and archive folders — not the data itself. The data just needs to flow through the archiver.

---

## 3. Table Schemas

### 3.1 `claims` (~5,000 rows)

| Column | Type | Notes |
|--------|------|-------|
| `claim_id` | STRING | PK, `CLM-000001` through `CLM-005000` |
| `member_id` | STRING | FK-like, `MBR-XXXXX` |
| `provider_id` | STRING | FK-like, `PRV-XXXX` |
| `claim_type` | STRING | Medical / Dental / Pharmacy / Vision |
| `diagnosis_code` | STRING | ICD-10 style, e.g., `J06.9`, `E11.9` |
| `amount` | DECIMAL(10,2) | $10 – $50,000 |
| `status_flag` | STRING | Closed (~80%), Active (~15%), Pending (~5%) |
| `event_date` | DATE | **Watermark column** — 2018-2025, ~625/year |
| `load_timestamp` | TIMESTAMP | Simulated ETL load time (event_date + 1-30 days) |

- ~15 rows with NULL `event_date` (tests NULL date error logging)
- `status_flag = 'Active'` supports exclusion condition testing
- Matches scanner pattern `event_date`

### 3.2 `members` (~3,000 rows)

| Column | Type | Notes |
|--------|------|-------|
| `member_id` | STRING | PK, `MBR-00001` through `MBR-03000` |
| `first_name` | STRING | Faker-generated |
| `last_name` | STRING | Faker-generated |
| `date_of_birth` | DATE | Random 1940-2010 |
| `plan_type` | STRING | HMO / PPO / EPO / POS |
| `enrollment_status` | STRING | Active / Inactive / Terminated |
| `start_date` | DATE | **Watermark column** — 2019-2025, ~430/year |

- ~10 rows with NULL `start_date`
- Won't match default scanner patterns (demonstrates "unmatched" — watermark_column set manually in table_configs)

### 3.3 `providers` (~1,000 rows)

| Column | Type | Notes |
|--------|------|-------|
| `provider_id` | STRING | PK, `PRV-0001` through `PRV-1000` |
| `provider_name` | STRING | Faker-generated |
| `specialty` | STRING | Cardiology / Oncology / Pediatrics / etc. |
| `npi` | STRING | 10-digit NPI-like number |
| `effective_date` | DATE | **Watermark column** — 2020-2025, ~167/year |
| `state` | STRING | US state code |
| `is_active` | BOOLEAN | True (~85%), False (~15%) |

- ~5 rows with NULL `effective_date`
- Smallest table — good candidate for "the one that fails" in UC-4

---

## 4. Data Distribution

With `retention_years = 3` and current year 2026, cutoff = 2023.

| Table | Years in data | Eligible for archive (≤2023) | Too recent (2024+) |
|-------|--------------|-------------------------------|---------------------|
| claims | 2018-2025 | 2018, 2019, 2020, 2021, 2022, 2023 (6 years) | 2024, 2025 |
| members | 2019-2025 | 2019, 2020, 2021, 2022, 2023 (5 years) | 2024, 2025 |
| providers | 2020-2025 | 2020, 2021, 2022, 2023 (4 years) | 2024, 2025 |

All generators use a **fixed random seed** (42) for reproducibility — re-running the notebook produces identical data.

---

## 5. Notebook Structure

Single notebook at `tests/interactive/generate_test_data.py`.

| Cell | Content |
|------|---------|
| 1 | `%pip install faker` + restart Python |
| 2 | Widgets: `catalog` (default `sandeep_manocha`), `schema` (default `source_data_samples`) |
| 3 | Verify catalog and schema exist (raise if not — no auto-creation) |
| 4 | Generate and write `claims` table |
| 5 | Generate and write `members` table |
| 6 | Generate and write `providers` table |
| 7 | Summary: row counts per table per year, NULL counts, status distributions |
| 8 | Testing playbook (markdown) — what to do next, step by step |

Table creation uses `CREATE TABLE IF NOT EXISTS` + `INSERT OVERWRITE` — idempotent, safe to re-run. No classes, no helper modules, all inline.

---

## 6. Testing Playbook

After generating data, the user follows these high-level steps. Each step references the DABs bundle commands and the relevant use case from the handle-failed-archives spec.

### Step 0: Prerequisites

- Deploy the bundle: `databricks bundle deploy -t dev`
- Ensure config tables are seeded: run `setup_config_tables` job, then `seed_config` notebook
- Ensure `table_configs` has entries for claims, members, providers with `retention_years = 3` and correct `watermark_column` values
- Ensure archive base path volume exists and is covered by a UC external location

### Step 1: Happy Path — Dry Run

```
databricks bundle run caresource_archive_run -t dev \
  --params config_table=sandeep_manocha.caresource_audit.global_settings,dry_run=true
```

**What to check:** Job succeeds. Each table shows eligible years, would-archive counts, per-condition exclusion counts (if any), NULL date counts. No data moves.

### Step 2: Happy Path — Live Archive

```
databricks bundle run caresource_archive_run -t dev \
  --params config_table=sandeep_manocha.caresource_audit.global_settings,dry_run=false
```

**What to check:** All 3 tables archive successfully. Audit table has STARTED → ARCHIVED rows for each table+year. Archive folders exist in the volume. Watermark values recorded.

### Step 3: Happy Path — Re-run (Redundant)

Run the same command again.

**What to check:** All years SKIP (no new data above watermark). Audit table shows SKIPPED rows with `archive_mode = SKIP`. Fast — just count queries.

### Step 4: UC-5 — Intentional Re-run (New Data)

Insert a few new rows into `claims` for an already-archived year (e.g., year 2022 with `event_date` after the current watermark). Re-run the archive job.

**What to check:** Year 2022 gets APPEND mode. New watermark recorded. Other years SKIP.

### Step 5: UC-1 — Multi-Year Failure

To simulate a mid-year failure: either rename/drop the `providers` table while the job is running (hard), or insert a row with an intentionally corrupt value that causes a Spark error. The simpler approach is to temporarily revoke access to the archive path for one table.

**What to check:** Failed table has STARTED → FAILED in audit with error message. Subsequent years for that table were never attempted. Other tables (claims, members) succeeded independently.

### Step 6: UC-2 — Orphan Folder Detection

Manually create an empty Delta-like folder at an archive path for a year that has no ARCHIVED audit entry (e.g., create a folder at the archive path for `providers/year_2023` but leave no audit row).

Re-run the archive job.

**What to check:** The job raises `ArchiveOperationError` with "orphan folder" message and actionable resolution steps. The job does NOT delete or overwrite the folder.

### Step 7: UC-2 Resolution — Manual Cleanup

Follow the error message instructions:
1. Inspect the orphan folder: `SELECT COUNT(*) FROM delta.\`<path>\``
2. If corrupt/partial: delete the folder from cloud storage
3. Re-run the archive job

**What to check:** After deleting the folder, re-run succeeds with CREATE mode for that year.

### Step 8: UC-3 — Re-run After Failure

After Step 5's failure is fixed (restore access, fix the data issue), re-run.

**What to check:** Previously-failed year retries with INFO log "Previous run FAILED... Retrying." Already-archived years SKIP via watermark. Audit trail shows FAILED → STARTED → ARCHIVED sequence.

### Step 9: UC-4 — Parallel ForEach Failure

Run all 3 tables. Cause `providers` to fail (e.g., drop the providers source table just before the job starts, or make its archive path inaccessible).

**What to check:** Claims and members succeed. Providers fails. Databricks job UI shows the mixed result across ForEach iterations. Re-run with `table_config_filter` targeting just the failed table.

### Step 10: UC-6 — Stale STARTED Detection

Insert a fake STARTED audit row with an old `created_at` timestamp (e.g., 24 hours ago) for a table+year that hasn't been archived yet, using a different `archive_run_id`. Re-run.

**What to check:** The system detects the stale STARTED (age > 4 hour threshold), logs a WARNING about the abandoned run, and proceeds to the normal decision tree. If no orphan folder → CREATE succeeds. If orphan folder exists → ERROR per UC-2.

### Step 11: Rehydration (Happy Path)

After successful archives, run the rehydrate job to restore a year:

```
databricks bundle run caresource_rehydrate -t dev \
  --params config_table=sandeep_manocha.caresource_audit.global_settings,source_table=claims,years=2020,target_catalog=sandeep_manocha,target_schema=caresource_rehydrated,archive_base_path=<archive_base_path from table_configs>
```

The `archive_base_path` value comes from the `table_configs` entry for claims (e.g., `/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims`).

**What to check:** External table created. Unified view (if created) combines current + restored data. Rehydration audit log has a SUCCESS entry.

---

## 7. Files

| File | Description |
|------|-------------|
| `tests/interactive/generate_test_data.py` | Databricks notebook — generates all 3 tables with Faker |
| `docs/superpowers/specs/2026-04-07-test-data-generation-design.md` | This spec |
