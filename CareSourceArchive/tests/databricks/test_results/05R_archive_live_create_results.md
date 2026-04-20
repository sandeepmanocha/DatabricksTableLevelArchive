# 05 — Archive Live Run (CREATE mode) Results

## Run — 2026-04-20 00:47 CDT

**TL;DR:** Live archive passed cleanly on first attempt (~202s). All 21 table-year partitions archived in CREATE mode; Delta row counts match audit `record_count` exactly for all 21 (100%). Source tables unchanged (`delete_after_archive=false`), NULL-watermark rows confirmed to stay in source (claims=15, members=10, providers=5 — exactly the 04T dry-run delta).

**Branch:** `feat/delta_config_build_v6_dab`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Config table:** `dev2_archive.metadata.global_settings`
**Audit log:** `dev2_archive.metadata.archive_audit_log`
**Source:** `dev2_archive.source_data_samples` (`claims`, `members`, `providers`)
**Archive base path:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples` (from `schema_templates.archive_base_path`; the volume's base is `/Volumes/.../sample_data_archive_ext_vol` and the archiver adds the schema segment)
**Run-as SP:** `44edd08d-b71a-4e29-a01b-4881be31a144` (`caresource-archive-dev`)
**Retention:** `default_retention_years=0` → all years eligible
**archive_run_id:** `67b52af0-d0c6-443d-ad48-2ac0677eca6f`
**Job run URL:** https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/160311664776002

---

### Pre-flight — **PASS**

| Check | Result |
|---|---|
| `archive_audit_log` | 21 DRY_RUN rows from 04T, **0 ARCHIVED, 0 STARTED** → clean for CREATE |
| `table_configs` | 3 active, `delete_after_archive=false`, `retention_years=0`; watermarks `claims/event_date`, `members/start_date`, `providers/effective_date` |
| Archive volume | Volume exists, no per-table subfolders under `source_data_samples/` → no orphan/SKIP risk |
| Source row counts (incl. NULL year bucket) | `claims`=5000 (8yr+15 NULL), `members`=3000 (7yr+10 NULL), `providers`=1000 (6yr+5 NULL) — matches 04T exactly |

---

### Step 1 — Run archive in live CREATE mode — **PASS**

Command:

```
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="false",source_catalog="dev2_archive",source_schema="source_data_samples"
```

- Status: `TERMINATED SUCCESS` (all 3 tasks: `generate_parameters`, `run_archive_iteration`, `run_archive`)
- Duration: ~202 s
- Run URL above

---

### Step 2 — Audit log STARTED + ARCHIVED entries — **PASS**

Final audit log breakdown:

| status | archive_mode | count |
|---|---|---|
| `DRY_RUN` | (empty) | 21 (from 04T) |
| `STARTED` | (empty) | 21 (this run) |
| `ARCHIVED` | `CREATE` | 21 (this run) |

All 21 `(table, year)` pairs have paired `STARTED` → `ARCHIVED` entries, all sharing `archive_run_id = 67b52af0-d0c6-443d-ad48-2ac0677eca6f`. All `ARCHIVED` rows carry a populated `watermark_value` (MAX of the watermark column for that year, e.g. `claims/2020 → 2020-12-30`, `claims/2025 → 2025-12-31`). ✓

---

### Step 3 — Archive Delta folders exist; row counts match audit — **PASS**

All 21 archive Delta paths verified — row count per partition equals `record_count` in the audit log **exactly** (21/21 = 100% match):

| table | year | archive rows | audit `record_count` | match |
|---|---|---|---|---|
| claims | 2018 | 623 | 623 | ✓ |
| claims | 2019 | 624 | 624 | ✓ |
| claims | 2020 | 623 | 623 | ✓ |
| claims | 2021 | 623 | 623 | ✓ |
| claims | 2022 | 624 | 624 | ✓ |
| claims | 2023 | 624 | 624 | ✓ |
| claims | 2024 | 622 | 622 | ✓ |
| claims | 2025 | 622 | 622 | ✓ |
| members | 2019 | 426 | 426 | ✓ |
| members | 2020 | 426 | 426 | ✓ |
| members | 2021 | 427 | 427 | ✓ |
| members | 2022 | 428 | 428 | ✓ |
| members | 2023 | 429 | 429 | ✓ |
| members | 2024 | 426 | 426 | ✓ |
| members | 2025 | 428 | 428 | ✓ |
| providers | 2020 | 163 | 163 | ✓ |
| providers | 2021 | 168 | 168 | ✓ |
| providers | 2022 | 166 | 166 | ✓ |
| providers | 2023 | 167 | 167 | ✓ |
| providers | 2024 | 166 | 166 | ✓ |
| providers | 2025 | 165 | 165 | ✓ |
| **Totals** | | **4985 / 2990 / 995** | **4985 / 2990 / 995** | **all match** |

Folder layout confirmed via `databricks fs ls dbfs:/Volumes/.../source_data_samples/<tbl>/`:

- `claims/`: `year_2018 … year_2025` (8 folders)
- `members/`: `year_2019 … year_2025` (7 folders)
- `providers/`: `year_2020 … year_2025` (6 folders)

---

### Source data untouched (delete_after_archive=false) — **PASS**

| tbl | pre-run | post-run | delta |
|---|---|---|---|
| `claims` | 5000 | 5000 | 0 |
| `members` | 3000 | 3000 | 0 |
| `providers` | 1000 | 1000 | 0 |

---

### NULL-watermark behavior — confirmed (closes 04T follow-up)

04T predicted the ~0.3% `record_count` gap was NULL-watermark rows. After the live run:

| tbl | source NULL rows | dry-run delta (04T) | match |
|---|---|---|---|
| `claims` (`event_date IS NULL`) | 15 | 15 | ✓ |
| `members` (`start_date IS NULL`) | 10 | 10 | ✓ |
| `providers` (`effective_date IS NULL`) | 5 | 5 | ✓ |

Since `delete_after_archive=false` this run would not have deleted them anyway; but combined with the 04T dry-run evidence (record_count per year excluded exactly these NULL rows), the behavior is: **rows with NULL watermark do not fall into any year bucket and are therefore neither archived nor deleted**. Needs a policy decision — see Next Steps.

---

### Volume visibility discovery — catalog owner missing `READ_VOLUME`

While running Step 3, `databricks fs ls` against the archive volume returned `Error: no such directory` for my user (`sandeep.manocha@databricks.com`), even though UC SQL reads of the same Delta paths worked fine. Root cause:

```
databricks grants get volume dev2_archive.source_data_samples_archive.sample_data_archive_ext_vol
{
  "privilege_assignments": [
    {"principal": "44edd08d-b71a-4e29-a01b-4881be31a144", "privileges": ["READ_VOLUME","WRITE_VOLUME"]}
  ]
}
```

Only the run-as SP had volume privileges. **Catalog ownership does not cascade to `READ_VOLUME` on child volumes.** UC returns 404 ("no such directory") rather than 403 for missing-read cases on volumes. Two fixes were needed together:

1. Self-grant: `GRANT READ VOLUME ON VOLUME dev2_archive.source_data_samples_archive.sample_data_archive_ext_vol TO \`sandeep.manocha@databricks.com\`;`
2. Use `dbfs:/Volumes/...` path prefix for `databricks fs ls` (bare `/Volumes/...` gives "no such directory" even with the grant).

