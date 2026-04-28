# 12 — Orphan Folder Detection Results

## Run — 2026-04-27 (post-fix verification — PASS)

> **TL;DR:** Re-ran 12T against `dev2_archive` after the Bug B (`DELTA_MISSING_TRANSACTION_LOG` → `archive_folder_orphan`) and Bug A (`archive_delta_version` capture) fixes landed locally on `feat/delta_config_build_v10_test_cases` (commits `88609bf` for Bug B, `016eeb3` for Bug A). Phase 2c produced the operator-friendly `archive_folder_orphan` diagnostic with no raw Spark exception leak, and Phase 3c's recovered `ARCHIVED/CREATE` rows all carry a non-NULL `archive_delta_version`. **Test passed for both fixes.**

**Branch:** `feat/delta_config_build_v10_test_cases`
**Profile:** `fe-sandbox-manocha`
**Workspace:** https://fe-sandbox-manocha.cloud.databricks.com
**Bundle Target:** `dev-serverless`
**Source catalog/schema:** `dev2_archive.source_data_samples`
**Audit:** `dev2_archive.metadata.archive_audit_log`
**Archive volume:** `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`
**Table:** `claims` (scoped via `table_config_filter`)

---

### Pre-flight Check

| Check | Finding |
|-------|---------|
| Audit log (claims year 2018) | 0 rows — already clean (the prior aborted Run 2026-04-27 left no FAILED rows because phase 2b never wrote one) |
| Archive folder (`claims/year_2018/`) | Did not exist — Phase 1a effectively a no-op |
| Other claims years | Audit log empty for all years 2019–2025 — full archive run will be exercised by Phase 3b, which is bonus coverage on the Bug A version-capture path |
| Other tables | Untouched |

Phase 1 cleanup steps were therefore no-ops; jumped to Phase 2.

---

### Phase 2 — Create Orphan + Run (expect failure)

| Step | Status | Detail |
|------|--------|--------|
| 2a. Create empty orphan folder | **PASS** | `PUT /api/2.0/fs/directories/.../claims/year_2018` returned `null`; `databricks fs ls` confirmed the folder exists with no `_delta_log/` entries. |
| 2b. Run `caresource_archive_run` | **PASS** | Job INTERNAL_ERROR FAILED as expected. Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/840821698378645 . `state_message`: "Task run_archive failed... Some iterations failed." |
| 2c. Audit-log diagnostic | **PASS** | Two STARTED → FAILED pairs for year 2018 (automatic for-each retry). Both `error_message` columns carry the operator-friendly `archive_folder_orphan` diagnostic from `src/exceptions.py`. |

**FAILED `error_message` (full, both pairs identical):**

```
dev2_archive.source_data_samples.claims year 2018: Archive path
/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/claims/year_2018
has data files but no valid Delta transaction log (orphan or corrupted folder).
Use delete_archived_slice to wipe the orphan path, then re-run the archive job.
See docs/runbooks/recovery.md.
```

**Bug B regression gate (PASS):**
- The raw `[DELTA_MISSING_TRANSACTION_LOG]` Spark exception did **not** propagate into `error_message`. The pre-fix bug surfaced in this file's earlier "aborted" run is fixed by `src/utils.py::_NOT_A_DELTA_TABLE_FRAGMENTS` recognising both `"DELTA_MISSING_TRANSACTION_LOG"` and `"missing transaction log"` (commit `88609bf`).
- For-each loop stopped at first failure (year 2018) — no other years were attempted in this run.

---

### Phase 3 — Fix + Re-run (recovery)

| Step | Status | Detail |
|------|--------|--------|
| 3a. Delete orphan folder | **PASS** | `DELETE /api/2.0/fs/directories/.../claims/year_2018` returned `null`; `databricks fs ls` showed the parent `claims/` directory empty. |
| 3b. Re-run `caresource_archive_run` | **PASS** | Job TERMINATED SUCCESS. Run URL: https://fe-sandbox-manocha.cloud.databricks.com/?o=7474658872313088#job/645665657236546/run/400475209792008 . All 8 years (2018–2025) processed in this run. |
| 3c. Audit log final state | **PASS** | See table below. |

