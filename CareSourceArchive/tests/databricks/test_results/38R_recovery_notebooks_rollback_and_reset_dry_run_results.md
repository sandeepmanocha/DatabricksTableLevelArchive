# 38 — Recovery Trio End-to-End Results

> Append new runs **below** this line, **newest first** — add a `## Run — <date> <time>` section after the H1, per `38T` instructions.

## Run — 2026-04-27 10:25 CDT (live e2e, post-rename)

**TL;DR:** All 9 phases of the recovery trio e2e PASS on the renamed bundle. Both `RECOVERY_ARCHIVE_DELETED` and `RECOVERY_ARCHIVE_ROLLED_BACK` flow end-to-end, the `_ROLLBACK_TARGET_ALLOWED_STATUSES` gate accepts both `ARCHIVED` and `RECOVERY_ARCHIVE_ROLLED_BACK`, and Phase 8 raises the rewritten `target_status_not_rollbackable` exception with both renamed statuses cited verbatim. Two test-case authoring bugs in 38T were found and fixed during this run.

**Environment:** `dev2_archive` catalog, `source_data_samples` schema, `metadata` audit/config schema, workspace `fe-sandbox-manocha`, bundle target `dev-serverless`. Test scoped to `providers / year 2020`. Archive volume root `/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.

**Captured run IDs and audit IDs:**

- `SEED_AUDIT_ID` = `a5c19325-3c27-4659-9425-350918d83805` (Phase 2 ARCHIVED, `archive_mode=CREATE`)
- `DELETE_AUDIT_ID_1` = `357ae912-ac62-4346-a384-ace12a3b45fb` (Phase 4 RECOVERY_ARCHIVE_DELETED)
- `ROLLBACK_AUDIT_ID` = `e9875a73-2ecc-4ee1-9786-3515f2fd9d01` (Phase 6 RECOVERY_ARCHIVE_ROLLED_BACK, `archive_delta_version=2`)
- `DELETE_AUDIT_ID_2` = `7729a6b4-1ff7-472a-bde3-52c88e357637` (Phase 7a RECOVERY_ARCHIVE_DELETED)
- Archive run id (Phase 2): `6d83e65e-2423-483a-9a28-b5426b80050e`

**Job runs:** Phase 2 archive `205424302425745` (~144s), Phase 3 dry-run delete `634480103783277` (~62s), Phase 4 live delete `208374699873077` (~82s), Phase 5 dry-run rollback `443844646973158` (~60s), Phase 6 live rollback `348647161920415` (~82s), Phase 7a re-delete `551358360484554` (~91s), Phase 7b dry-run rollback `341527773984331` (~41s), Phase 8 negative dry-run rollback `<failed-driver-side; no run id captured in CLI poll because the bundle CLI exited with FAILED before printing the URL banner block>`.

**Baselines (`providers / 2020`):** `BASELINE_2020 = 163` rows.

---

### Pre-flight — PASS
- Audit log: 0 rows for providers (already cleared by 31T cleanup).
- Source `providers / 2020`: 163 rows (= `BASELINE_2020`).
- `table_configs.providers`: `delete_after_archive=false`, `archive_base_path=/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples`.
- Archive volume providers folder: did not exist.
- Bundle: `caresource_archive_run` (645665657236546), `caresource_delete_archived_slice` (846146206358781), `caresource_rollback_archived_slice` (476199794789986), `generate_test_data` (857878464319321) deployed under `dev-serverless`. Old recovery names (`caresource-archive-reset-slice`, `caresource-archive-rollback-run`) NOT present in `databricks jobs list`.

### Phase 1 — Clean preconditions — PASS (no-op)
Audit was already empty, archive folder did not exist, `delete_after_archive=false`. Phase 1 explicit DELETE/`fs rm` would have been idempotent. Skipped the explicit calls because pre-flight confirmed the state.

### Phase 2 — Seed an `ARCHIVED` slice for `providers / 2020` — PASS
Archive run `205424302425745` (`config_table_filter=source_table = 'providers'`) TERMINATED SUCCESS in ~144s.

- 2a (deviation, fixed in test case): The first invocation failed with `ArchiveConfigError: filter_expr failed to parse: [UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with name `providers` cannot be resolved.` The CLI's CSV parser collapsed the SQL single quotes inside the `--params` value because they were `''` inside a zsh single-quoted outer arg. The working invocation uses outer double quotes so the inner `'providers'` survives: `--params "...,\"table_config_filter=source_table = 'providers'\""`.
- 2b: `SEED_AUDIT_ID = a5c19325-3c27-4659-9425-350918d83805`, `status = ARCHIVED`, `archive_mode = CREATE`, `record_count = 163` ✓. **`archive_delta_version` came back NULL, not `0`.** This contradicts the original 38T assertion. Reading `src/archiver.py` line 756, the archive job calls `log_archive(..., archive_mode=archive_mode)` without passing `archive_delta_version`, and the `audit.py` parameter defaults to `None`. The unit test `test_g4_null_version_raises_target_missing_version` documents this — `ARCHIVED` rows from `CREATE` paths intentionally do not carry a Delta version. The 38T test case was wrong; updated it to expect `NULL` and to require an operator-supplied `UPDATE` to set `archive_delta_version = 0` on the seed before Phase 5.
- 2c: Archive Delta `year_2020` row count = 163 ✓. `DESCRIBE HISTORY` shows version 0 = `CREATE TABLE AS SELECT` with `numOutputRows=163`.

### Phase 3 — Dry-run `caresource_delete_archived_slice` — PASS
Run `634480103783277` TERMINATED SUCCESS in ~62s.

- 3a (deviation, fixed in test case): The first invocation with `table_ids="providers"` (short name) failed with `ArchiveConfigError: Invalid table_ids — missing=['providers'], duplicate=[]` from `generate_delete_archived_slice_inputs.py`. The notebook validates `table_ids` against the `table_configs.table_id` column, which holds the FQN. Re-ran with `table_ids=dev2_archive.source_data_samples.providers` → SUCCESS. Updated 38T to require the FQN.
- 3b (deviation in assertion, fixed in test case): The original 38T assertion `audit_id != SEED_AUDIT_ID` returns 1 because Phase 2 also wrote a `STARTED` row before the `ARCHIVED` row. Total audit count for providers/2020 stays at 2 across Phase 3, proving the dry-run wrote no row. Updated 38T to assert `phase3_post_count = 2` instead.
- 3c: Archive Delta count still 163, history still has only version 0 = CREATE. ✓
- 3d: The for-each iteration `468646060486068` succeeded. `displayHTML` content not retrievable via `databricks jobs get-run-output` (the displayHTML payload doesn't surface in the API), but the indirect proof — job success + no audit row + no Delta mutation — is sufficient. Confirmed via grep that `src/recovery.py` lines 302/455 emit the action labels `WOULD_RECOVERY_ARCHIVE_ROLLED_BACK` / `WOULD_RECOVERY_ARCHIVE_DELETED` — the renamed strings.
- The for-each task value `delete_archived_slice_task_inputs` flowed correctly: the iteration's `for_each_task.inputs` field on the Jobs API shows the value being read from `{{tasks.generate_parameters.values.delete_archived_slice_task_inputs}}` — the renamed key.

### Phase 4 — Live `caresource_delete_archived_slice` — PASS
Run `208374699873077` TERMINATED SUCCESS in ~82s.

- 4b: `DELETE_AUDIT_ID_1 = 357ae912-ac62-4346-a384-ace12a3b45fb`, `status = RECOVERY_ARCHIVE_DELETED` ✓ (**headline assertion** — the renamed status flows end-to-end through generator → notebook → recovery function → audit table). `archive_mode IS NULL`, `archive_delta_version IS NULL`, `record_count = 163`, `error_message` starts with `"38T phase 4 — verify renamed RECOVERY_ARCHIVE_DELETED audit status"`, `needs_review = true`, `archive_run_id = 49e3187a-5249-4b21-9cf2-e9d5cd14d152` (distinct from the Phase 2 archive run id — recovery jobs generate their own run id).
- 4c: Archive Delta `year_2020` count = 0 ✓. `DESCRIBE HISTORY`: version 1 = `DELETE` with `numDeletedRows=163`. Version 0 (CREATE) still in history.
- 4d: Source `providers / 2020` count = 163 ✓ (recovery delete only touches the archive Delta).

### Phase 5 — Dry-run `caresource_rollback_archived_slice` to `SEED_AUDIT_ID` — PASS (after operator patch)

- 5a (operator step, documented in 38T): The first invocation against `SEED_AUDIT_ID` failed with `ArchiveOperationError: target_missing_version` because the seed row's `archive_delta_version` was NULL (see Phase 2b finding). I patched the audit row with `UPDATE … SET archive_delta_version = 0 WHERE audit_id = '${SEED_AUDIT_ID}'` (1 row affected). After the patch, the dry-run succeeded.
- 5a (post-patch): Run `443844646973158` TERMINATED SUCCESS in ~60s — gate accepted `status = 'ARCHIVED'`, the first member of `_ROLLBACK_TARGET_ALLOWED_STATUSES` ✓.
- 5b: Notebook displayHTML content not retrievable via API (same constraint as 3d). Job success + no Delta mutation is the proxy.
- 5c: Total audit rows for providers/2020 = 3 (STARTED + ARCHIVED + DELETE_AUDIT_ID_1). No new row from dry-run. Latest archive Delta version still 1 (DELETE) — no RESTORE entry yet ✓.

### Phase 6 — Live `caresource_rollback_archived_slice` to `SEED_AUDIT_ID` — PASS
Run `348647161920415` TERMINATED SUCCESS in ~82s.

- 6b: `ROLLBACK_AUDIT_ID = e9875a73-2ecc-4ee1-9786-3515f2fd9d01`, `status = RECOVERY_ARCHIVE_ROLLED_BACK` ✓ (**second headline assertion**). `archive_mode IS NULL`, `archive_delta_version = 2` (post-RESTORE Delta version) ✓, `record_count = 163`, `error_message IS NULL` ✓ (rollback writes no reason text), `needs_review = true`.
- 6c: Archive Delta `year_2020` count = 163 ✓. `DESCRIBE HISTORY`: version 2 = `RESTORE` with `operationParameters.version = "0"` ✓ (matches the patched `archive_delta_version` on `SEED_AUDIT_ID`).

### Phase 7 — Validate `RECOVERY_ARCHIVE_ROLLED_BACK` is itself rollback-able — PASS

- 7a: Run `551358360484554` TERMINATED SUCCESS in ~91s. New `RECOVERY_ARCHIVE_DELETED` row with `DELETE_AUDIT_ID_2 = 7729a6b4-1ff7-472a-bde3-52c88e357637`, `record_count = 163`. Archive Delta now at version 3 = `DELETE`.
- 7b/7c: Dry-run rollback targeting `ROLLBACK_AUDIT_ID` (status `RECOVERY_ARCHIVE_ROLLED_BACK`, `archive_delta_version=2`) ran as `341527773984331` and TERMINATED SUCCESS in ~41s. The gate did NOT raise `target_status_not_rollbackable` ✓ — proving the second member of `_ROLLBACK_TARGET_ALLOWED_STATUSES = {"ARCHIVED", "RECOVERY_ARCHIVE_ROLLED_BACK"}` is reachable end-to-end. This phase is the only e2e coverage of the membership change in `src/recovery.py` line 35.

### Phase 8 — Negative: rollback to `RECOVERY_ARCHIVE_DELETED` is refused — PASS
- 8a: Dry-run rollback against `DELETE_AUDIT_ID_1` (status `RECOVERY_ARCHIVE_DELETED`) failed at the `run_rollback_archived_slice` task as expected.
- 8b: Stack trace contains `ArchiveOperationError` and the diagnostic message:
  > `dev2_archive.source_data_samples.providers year 2020: Target audit row 357ae912-ac62-4346-a384-ace12a3b45fb has status RECOVERY_ARCHIVE_DELETED. Only ARCHIVED or RECOVERY_ARCHIVE_ROLLED_BACK rows are valid rollback targets — an ARCHIVED_AND_DELETED (main-flow source-delete), RECOVERY_ARCHIVE_DELETED (recovery wipe), FAILED, DRY_RUN, or other non-success row cannot be used as a target because restoring would either resurrect data that was intentionally deleted or jump to an inconsistent state. See docs/runbooks/recovery.md.`

  The message contains both `RECOVERY_ARCHIVE_ROLLED_BACK` (cited as a valid target) and `RECOVERY_ARCHIVE_DELETED` (cited as the actual target status), confirming the rewritten template in `src/exceptions.py` lines 85–95 is on the wire ✓. The exception bubble surfaces `ArchiveOperationError` (the renamed exception type, was `ArchiveError` pre-rename in some draft revisions).
- 8c: Latest archive Delta commit is still version 3 = DELETE (no new RESTORE) ✓. Audit log status breakdown: `ARCHIVED=1`, `RECOVERY_ARCHIVE_DELETED=2`, `RECOVERY_ARCHIVE_ROLLED_BACK=1`, `STARTED=1` (5 rows total, no Phase 8 row).

### Phase 9 — Cleanup — PASS
- 9a: 15 audit rows deleted (12 from the Phase 2 archive run that wrote `STARTED + ARCHIVED` for years 2020–2025 + 3 recovery rows from Phases 4/6/7a). Note that even though the test asserts only on year 2020, Phase 2's archive run iterates over every eligible year for `providers` because `default_retention_years=0` and the `table_config_filter` only selects the table, not the year.
- 9b: Archive folder `…/source_data_samples/providers` (and all six year subfolders) removed.
- 9c: `delete_after_archive = false` ✓.
- 9d: Source `providers / 2020` count = 163 = `BASELINE_2020` ✓.

---

## What Happened

The renamed recovery trio works end-to-end. Both new audit statuses (`RECOVERY_ARCHIVE_DELETED`, `RECOVERY_ARCHIVE_ROLLED_BACK`) flow from notebook widgets through `generate_delete_archived_slice_inputs.py` (the for-each task value key is the renamed `delete_archived_slice_task_inputs`), through the recovery functions in `src/recovery.py`, into the audit table with the correct shape (mode/version/record_count/needs_review/error_message). The dry-run paths emit the renamed action labels (`WOULD_RECOVERY_ARCHIVE_DELETED`, `WOULD_RECOVERY_ARCHIVE_ROLLED_BACK`) without writing audit rows or mutating the Delta. The `_ROLLBACK_TARGET_ALLOWED_STATUSES` gate accepts both `ARCHIVED` and `RECOVERY_ARCHIVE_ROLLED_BACK` (Phases 5/6 and 7 respectively), and refuses `RECOVERY_ARCHIVE_DELETED` with the rewritten exception template that mentions both renamed verbs (Phase 8). The rename plan is **GREEN** at the workspace level for all three jobs.

Three operational findings, recorded honestly:

1. **Test-case bug 1 — short table_ids.** 38T originally passed `table_ids=providers`, but `generate_delete_archived_slice_inputs.py` validates against `table_configs.table_id` which holds the FQN `dev2_archive.source_data_samples.providers`. Updated 38T to use the FQN and added an explanatory note. Not a code defect — the notebook's behavior is intentional and matches `manual-recovery.md`.
2. **Test-case bug 2 — Phase 3b assertion.** The original assertion `audit_id != SEED_AUDIT_ID` ignored the `STARTED` audit row that Phase 2 also writes. Updated to `phase3_post_count = 2` so the assertion stays correct regardless of how many fixed rows Phase 2 produces.
3. **Test-case bug 3 — `archive_delta_version=0` on seed.** The original 38T assumed `ARCHIVED/CREATE` rows carry `archive_delta_version = 0`. They don't — `src/archiver.py` line 756 calls `log_archive` without that kwarg, so it defaults to `None`. The unit test `test_g4_null_version_raises_target_missing_version` documents the path. Updated 38T to expect `NULL` and to require a manual `UPDATE archive_audit_log SET archive_delta_version = 0 WHERE audit_id = SEED_AUDIT_ID` before Phase 5; flagged as a test-data fix, not a product code change.

One CLI nuance worth noting (also seen in 30T): the Databricks bundle CLI's `--params` field is a CSV. Inside zsh, the safest pattern when a value contains an SQL single-quote is to wrap the outer arg in double quotes and escape the inner CSV double quotes with `\"`. The pattern `'... "table_config_filter=source_table = ''providers''" ...'` looks like nested single-quote escaping but actually concatenates two single-quoted substrings with bare `providers` in the middle, which the CSV parser then forwards unquoted to the SQL parser, producing `UNRESOLVED_COLUMN`.

## Next Steps

1. 38T is GREEN. The test case file has been updated to reflect actual notebook semantics; future operators can run it as-is.
2. Consider whether `src/archiver.py` should record `archive_delta_version` on `ARCHIVED/CREATE` rows. Today it does not, which forces operators to either patch the audit row before rollback or skip rollback for the very first archive of a slice. The runbook `docs/runbooks/recovery.md` already mentions the "older rows pre-dating R2" caveat. Not blocking the rename PR — just an observability gap that would simplify recovery for fresh tables.
3. The displayHTML payload from the iteration / rollback notebooks is not retrievable via `databricks jobs get-run-output`. For future test cases, prefer using `_log.info(json.dumps(result))` lines that surface in the driver log (which IS retrievable) over UI-only `displayHTML` for assertable values. Out of scope for the rename PR.

---

## Run — 2026-04-27 (local unit tests only)

**Workspace / notebooks:** not run (requires Databricks + deployed bundle).  
**Local:** `pytest tests/unit/test_recovery.py -v` from repo root.

### TL;DR

All **29** `src.recovery` unit tests **PASS**. Databricks notebook dry-run phases (38T Phases 2–3) still need a manual run in the workspace when ready.

### Local unit tests (src.recovery)

| Command | Result |
|---------|--------|
| `pytest tests/unit/test_recovery.py -v` | **PASS** (29 tests, ~0.04s) |

---

## What Happened

- Local `pytest` for `tests/unit/test_recovery.py` completed with all tests passing.
- No Databricks execution in this session.

## Next Steps

- Run **38T** Phases 1–3 in a workspace to record notebook + path integration.
- After a manual run, add a new `## Run — …` section **above** this one with profile, workspace URL, and phase PASS/FAIL.