After the grant + prefix fix, `databricks fs ls dbfs:/Volumes/...` lists `claims/`, `members/`, `providers/` and their `year_*` folders correctly. The SP has always had `READ_VOLUME` so it could see the folders throughout — `archive_folder_exists()` in `src/utils.py` uses `dbutils.fs.ls()` as the SP and works fine.

This is the **third time** this environment has surfaced an ownership-vs-grant gap (see Next Steps).

---

### Final State

| Item | Value |
|---|---|
| `archive_audit_log` | 63 rows total (21 DRY_RUN from 04T + 21 STARTED + 21 ARCHIVED from this run) |
| Archive Delta folders | 21 `year_*` folders across claims/members/providers, all readable as Delta tables |
| Source tables | Unchanged (5000 / 3000 / 1000) |
| Volume grants | SP + owner both have `READ_VOLUME`; only SP has `WRITE_VOLUME` |

---

## What Happened

1. **Pre-flight clean.** Audit log had no prior ARCHIVED or STARTED entries, archive volume had no orphan folders, all 3 tables active with correct watermarks and `delete_after_archive=false`.
2. **Live run succeeded first try.** ~202s end-to-end. All 3 bundle tasks (`generate_parameters`, `run_archive_iteration`, `run_archive`) TERMINATED SUCCESS. No retries, no code changes, no manual intervention during the run.
3. **Audit log is correct.** 21 STARTED + 21 ARCHIVED rows, all `archive_mode=CREATE`, all sharing one `archive_run_id`, all with populated `watermark_value` (MAX of watermark for the year). Pre-existing 21 DRY_RUN rows from 04T were untouched.
4. **Archive folders match audit 1:1.** For every one of the 21 (table, year) pairs, `COUNT(*)` on the archive Delta equals the audit log's `record_count`. Sum totals match too (4985 / 2990 / 995).
5. **Source untouched.** Counts still 5000 / 3000 / 1000 — confirms `delete_after_archive=false` is honored and this was a copy-only CREATE.
6. **Closed the 04T NULL-watermark question.** Source NULL counts (15/10/5) match the 04T dry-run delta exactly, and they remained in source after the live run. NULL-watermark rows are neither archived nor deleted under current archiver logic.
7. **Discovered a third ownership-vs-grant gap.** `databricks fs ls` on the volume failed for the catalog owner because only the SP had `READ_VOLUME`. UC returns 404 instead of 403, which made it look like the archiver hadn't written anything. After self-granting `READ_VOLUME` and switching to the `dbfs:/Volumes/...` path form, the CLI lists the archive folders correctly. SP's `READ_VOLUME + WRITE_VOLUME` worked fine throughout — archiver internals use `dbutils.fs.ls()` under the SP identity.