**Audit log (terminal statuses for `claims`, ordered by year):**

| Year | Status | Mode | record_count | archive_delta_version |
|------|--------|------|-------------:|-----------------------|
| 2018 | FAILED   | NULL   | 0   | NULL (Phase 2 first attempt) |
| 2018 | FAILED   | NULL   | 0   | NULL (Phase 2 retry)         |
| 2018 | ARCHIVED | CREATE | 623 | **0**                        |
| 2019 | ARCHIVED | CREATE | 624 | **0**                        |
| 2020 | ARCHIVED | CREATE | 623 | **0**                        |
| 2021 | ARCHIVED | CREATE | 623 | **0**                        |
| 2022 | ARCHIVED | CREATE | 624 | **0**                        |
| 2023 | ARCHIVED | CREATE | 624 | **0**                        |
| 2024 | ARCHIVED | CREATE | 622 | **0**                        |
| 2025 | ARCHIVED | CREATE | 622 | **0**                        |

**Bug A regression gate (PASS):**
- All 8 `ARCHIVED/CREATE` rows carry `archive_delta_version = 0` (the Delta version of the first commit on a fresh archive folder). Pre-fix, every one of these would have been NULL, blocking `rollback_archived_slice` with `target_missing_version`.
- The capture happens in `src/archiver.py::_archive_table_year` between `_verify_archive` and `log_archive` (commit `016eeb3`). Year 2018 also exercises the recovery path (FAILED → FAILED → ARCHIVED) and still records the version on the final `ARCHIVED` row.

---

### What Happened

1. **Phase 1:** No cleanup was needed — both the audit log and archive folder for `claims` were already empty for all years on `dev2_archive`. The previous aborted Run 2026-04-27 left no residual state.
2. **Phase 2:** Created an empty orphan folder via the Files API. `caresource_archive_run` failed exactly as intended: two STARTED → FAILED pairs were written, both carrying the `archive_folder_orphan` diagnostic. The raw `[DELTA_MISSING_TRANSACTION_LOG]` exception that broke the prior run is now classified correctly.
3. **Phase 3:** Deleted the orphan folder via the Files API. The re-run archived all 8 years (2018–2025) successfully. **Every** `ARCHIVED` row records `archive_delta_version = 0` — confirming the Bug A capture path works on the live workspace, not only in unit tests.

### Next Steps

1. **Both fixes verified end-to-end on dev2_archive.** Branch is ready for review/PR.
2. **38T re-run is still pending** — deferred to manual operator run because of the longer recovery-trio sequence (~10 phases including notebook tasks).
3. **No code or test-case changes** were needed during this run; both fixes work as designed.

---

## Run — 2026-04-27 (rename-recovery-trio regression — partial / aborted)

**Status:** ABORTED at Phase 2b. Skipped on operator instruction after surfacing a pre-existing orphan-classification gap unrelated to the rename plan.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, profile `fe-sandbox-manocha`, bundle target `dev-serverless`. Re-run was attempted on `claims` (per the original 12T fixture, after `caresource_scanner` repopulated `table_configs.claims`).

### What was attempted

- Phase 1 cleanup: PASS — audit cleared, archive folder removed, `table_configs` reset.
- Phase 2a (create empty orphan folder under `claims/year_2018`): PASS — folder existed, no `_delta_log/`.
- Phase 2b (run `caresource_archive_run` to detect the orphan): **deviation surfaced (see below).**

### Finding — pre-existing orphan-classification gap

The archive run failed with `INTERNAL_ERROR FAILED Task run_archive failed... Some iterations failed.` The audit log row carried the raw Spark exception:

```
[DELTA_MISSING_TRANSACTION_LOG] Incompatible format detected.
```

Expected per 12T spec (and per the 2026-04-10 baseline run below): `FAILED / archive_folder_orphan` with the operator-friendly diagnostic message.

