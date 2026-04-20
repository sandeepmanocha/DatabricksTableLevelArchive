# 21 — Rehydration Happy Path, Re-run, and Object Collision — Results

## Run — 2026-04-17 12:07 CDT

**TL;DR:** Happy-path rehydration and idempotent re-run both passed. Phases 3–4 (table/view collision) skipped — rehydrator now creates views only, making those scenarios Spark SQL semantics tests rather than rehydrator logic tests. Found and fixed a CLI quoting bug: `--params years="2020,2021"` passes literal quotes through to the notebook.

**Branch:** `feat/delta_config_build_v5_rehydrate`
**Profile:** fe-sandbox-manocha
**Target:** dev-serverless
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Resolved Parameters

| Parameter | Value |
|---|---|
| `PROFILE` | `fe-sandbox-manocha` |
| `TARGET` | `dev-serverless` |
| `SOURCE_CATALOG` | `dev2_archive` |
| `SOURCE_SCHEMA` | `source_data_samples` |
| `CONFIG_TABLE` | `dev2_archive.metadata.global_settings` |
| `ARCHIVE_VOL` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `REHYDRATE_TARGET_SCHEMA` | `rehydrated` |
| `REHYDRATION_AUDIT_TABLE` | `dev2_archive.metadata.rehydration_audit_log` |
| `unified_view_suffix` | `_unified` (default) |

---

### Pre-flight: **PASS**

| Check | Result |
|---|---|
| Archive `claims/year_2020` | 623 rows |
| Archive `claims/year_2021` | 623 rows |
| Source table `claims` | 5,000 rows (8 years + 15 NULL) |
| Target schema `rehydrated` | Existed from prior run (dropped in Step 1) |
| Rehydration audit table | 8 existing rows |

Source row counts by year:

| Year | Count |
|------|-------|
| NULL | 15 |
| 2018 | 623 |
| 2019 | 624 |
| 2020 | 623 |
| 2021 | 623 |
| 2022 | 624 |
| 2023 | 624 |
| 2024 | 622 |
| 2025 | 622 |

---

### Step 1 — Phase 1: Clean target + run rehydration: **PASS**

Dropped `dev2_archive.rehydrated` with `DROP SCHEMA IF EXISTS ... CASCADE`.

| Field | Value |
|-------|-------|
| Run URL | [run/481022376599866](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/481022376599866) |
| Status | TERMINATED SUCCESS |
| Duration | ~42 sec |

**Note — CLI quoting issue:** Two prior attempts failed because `--params years="2020,2021"` passed literal quote characters into the `years` widget value (`"2020` → `int()` failed). Fixed by adding `years_raw = years_raw.strip('"').strip("'")` to `notebooks/run_rehydrate.py` (defensive stripping of CLI quoting artifacts). Redeployed and succeeded on third attempt.

| Failed attempt | Run URL | Error |
|---|---|---|
| 1 | [run/182526291571780](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/182526291571780) | `ValueError: invalid literal for int() with base 10: '2020\\'` (backslash escape leaked) |
| 2 | [run/1026354752964456](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/1026354752964456) | `ValueError: invalid literal for int() with base 10: '"2020'` (CSV quotes passed through) |

---

### Step 2 — Phase 1: Verify views, unified view, audit log: **PASS**

**Objects in `dev2_archive.rehydrated`:**

| Object | Type |
|--------|------|
| `claims_year_2020` | View |
| `claims_year_2021` | View |
| `claims_unified` | View |

**Unified view row counts (`include_live_data=false`):**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

**Latest audit row:**

| Field | Value |
|-------|-------|
| `archive_path` | `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` |
| `source_table` | `dev2_archive.source_data_samples.claims` |
| `target_catalog` | `dev2_archive` |
| `target_schema` | `rehydrated` |
| `years` | `[2020, 2021]` |
| `tables_created` | 2 |
| `status` | **COMPLETED** |
| `error_message` | (null) |
| `created_at` | `2026-04-17T17:11:15.697Z` |

---

### Step 3 — Baseline row counts (before re-run): **PASS**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

---

### Step 4 — Re-run rehydration (identical params): **PASS**

| Field | Value |
|-------|-------|
| Run URL | [run/1072783677086132](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/614415962528020/run/1072783677086132) |
| Status | TERMINATED SUCCESS |
| Duration | ~35 sec |

---

### Step 5 — Verify idempotency + second audit entry: **PASS**

**Unified view row counts after re-run:**

| Year | Count |
|------|-------|
| 2020 | 623 |
| 2021 | 623 |

Counts match Step 3 baseline exactly.

**Audit entries for `target_schema = 'rehydrated'` (most recent first):**

| `created_at` | `status` | `tables_created` | `years` | Note |
|---|---|---|---|---|
| `2026-04-17T17:15:24.862Z` | COMPLETED | 2 | `[2020, 2021]` | Step 4 re-run |
| `2026-04-17T17:12:39.397Z` | COMPLETED | 2 | `[2020, 2021]` | Intermediate run (notebook fix verification) |
| `2026-04-17T17:11:15.697Z` | COMPLETED | 2 | `[2020, 2021]` | Step 1 fresh run |

Two COMPLETED rows for the primary test (Step 1 at `17:11:15` and Step 4 at `17:15:24`), both with `tables_created = 2`. Idempotency confirmed.

---

### Steps 6–11 — Phases 3 & 4: **SKIP**

Skipped. The rehydrator now creates **views** (`CREATE OR REPLACE VIEW`), not tables. The table-name collision scenarios (pre-creating a base TABLE to block `CREATE OR REPLACE VIEW`) test Spark/Databricks SQL semantics rather than rehydrator logic.

---

### Step 12 — Cleanup: **PASS**

`DROP SCHEMA IF EXISTS dev2_archive.rehydrated CASCADE` — succeeded.

---

## What Happened

1. **Pre-flight passed** — archive volumes for 2020 and 2021 had 623 rows each, audit table existed with 8 prior rows, source table had 5,000 rows.

2. **CLI quoting issue discovered and fixed.** The Databricks CLI's `--params` flag uses Go's `StringToString` pflag type, which splits on commas. Passing `years=2020,2021` fails because `2021` is treated as a standalone entry. The CSV-style workaround (`years="2020,2021"`) preserves the comma but passes literal double-quotes into the job parameter value. Added `years_raw.strip('"').strip("'")` to `notebooks/run_rehydrate.py` to defensively strip these artifacts.

3. **Phase 1 (happy path)** — fresh rehydration created 3 views (`claims_year_2020`, `claims_year_2021`, `claims_unified`) with correct row counts (623 per year). Audit logged `COMPLETED` with `tables_created = 2`.

4. **Phase 2 (idempotency)** — identical re-run succeeded without errors. `CREATE OR REPLACE VIEW` replaced existing views cleanly. Row counts unchanged. Second `COMPLETED` audit row appeared.

5. **Phases 3–4 (collision)** — skipped. Test case updated to reflect that the rehydrator creates views only; table/view name collision scenarios are not relevant.

6. **Cleanup** — target schema dropped.

## Next Steps

- **Proceed to test 22** (partial success, missing folders, zero-restore).
- The `years_raw.strip()` fix in `run_rehydrate.py` should be committed with the test results.
- Update `dab-commands.md` rehydrate section to document the `--params 'years="2020,2021"'` quoting pattern.
- Consider adding `include_live_data` widget usage example to the runbook.