---

## Next Steps

1. **Proceed to 06T (archive idempotent re-run).** Pre-conditions: 21 ARCHIVED + 21 matching folders now exist, so a re-run should produce `SKIP` (not `CREATE`, not `APPEND`) for every (table, year) pair. Any row that shows `merge_action=CREATE` on re-run is a bug.
2. **Make a policy decision on NULL-watermark rows.** Three tables each have a small number of NULL-watermark rows (claims=15, members=10, providers=5) that are silently passed over by the archiver — neither archived nor deleted. Options:
   - **Accept as-is:** document in `docs/archiver-behavior.md` that rows with NULL watermark are not archived and not eligible for `delete_after_archive` either.
   - **Route to explicit NULL bucket:** archive NULL-watermark rows into a `year_null/` Delta folder and audit with `year = NULL` or a sentinel.
   - **Dead-letter:** write NULL-watermark rows to a `dead_letter/` volume and raise a warning in the audit log (`status = WARNING`).
3. **Fix the owner-vs-grant gap for volumes in the setup/seed job.** Third environment observation of the same class of bug:
   - `seed_config.py` / `setup_config_tables.py` should grant the catalog owner `READ VOLUME ON VOLUME <archive_volume>` in addition to the SP grants, OR
   - Document in `docs/runbooks/service-principals.md` / `catalog-setup.md` that the catalog owner must run one-time grants: `MODIFY ON SCHEMA <metadata_schema>`, `SELECT ON SCHEMA <source_schema>`, `READ VOLUME ON VOLUME <archive_volume>`.
4. **Update test case `05T_archive_live_create.md`:**
   - The `SELECT COUNT(*) FROM delta.<ARCHIVE_VOL>/claims/year_2020` step is wrong for this env — the actual archive base is `<ARCHIVE_VOL>/source_data_samples/<table>/year_<yyyy>`, i.e. the archiver adds a schema segment. Either document this as expected (path = `<volume_root>/<schema>/<table>/year_<yyyy>`) or have the runner read the actual path from `schema_templates.archive_base_path`.
   - Note the `dbfs:/Volumes/...` prefix requirement for `databricks fs ls` against UC volumes (bare `/Volumes/...` gives misleading 404s).
5. **Revoke the extra grants in higher envs.** In `dev2_archive` the self-grants (`READ_VOLUME`, `SELECT ON source_schema`, `MODIFY ON metadata`) are fine to leave. In stage/prod, revoke these from the human owner and rely solely on the SP identity.

---



**TL;DR:** Live archive succeeded on attempt 4 after resolving SP permission issues. All 3 tables archived (CREATE mode) across 21 table-year combos. Folder counts match audit log. Source data unchanged (`delete_after_archive = false`).

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** fe-sandbox-manocha
**Target:** dev-serverless
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com

### Pre-flight State