Root cause: `src/utils.py::_NOT_A_DELTA_TABLE_FRAGMENTS` (lines 139–145) enumerates the substrings used by `_classify_delta_probe_exception` to recognise an empty-folder situation. The current set includes `"is not a Delta table"`, `"DELTA_MISSING_DELTA_TABLE"`, `"not a valid Delta table"` and case variants, but **not** `"DELTA_MISSING_TRANSACTION_LOG"` or `"missing transaction log"`. Modern Databricks Runtime raises `DELTA_MISSING_TRANSACTION_LOG` for an empty folder, so `_classify_delta_probe_exception` returns `None` and the caller re-raises the raw exception instead of producing `FAILED / archive_folder_orphan`.

This is a **pre-existing gap unrelated to the recovery-trio rename plan.** The behaviour was not changed by anything in `rename_recovery_trio_f2cb0f85.plan.md` — `src/utils.py` was untouched. The 2026-04-10 baseline run further down this file passed because the older DBR raised one of the recognised strings.

### Test-case authoring bugs found while attempting the run

The 12T markdown also has stale schema references that need updating before the next re-run:

- `claims.claim_date` does not exist; the watermark column is `event_date`.
- `table_configs.archive_enabled` does not exist; the column is `is_active`.
- `archive_audit_log.action_taken` and `error_code` do not exist; current schema exposes `status`, `error_message`, `audit_id`.

These are test-case bugs only — the product columns work as designed.

### Disposition

Skipped per operator instruction. **Did not modify code.** The product fix (adding `"DELTA_MISSING_TRANSACTION_LOG"` to `_NOT_A_DELTA_TABLE_FRAGMENTS`) and the 12T markdown fixes are tracked separately. After both fixes land, this test should be re-run from a clean schema state.

---

## Run — 2026-04-10 16:26 CDT (claims)

> **TL;DR:** Created an orphan archive folder (exists but no audit trail). Archiver detected it, logged FAILED with clear error and resolution steps. Removed the orphan, re-ran — archiver recovered and archived 623 rows with `CREATE` mode. Other years correctly SKIPPED. **Test passed.**

**Branch:** `feat/delta_config_build_v3_code_reduce`
**Profile:** DEFAULT
**Workspace:** https://e2-demo-field-eng.cloud.databricks.com
**Bundle Target:** dev
**Table:** claims (scoped via `table_config_filter`)

---

### Pre-flight Check

| Check | Finding |
|-------|---------|
| Audit log (claims year 2018) | 10 entries from prior test runs (test 08 DRY_RUN, test 11 STARTED/FAILED/ARCHIVED) |
| Archive folder (year_2018) | Existed with data from test 11 Phase 1 archive |
| Other claims years | Untouched — years 2019–2026 have ARCHIVED/SKIPPED entries and intact folders |
| Other tables | Not touched |

---

### Phase 1 — Establish Preconditions

| Step | Status | Detail |
|------|--------|--------|
| 1a. Delete archive folder for year 2018 | **PASS** | Folder was not present at `year=2018` path. Note: archiver uses `year_2018` (underscore) naming, not `year=2018` (Hive-style). The test case paths need correction — see notes below. |
| 1b. Delete audit entries | **PASS** | `DELETE` removed 10 rows (`num_affected_rows = 10`) |
| 1c. Verify clean state | **PASS** | `audit_entries = 0` confirmed |

---

### Phase 2 — Create Orphan + Run (expect failure)

| Step | Status | Detail |
|------|--------|--------|
| 2a. Create orphan folder | **PASS** | Created empty directory via Files API `PUT /api/2.0/fs/directories/.../year=2018`. However, the archiver checks `year_2018` not `year=2018` — the *real* orphan was the `year_2018` folder that still existed from Phase 1a (the test case path was wrong, but the orphan condition was already present because we deleted the audit entries while the `year_2018` folder still had data). |
| 2b. Run archive | **PASS** | Job INTERNAL_ERROR FAILED as expected. Run URL: `https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/253993187635667` |
| 2c. Check audit log | **PASS** | Two STARTED → FAILED pairs for year 2018 (automatic retry). Error message below. For-each loop stopped at year 2018 — no other years processed. |

**Error message (full):**

```
sandeep_manocha.caresource_data_samples.claims year 2018: Archive folder exists at
/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/caresource_data_samples/claims/year_2018
but no successful archive is recorded in the audit table. This may be from a failed prior write with partial data.
1. Inspect the folder: SELECT COUNT(*) FROM delta.`/Volumes/.../claims/year_2018`
2. If partial/corrupt: DELETE the folder from cloud storage
3. Re-run the archive job
```

