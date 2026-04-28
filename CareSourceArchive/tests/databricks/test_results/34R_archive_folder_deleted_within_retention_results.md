# 34 — Archive Folder Deleted While Inside Retention Window (silent re-CREATE) Results

## Run — 2026-04-26 14:42 CDT

**Branch:** `feat/delta_config_build_v10_test_cases`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Pre-flight archive_run_id (claims/2020 baseline):** `aa12dba6-4aa3-440a-b1c9-c6a0391da112`
**Phase 2c re-CREATE archive_run_id:** `f4ecc378-f05b-4055-9409-b9d20b87fa94`
**Phase 3a SKIP archive_run_id:** `283ed984-ac7c-45e4-b927-4957f5ebb329`

### TL;DR

Deleted `claims/year_2020` folder, audit still said `ARCHIVED`. Archiver detected `MISSING + ARCHIVED`, re-CREATEd the folder from source with the same record_count (623), and re-run produced SKIPPED. Self-heal works silently with no FAILED row. **PASS.**

---

## Phase 1 — Establish preconditions: **PASS**

### 1a — claims/2020 ARCHIVED row: PASS

```
status=ARCHIVED, record_count=623, archive_run_id=aa12dba6-4aa3-440a-b1c9-c6a0391da112,
created_at=2026-04-26T17:58:39.618Z
```

`pre_count = 623`.

### 1b — Folder + Delta count: PASS

Folder listed, `archive_rows = 623` (matches `pre_count`).

### 1c — Source row count: PASS

`source_rows = 623` (matches `pre_count`).

### 1d — Eligibility: PASS

`retention_cutoff_year = 2026, year_2020_eligibility = eligible_for_archive`. With `default_retention_years = 0`, every year ≤ 2026 is eligible.

---

## Phase 2 — Delete the folder + Re-run: **PASS**

### 2a / 2b — Delete folder, verify gone: PASS

```bash
databricks fs rm dbfs:/Volumes/.../source_data_samples/claims/year_2020 --recursive
# verify
databricks fs ls dbfs:/Volumes/.../source_data_samples/claims/year_2020
# Error: no such directory
```

### 2c — Run archive (claims only): PASS

Bundle run TERMINATED SUCCESS in ~138s.
Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/529400473907940

### 2d — Audit log (last 5 rows for claims/2020): PASS

| status | archive_mode | record_count | archive_run_id | created_at |
|---|---|---|---|---|
| ARCHIVED | CREATE | 623 | `f4ecc378…` (new) | 2026-04-26T19:39:45.977Z |
| STARTED | (null) | 0 | `f4ecc378…` (new) | 2026-04-26T19:39:28.167Z |
| SKIPPED | SKIP | 0 | `4d94680d…` (clean-slate run #2) | 2026-04-26T19:24:31.737Z |
| STARTED | (null) | 0 | `4d94680d…` | 2026-04-26T19:24:24.235Z |
| ARCHIVED | CREATE | 623 | `aa12dba6…` (clean-slate run #1) | 2026-04-26T17:58:39.618Z |

Newest two rows are the new run's STARTED → ARCHIVED (CREATE) pair. `record_count = 623 = pre_count`. No FAILED row.

### 2e — Folder recreated: PASS

Folder lists `_archive_metadata.json`, `_delta_log/`, plus parquet part files. `archive_rows = 623`.

### 2f — No FAILED rows: PASS

`failed_rows = 0` for the run window.

---

## Phase 3 — Re-run again (expect SKIP): **PASS**

### 3a — Re-run archive: PASS

Bundle run TERMINATED SUCCESS in ~103s.
Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/544194261837964

### 3b — Audit log (last 3 rows): PASS

| status | archive_mode | record_count | archive_run_id |
|---|---|---|---|
| SKIPPED | SKIP | 0 | `283ed984…` (newest) |
| STARTED | (null) | 0 | `283ed984…` |
| ARCHIVED | CREATE | 623 | `f4ecc378…` (Phase 2 re-CREATE) |

System back to steady state — no new data to archive, so SKIP.

---

## Phase 4 — Cleanup

Not needed. Folder rebuilt, audit history preserved.

---

## What this proves

| Behavior | Pass criteria | Result |
|---|---|---|
| Folder deleted while source still has data | Archiver detected `MISSING + ARCHIVED` → CREATE | **PASS** |
| Re-run rebuilt folder from source | New `ARCHIVED` row, `archive_mode = CREATE`, `record_count = 623 = pre_count` | **PASS** |
| No FAILED row, no operator action | Self-heal was silent, `failed_rows = 0` | **PASS** |
| Subsequent re-run is idempotent | Newest row is `SKIPPED` | **PASS** |
| Year is eligible for archive | `eligible_for_archive` (cutoff = 2026 with retention=0) | **PASS** |

## What Happened

Pre-flight confirmed `claims/year_2020` had an `ARCHIVED` audit row (record_count = 623), the archive folder existed with 623 Delta rows, the source still had 623 rows for 2020, and 2020 was eligible for archive under the seeded retention=0 cutoff of 2026. We then ran `dbutils.fs.rm` on the year_2020 folder via the CLI and confirmed it returned "no such directory" on the next ls. Running the archive job scoped to `claims` produced a new `STARTED → ARCHIVED (CREATE)` pair with the same `record_count = 623` and a fresh archive_run_id. The folder was rebuilt with `_delta_log` and parquet parts. No FAILED rows. A second archive run produced `SKIPPED` (no new data), confirming idempotence.

## Next Steps

Proceed to 35T (claims retention oscillation, DAA=false). 34T leaves the audit log with extra `STARTED/ARCHIVED/SKIPPED` rows for claims/2020, but 35T's verification queries scope by archive_run_id and year, so they are unaffected.