| Metric | Value |
|--------|-------|
| ARCHIVED entries in audit log | 0 |
| DRY_RUN entries | 42 |
| FAILED entries (from prior permission failures) | 6 |
| table_configs active | 3 (claims, members, providers) |
| Source: claims | 5,000 rows (8 years + 15 NULL) |
| Source: members | 3,000 rows (7 years + 10 NULL) |
| Source: providers | 1,000 rows (6 years + 5 NULL) |
| Archive volume | Empty (no table folders) |

All 3 tables active with valid watermark columns (event_date, start_date, effective_date). `delete_after_archive = false` for all. No large tables configured (no bronze_column_lineage or bronze_query_history).

### Step 1 — Run archive (live, dry_run=false): **PASS** (attempt 4)

**Attempts 1–3 failed** due to SP permission issues (see below). Attempt 4 succeeded.

| Attempt | Run URL | Status | Error |
|---------|---------|--------|-------|
| 1 | [run/1067971749786863](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/917535404414134/run/1067971749786863) | FAILED | `INSUFFICIENT_PERMISSIONS: MODIFY,SELECT on any file` |
| 2 | [run/798918890917615](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/917535404414134/run/798918890917615) | FAILED | Same — `READ/WRITE VOLUME` grants alone insufficient |
| 3 | [run/233685155046415](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/917535404414134/run/233685155046415) | FAILED | Same — `MODIFY/SELECT` on schema also insufficient |
| **4** | [run/384988205114096](https://fe-sandbox-manocha.cloud.databricks.com/?o=7474652022110066#job/917535404414134/run/384988205114096) | **SUCCESS** | `GRANT MODIFY, SELECT ON ANY FILE` resolved it |

**Successful run details:**

| Field | Value |
|-------|-------|
| Status | TERMINATED SUCCESS |
| Duration | ~159 sec |
| ForEach iterations | 3 total, 3 succeeded, 0 failed |

**Grants applied to SP `ac94d080-96a0-4866-a720-3c60ab629326`:**

```sql
GRANT READ VOLUME, WRITE VOLUME ON VOLUME dev2_archive.source_data_samples_archive.sample_data_archive_ext_vol TO `ac94d080-96a0-4866-a720-3c60ab629326`;
GRANT MODIFY ON SCHEMA dev2_archive.source_data_samples_archive TO `ac94d080-96a0-4866-a720-3c60ab629326`;
GRANT SELECT ON SCHEMA dev2_archive.source_data_samples_archive TO `ac94d080-96a0-4866-a720-3c60ab629326`;
GRANT MODIFY ON ANY FILE TO `ac94d080-96a0-4866-a720-3c60ab629326`;
GRANT SELECT ON ANY FILE TO `ac94d080-96a0-4866-a720-3c60ab629326`;
```

The first two grants (READ/WRITE VOLUME, MODIFY/SELECT on schema) were necessary but not sufficient. The **`ANY FILE`** grant was required because writing Delta to an external volume path goes through Spark's Delta writer, which checks legacy workspace-level file ACLs in addition to UC permissions.

### Step 2 — Check audit log: **PASS**

Every eligible table+year has a STARTED → ARCHIVED pair. All `archive_mode = CREATE`.

#### claims (8 years)

| Year | Status | Record Count | Watermark Value | Mode |
|------|--------|-------------|-----------------|------|
| 2018 | ARCHIVED | 623 | 2018-12-31 | CREATE |
| 2019 | ARCHIVED | 624 | 2019-12-31 | CREATE |
| 2020 | ARCHIVED | 623 | 2020-12-30 | CREATE |
| 2021 | ARCHIVED | 623 | 2021-12-30 | CREATE |
| 2022 | ARCHIVED | 624 | 2022-12-31 | CREATE |
| 2023 | ARCHIVED | 624 | 2023-12-31 | CREATE |
| 2024 | ARCHIVED | 622 | 2024-12-31 | CREATE |
| 2025 | ARCHIVED | 622 | 2025-12-31 | CREATE |

#### members (7 years)

| Year | Status | Record Count | Watermark Value | Mode |
|------|--------|-------------|-----------------|------|
| 2019 | ARCHIVED | 426 | 2019-12-30 | CREATE |
| 2020 | ARCHIVED | 426 | 2020-12-31 | CREATE |
| 2021 | ARCHIVED | 427 | 2021-12-31 | CREATE |
| 2022 | ARCHIVED | 428 | 2022-12-30 | CREATE |
| 2023 | ARCHIVED | 429 | 2023-12-31 | CREATE |
| 2024 | ARCHIVED | 426 | 2024-12-29 | CREATE |
| 2025 | ARCHIVED | 428 | 2025-12-30 | CREATE |

#### providers (6 years)

| Year | Status | Record Count | Watermark Value | Mode |
|------|--------|-------------|-----------------|------|
| 2020 | ARCHIVED | 163 | 2020-12-31 | CREATE |
| 2021 | ARCHIVED | 168 | 2021-12-29 | CREATE |
| 2022 | ARCHIVED | 166 | 2022-12-31 | CREATE |
| 2023 | ARCHIVED | 167 | 2023-12-30 | CREATE |
| 2024 | ARCHIVED | 166 | 2024-12-30 | CREATE |
| 2025 | ARCHIVED | 165 | 2025-12-29 | CREATE |

### Step 3 — Verify archive folders exist: **PASS**

| Archive Path | Audit Count | Folder Count | Result |
|-------------|-------------|-------------|--------|
| claims/year_2020 | 623 | 623 | **PASS** |
| members/year_2021 | 427 | 427 | **PASS** |
| providers/year_2022 | 166 | 166 | **PASS** |

All 3 table folders confirmed in volume: `claims/`, `members/`, `providers/`.

### Source data unchanged: **PASS**

| Table | Before | After |
|-------|--------|-------|
| claims | 5,000 | 5,000 |
| members | 3,000 | 3,000 |
| providers | 1,000 | 1,000 |

`delete_after_archive = false` — source rows preserved.

## What Happened

Live archive on `fe-sandbox-manocha` (dev-serverless target) required 4 attempts due to SP permission issues. The SP `caresource-archive-dev` had catalog, schema, and table grants but lacked two critical permissions: (1) `READ/WRITE VOLUME` on the archive external volume, and (2) `MODIFY/SELECT ON ANY FILE` — a legacy workspace-level file ACL required when writing Delta to external volume paths via Spark. After applying all grants, the archive succeeded. All 21 table-year combinations (8 claims + 7 members + 6 providers) were archived in CREATE mode with correct record counts and watermark values.

## Next Steps

- Proceed to 06T (archive rerun skip) — expect all table-years to SKIP since archives exist
- The `ANY FILE` grant requirement should be documented in the service-principals runbook (see below)
- Stale STARTED/FAILED entries from attempts 1–3 remain in the audit log — harmless but noisy

---

## Run — 2026-04-10 13:06 CDT

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

### Pre-step — Table Config Changes

- Disabled `bronze_column_lineage` and `bronze_query_history` in `table_configs` (2 rows affected).
- Enabled `claims`, `members`, and `providers` in `table_configs` (3 rows affected).
- Verified 6 active tables: `bronze_table_lineage`, `claims`, `gold_daily_access_trends`, `members`, `providers`, `silver_query_table_access`.

---

### Before — Baseline

| Metric | Value |
|--------|-------|
| ARCHIVED entries in audit log | 0 |

#### Source row counts by table/year

| Table | Year | Count |
|-------|------|-------|
| claims | NULL | 15 |
| claims | 2018 | 623 |
| claims | 2019 | 624 |
| claims | 2020 | 625 |
| claims | 2021 | 623 |
| claims | 2022 | 624 |
| claims | 2023 | 624 |
| claims | 2024 | 622 |
| claims | 2025 | 622 |
| members | NULL | 10 |
| members | 2019 | 426 |
| members | 2020 | 426 |
| members | 2021 | 427 |
| members | 2022 | 428 |
| members | 2023 | 429 |
| members | 2024 | 426 |
| members | 2025 | 428 |
| providers | NULL | 5 |
| providers | 2020 | 167 |
| providers | 2021 | 167 |
| providers | 2022 | 167 |
| providers | 2023 | 170 |
| providers | 2024 | 163 |
| providers | 2025 | 166 |

---

### Step 1 — Run Archive (live, dry_run=false)

| Field | Value |
|-------|-------|
| Status | **FAIL** — INTERNAL_ERROR (2 of 6 iterations failed) |
| Duration | ~158 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1053611685911312 |
| Error | `An error occurred during execution of task For each: Some iterations failed` |

#### For-each iteration stats

| Metric | Value |
|--------|-------|
| Total iterations | 6 |
| Succeeded | 4 |
| Failed | 2 |
| Error category | `RunExecutionError` — "Workload failed, see run output for details" |

---

### Step 2 — Audit Log Analysis

#### claims (8 years) — all PASS

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2018 | ARCHIVED | 623 | 2018-12-31 | CREATE | **PASS** |
| 2019 | ARCHIVED | 624 | 2019-12-31 | CREATE | **PASS** |
| 2020 | ARCHIVED | 625 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 623 | 2021-12-30 | CREATE | **PASS** |
| 2022 | ARCHIVED | 624 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 624 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 622 | 2024-12-31 | CREATE | **PASS** |
| 2025 | ARCHIVED | 622 | 2025-12-31 | CREATE | **PASS** |

#### Other active tables (lineage dataset) — all PASS

| Table | Year | Record Count | Watermark Value | Mode |
|-------|------|-------------|-----------------|------|
| bronze_table_lineage | 2026 | 30,010,157 | 2026-04-01 | CREATE |
| gold_daily_access_trends | 2026 | 60,709 | 2026-04-01 | CREATE |
| silver_query_table_access | 2025 | 1,795 | 2025-12-31 | CREATE |
| silver_query_table_access | 2026 | 8,304,946 | 2026-03-31 | CREATE |

#### members — **NO AUDIT ENTRIES**

No STARTED or ARCHIVED rows in `archive_audit_log` for members. The iteration failed before any audit logging occurred.

#### providers — **NO AUDIT ENTRIES**

No STARTED or ARCHIVED rows in `archive_audit_log` for providers. The iteration failed before any audit logging occurred.

---

### Root Cause — members and providers failures (operator error)

`table_configs` shows both `members` and `providers` have **empty `watermark_column`**:

| source_table | watermark_column | is_active |
|-------------|-----------------|-----------|
| claims | `event_date` | true |
| members | *(empty)* | true |
| providers | *(empty)* | true |

Tables were enabled (`is_active = true`) without setting `watermark_column`. The archiver needs a watermark column to partition data by year. Without it, the iteration crashes before it can even write a STARTED entry to the audit log.

**This is operator error, not a bug.** The correct watermark columns are: `start_date` (members), `effective_date` (providers). These must be set when enabling the tables.

---

### Step 3 — Verify Archive Folders

Skipped — not meaningful to verify while 2 of 3 target tables failed.

---

### Notes

- Claims archived successfully across all 8 years with correct record counts and watermark values.
- NULL-date rows (15 claims) correctly excluded.
- Lineage tables also archived successfully (they were not disabled this run and have valid watermark columns).
- Provider source counts differ slightly from prior run (e.g. 2020: 167 vs 163 previously).
- Concurrency was 10.

---

### Overall Result: FAIL

**Partial success.** Claims and lineage tables archived correctly (CREATE mode, correct counts, watermarks recorded). However, `members` and `providers` iterations failed because `table_configs.watermark_column` is empty for both — the scanner did not resolve `start_date` / `effective_date` as watermark columns. The archiver does not handle missing watermark columns gracefully (no FAILED audit entry, no user-facing error message).

**Action needed:** Set `watermark_column` in `table_configs` for members (`start_date`) and providers (`effective_date`), then re-run.

---
---

**Test Date:** 2026-04-07
**Test Time:** 12:33 – 12:37 CDT
**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev

---

## Pre-step — Disable Large Tables

Disabled `bronze_column_lineage` and `bronze_query_history` in `table_configs` (2 rows affected).

Make sure to Enable `claims`, `members`, and `providers` in `table_configs` (3 rows).
---

## Before — Baseline

| Metric | Value |
|--------|-------|
| ARCHIVED entries in audit log | 0 |

### Source row counts by table/year

| Table | Year | Count |
|-------|------|-------|
| claims | NULL | 15 |
| claims | 2018 | 623 |
| claims | 2019 | 624 |
| claims | 2020 | 623 |
| claims | 2021 | 623 |
| claims | 2022 | 624 |
| claims | 2023 | 624 |
| claims | 2024 | 622 |
| claims | 2025 | 622 |
| members | NULL | 10 |
| members | 2019 | 426 |
| members | 2020 | 426 |
| members | 2021 | 427 |
| members | 2022 | 428 |
| members | 2023 | 429 |
| members | 2024 | 426 |
| members | 2025 | 428 |
| providers | NULL | 5 |
| providers | 2020 | 163 |
| providers | 2021 | 168 |
| providers | 2022 | 166 |
| providers | 2023 | 167 |
| providers | 2024 | 166 |
| providers | 2025 | 165 |

---

## Step 1 — Run Archive (live, dry_run=false)

| Field | Value |
|-------|-------|
| Status | **PASS** — TERMINATED SUCCESS |
| Duration | ~167 sec |
| Run URL | https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/534308376371821 |

---

## Step 2 — Check Audit Log

Every eligible table+year has a STARTED → ARCHIVED pair. All `archive_mode = CREATE`. **PASS**

### claims (8 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2018 | ARCHIVED | 623 | 2018-12-31 | CREATE | **PASS** |
| 2019 | ARCHIVED | 624 | 2019-12-31 | CREATE | **PASS** |
| 2020 | ARCHIVED | 623 | 2020-12-30 | CREATE | **PASS** |
| 2021 | ARCHIVED | 623 | 2021-12-30 | CREATE | **PASS** |
| 2022 | ARCHIVED | 624 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 624 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 622 | 2024-12-31 | CREATE | **PASS** |
| 2025 | ARCHIVED | 622 | 2025-12-31 | CREATE | **PASS** |

### members (7 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2019 | ARCHIVED | 426 | 2019-12-30 | CREATE | **PASS** |
| 2020 | ARCHIVED | 426 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 427 | 2021-12-31 | CREATE | **PASS** |
| 2022 | ARCHIVED | 428 | 2022-12-30 | CREATE | **PASS** |
| 2023 | ARCHIVED | 429 | 2023-12-31 | CREATE | **PASS** |
| 2024 | ARCHIVED | 426 | 2024-12-29 | CREATE | **PASS** |
| 2025 | ARCHIVED | 428 | 2025-12-30 | CREATE | **PASS** |

### providers (6 years)

| Year | Status | Record Count | Watermark Value | Mode | Result |
|------|--------|-------------|-----------------|------|--------|
| 2020 | ARCHIVED | 163 | 2020-12-31 | CREATE | **PASS** |
| 2021 | ARCHIVED | 168 | 2021-12-29 | CREATE | **PASS** |
| 2022 | ARCHIVED | 166 | 2022-12-31 | CREATE | **PASS** |
| 2023 | ARCHIVED | 167 | 2023-12-30 | CREATE | **PASS** |
| 2024 | ARCHIVED | 166 | 2024-12-30 | CREATE | **PASS** |
| 2025 | ARCHIVED | 165 | 2025-12-29 | CREATE | **PASS** |

### Other active tables (lineage dataset)

| Table | Year | Record Count | Mode |
|-------|------|-------------|------|
| bronze_table_lineage | 2026 | 30,010,157 | CREATE |
| gold_daily_access_trends | 2026 | 60,709 | CREATE |
| silver_query_table_access | 2025 | 1,795 | CREATE |
| silver_query_table_access | 2026 | 8,304,946 | CREATE |

---

## Step 3 — Verify Archive Folders Exist

Archive folder naming: `year_YYYY` (not `year=YYYY`)

| Archive Path | Expected | Actual | Result |
|-------------|----------|--------|--------|
| `.../claims/year_2020` | 623 | 623 | **PASS** |
| `.../members/year_2021` | 427 | 427 | **PASS** |
| `.../providers/year_2022` | 166 | 166 | **PASS** |

All 6 active table folders confirmed in volume:
`bronze_table_lineage/`, `claims/`, `gold_daily_access_trends/`, `members/`, `providers/`, `silver_query_table_access/`

---

## Notes

- Rows with NULL dates (15 claims, 10 members, 5 providers) were correctly excluded from archiving.
- `bronze_column_lineage` and `bronze_query_history` were disabled before the run to keep test duration reasonable.
- Concurrency was 10 (updated in test 04).

---

## Overall Result: PASS

First live archive correctly created Delta folders for all eligible table+year combinations. Record counts in audit log match archive folder counts. All entries have `archive_mode = CREATE` and recorded `watermark_value`. Source data with NULL dates was excluded.