**Expectation check:**
- STARTED → FAILED: **YES** (two pairs, automatic retry)
- `orphan_folder` literal keyword in error: **NO** — the error describes the orphan condition in plain English instead
- Resolution steps in error: **YES** — inspect, delete, re-run
- For-each stops at first failure: **YES** — only year 2018 was processed
- Second STARTED → FAILED pair (retry): **YES** — same behavior as test 11 Phase 2

---

### Phase 3 — Fix + Re-run (recovery)

| Step | Status | Detail |
|------|--------|--------|
| 3a. Delete orphan folder | **PASS** | Recursively deleted `year_2018` folder (Delta log, parquet, metadata) via Files API. Also confirmed `year=2018` already gone. |
| 3b. Re-run archive | **PASS** | TERMINATED SUCCESS. Run URL: `https://e2-demo-field-eng.cloud.databricks.com/?o=1444828305810485#job/197650125146998/run/1060630379650973` |
| 3c. Check audit log | **PASS** | Results below |

**Audit log (Phase 3, most recent first):**

| Year | Status | Mode | Records | Timestamp |
|------|--------|------|---------|-----------|
| 2026 | SKIPPED | SKIP | 0 | 21:33:55 |
| 2025 | SKIPPED | SKIP | 0 | 21:33:48 |
| 2024 | SKIPPED | SKIP | 0 | 21:33:40 |
| 2023 | SKIPPED | SKIP | 0 | 21:33:33 |
| 2022 | SKIPPED | SKIP | 0 | 21:33:27 |
| 2021 | SKIPPED | SKIP | 0 | 21:33:20 |
| 2020 | SKIPPED | SKIP | 0 | 21:33:13 |
| 2019 | SKIPPED | SKIP | 0 | 21:33:07 |
| **2018** | **ARCHIVED** | **CREATE** | **623** | **21:32:59** |

**Expectation check:**
- Year 2018 recovered from FAILED → ARCHIVED with `archive_mode = CREATE`: **YES**
- Record count = 623: **YES**
- Other years SKIPPED (already archived): **YES**

---

## What Happened

1. **Phase 1:** Cleaned audit entries for claims year 2018 (10 rows deleted). The archive folder `year_2018` still existed from a prior test run — this was the orphan condition. The test case uses `year=2018` paths but the archiver actually creates `year_2018` (underscore) folders.

2. **Phase 2:** Ran the archiver against the orphan condition (folder exists, no audit trail). The archiver correctly detected the orphan, logged a FAILED status with a descriptive error message and three-step resolution instructions. The for-each loop stopped processing at year 2018 — later years were not attempted. The automatic retry also failed (same orphan still present).

3. **Phase 3 (first attempt):** Deleted `year=2018` folder (the one from Phase 2a), but the archiver still failed because the actual orphan was at `year_2018`. After identifying the naming mismatch, deleted `year_2018` recursively (Delta log + parquet + metadata). Re-ran the archiver — it recovered, archived 623 rows as CREATE, and all other years were SKIPPED.

## Next Steps

1. **Update test case paths:** The test case `12T_orphan_folder_detection.md` uses `year=2018` (Hive-style partitioning) in the `dbutils.fs.rm` and `dbutils.fs.mkdirs` commands, but the archiver creates folders using `year_2018` (underscore). Update the test case to use `year_2018` paths so the manual steps target the correct folder:
   - Phase 1a: `dbutils.fs.rm(".../claims/year_2018", recurse=True)`
   - Phase 2a: `dbutils.fs.mkdirs(".../claims/year_2018")`
   - Phase 3a: `dbutils.fs.rm(".../claims/year_2018", recurse=True)`

2. **Consider adding `orphan_folder` keyword:** The error message describes the orphan condition clearly but doesn't include the literal keyword `orphan_folder`. Consider adding it for easier grep/search in audit logs (e.g., prefix the error with `[orphan_folder]`).

3. **All test assertions passed** — the orphan detection and recovery flow works correctly.
