# 38 — Recovery Trio End-to-End (`delete_archived_slice` + `rollback_archived_slice`)

**Goal:** Drive the renamed recovery trio through every status transition in a single test on `providers / year 2020`, asserting that the new audit vocabulary (`RECOVERY_ARCHIVE_DELETED`, `RECOVERY_ARCHIVE_ROLLED_BACK`) flows end-to-end through the YAML jobs, the notebooks, the recovery functions, and the audit table — and that the updated `_ROLLBACK_TARGET_ALLOWED_STATUSES = {"ARCHIVED", "RECOVERY_ARCHIVE_ROLLED_BACK"}` gate behaves correctly in both the accept and refuse directions.

**Covers:**

- Bundle resources `caresource_delete_archived_slice` (with `generate_parameters` + `run_delete_archived_slice` + `run_delete_archived_slice_iteration`) and `caresource_rollback_archived_slice` (with `run_rollback_archived_slice`).
- `src.recovery.delete_archived_slice` dry-run preview action `"WOULD_RECOVERY_ARCHIVE_DELETED"` — no audit row written.
- `src.recovery.delete_archived_slice` live path writing `status='RECOVERY_ARCHIVE_DELETED'`, `archive_delta_version IS NULL`, `error_message = reason`, `needs_review=true`.
- `src.recovery.rollback_archived_slice` dry-run preview action `"WOULD_RECOVERY_ARCHIVE_ROLLED_BACK"` — no audit row written.
- `src.recovery.rollback_archived_slice` live path writing `status='RECOVERY_ARCHIVE_ROLLED_BACK'`, `archive_delta_version` populated with the post-RESTORE Delta version, `record_count` = restored row count.
- `_ROLLBACK_TARGET_ALLOWED_STATUSES` membership change: `ARCHIVED` accepted (Phases 5–6) **and** `RECOVERY_ARCHIVE_ROLLED_BACK` accepted (Phase 7).
- Negative gate: target `RECOVERY_ARCHIVE_DELETED` refused with `ArchiveOperationError(reason='target_status_not_rollbackable')` and the rewritten exception template that mentions both renamed verbs.
- Renamed task value key `delete_archived_slice_task_inputs` flowing from `generate_delete_archived_slice_inputs.py` to the for-each iteration.

**Depends on:** 01_setup_and_deploy (config tables seeded, providers test data present). Uses `providers` in isolation — does not touch `claims` or `members`. **Mutates the providers archive Delta multiple times**; cleans itself up in Phase 9.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/38R_recovery_notebooks_rollback_and_reset_dry_run_results.md` (below the H1 title). Do not overwrite previous runs.

> **Workspace parameters.** This test is written with the `sandeep_manocha` convention (see `_workspace_params.md`). When running against another workspace (e.g. `dev2_archive`), substitute the catalog name in every CLI invocation and SQL block. The volume path on disk should be pulled from `table_configs.archive_base_path` rather than copied from this file.

## Execution Rules

> **DO NOT FIX CODE.** If a step fails or produces unexpected results, do **not** modify source code, notebooks, or SQL logic to make it pass. Instead:
>
> 1. **Record** the exact error, unexpected output, or deviation from expected behavior in the results file.
> 2. **Log** the issue with enough detail for a developer to reproduce (command run, actual vs expected output, full error messages).
> 3. **Continue** with remaining steps if possible (unless a failure makes subsequent steps meaningless).
> 4. **Summarize** at the end of the results file under a `## What Happened` section — plain-English description of everything that occurred.
> 5. **Recommend next steps** under a `## Next Steps` section — what the developer should investigate or fix, which test to re-run after the fix, and any manual actions needed.
> 6. Mark each step as **PASS**, **FAIL**, or **SKIP** (skipped due to prior failure) in the results.
> 7. Add a **TL;DR** (max 3 lines) right below the run header summarizing what happened and the outcome.
> 8. **Pre-flight check.** Before running, verify environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers`. Check:
>    - **Source table (`providers`):** Must have data for year 2020. If empty (e.g. drained by 30T), re-run `generate_test_data` to restore.
>    - **Audit log (`providers` only):** Phase 1 clears it. Note any rows present so the operator knows what is being wiped.
>    - **Archive volume (`providers` only):** Phase 1 deletes any `year_*` folder. Tell the user what exists before removing.
>    - **`table_configs` (`providers`):** `archive_base_path` must be correct; `delete_after_archive` starts `false`.
>    - **Bundle deploy:** Jobs `caresource_archive_run`, `caresource_delete_archived_slice`, `caresource_rollback_archived_slice` must exist (`databricks jobs list`). Old names (`caresource-archive-reset-slice`, `caresource-archive-rollback-run`) must NOT exist.
>    - **Other tables:** Do NOT touch `claims` or `members`.

---

## Phase 1 — Clean preconditions on `providers`

### 1a. Clear audit log for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
```

### 1b. Remove any existing archive folders for providers

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 1c. Confirm `delete_after_archive = false` and capture `archive_base_path`

```sql
SELECT table_id, archive_base_path, delete_after_archive
FROM sandeep_manocha.caresource_audit.table_configs
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

**Expect:** Exactly one row, `delete_after_archive = false`. Record `archive_base_path` as `BASE_PATH` for later assertions.

### 1d. Capture baseline source row count for year 2020

```sql
SELECT COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2020
```

**Expect:** Non-zero. Record as `BASELINE_2020`. If zero, stop and re-run `generate_test_data`.

---

## Phase 2 — Seed an `ARCHIVED` slice for `providers / 2020`

### 2a. Run archive (providers only, live, delete off)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 2b. Capture the seed `audit_id` for year 2020

```sql
SELECT audit_id, status, archive_mode, archive_delta_version, record_count, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** Exactly one row with `archive_mode = 'CREATE'`, `archive_delta_version IS NOT NULL` (typically `0` for the first write to a fresh archive folder — captured by `_archive_table_year` immediately after `_verify_archive` per the 2026-04-27 Bug A fix in `src/archiver.py`), `record_count = BASELINE_2020`. Record the `audit_id` as `SEED_AUDIT_ID` and the captured value as `SEED_VERSION` for use in Phase 5 / 6 assertions.

**Critical assertion (Bug A regression gate):** if `archive_delta_version IS NULL`, the version-capture fix has regressed. Do **not** patch the row manually — fail the run and re-open the bug. `rollback_archived_slice` would refuse the seed row with `target_missing_version` and Phases 5–6 cannot proceed.

### 2c. Verify archive Delta has the seeded rows

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = BASELINE_2020`.

---

## Phase 3 — Dry-run `caresource_delete_archived_slice` for `providers / 2020`

> **Note.** Recovery dry-run does NOT write an audit row. It only returns the result dict (printed via `displayHTML`) and exits. The assertion is therefore "no new audit row, no Delta commit", not "DRY_RUN row visible".

### 3a. Run the recovery delete job in dry-run mode

```bash
databricks bundle run caresource_delete_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_ids="sandeep_manocha.source_data_samples.providers",year="2020",reason="",dry_run="true"
```

> `table_ids` must be the fully-qualified `<catalog>.<schema>.<source_table>` value that appears in `table_configs.table_id`. Short names like `providers` will fail with `ArchiveConfigError: Invalid table_ids — missing=['providers'] …` from `generate_delete_archived_slice_inputs.py`. Reason is allowed to be empty when `dry_run=true`; `generate_delete_archived_slice_inputs.py` only enforces non-empty reason on live runs.

### 3b. Confirm no audit row was written for `providers / 2020` since Phase 2

The archive job's preflight in Phase 2 writes both a `STARTED` row (before archiving) and an `ARCHIVED` row (after). So before Phase 3 begins there are already 2 rows for providers/2020. Capture the count before and after the dry-run and assert it is unchanged:

```sql
SELECT COUNT(*) AS phase3_post_count
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
```

**Expect:** `phase3_post_count = 2` (one `STARTED` from Phase 2a, one `ARCHIVED` = `SEED_AUDIT_ID`). Recovery dry-run does NOT write an audit row.

### 3c. Confirm archive Delta is at version 0 and row count is unchanged

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = BASELINE_2020`.

```bash
databricks experimental aitools tools query \
  "DESCRIBE HISTORY delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** Latest `version = 0` (only the WRITE from Phase 2). No DELETE / RESTORE entries.

### 3d. Notebook output

In the Databricks Jobs UI, open the `run_delete_archived_slice_iteration` task output for the run. The displayHTML summary table must include `action = WOULD_RECOVERY_ARCHIVE_DELETED` and `rows_to_delete` = `BASELINE_2020`. The job overall status is `Succeeded`.

---

## Phase 4 — Live `caresource_delete_archived_slice` for `providers / 2020`

### 4a. Run the recovery delete job live

```bash
databricks bundle run caresource_delete_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_ids="sandeep_manocha.source_data_samples.providers",year="2020",reason="38T phase 4 — verify renamed RECOVERY_ARCHIVE_DELETED audit status",dry_run="false"
```

### 4b. Audit: `RECOVERY_ARCHIVE_DELETED` row written with the right shape

```sql
SELECT audit_id, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 200) AS reason_excerpt,
       needs_review, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'RECOVERY_ARCHIVE_DELETED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:**

- Exactly one row.
- `status = 'RECOVERY_ARCHIVE_DELETED'` (the renamed value — this is the headline assertion of the test).
- `archive_mode IS NULL`.
- `archive_delta_version IS NULL` (recovery delete does not record a post-DELETE Delta version on the audit row).
- `record_count = BASELINE_2020` (count read just before the DELETE, not after).
- `reason_excerpt` contains the literal substring `"38T phase 4"`.
- `needs_review = true`.
- Record `audit_id` as `DELETE_AUDIT_ID_1`.

### 4c. Archive Delta now has a DELETE commit; row count is 0

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = 0`.

```bash
databricks experimental aitools tools query \
  "DESCRIBE HISTORY delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** A second commit at `version = 1` with `operation = 'DELETE'`. Version 0 (the WRITE from Phase 2) still in history.

### 4d. Source row count is unchanged

```sql
SELECT COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2020
```

**Expect:** `cnt = BASELINE_2020`. Recovery deletes the archive, never the source.

---

## Phase 5 — Dry-run `caresource_rollback_archived_slice` to `SEED_AUDIT_ID`

### 5a. Run the rollback job in dry-run mode against the original ARCHIVED row

```bash
databricks bundle run caresource_rollback_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_id="providers",year="2020",target_audit_id="${SEED_AUDIT_ID}",dry_run="true"
```

### 5b. Notebook output

Open the `run_rollback_archived_slice` task output. The displayHTML summary must show `action = WOULD_RECOVERY_ARCHIVE_ROLLED_BACK`, `target_version = 0`, `dry_run = True`. Job status `Succeeded`. This proves the gate accepts `status = 'ARCHIVED'` (the first member of `_ROLLBACK_TARGET_ALLOWED_STATUSES`).

### 5c. Confirm no audit row written and no Delta commit

```sql
SELECT COUNT(*) AS rows_added_phase5
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND audit_id NOT IN ('${SEED_AUDIT_ID}', '${DELETE_AUDIT_ID_1}')
```

**Expect:** `rows_added_phase5 = 0`. Latest archive Delta version still `1` (no RESTORE yet).

---

## Phase 6 — Live `caresource_rollback_archived_slice` to `SEED_AUDIT_ID`

### 6a. Run the rollback job live

```bash
databricks bundle run caresource_rollback_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_id="providers",year="2020",target_audit_id="${SEED_AUDIT_ID}",dry_run="false"
```

### 6b. Audit: `RECOVERY_ARCHIVE_ROLLED_BACK` row written with version populated

```sql
SELECT audit_id, status, archive_mode, archive_delta_version, record_count,
       error_message, needs_review, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'RECOVERY_ARCHIVE_ROLLED_BACK'
ORDER BY created_at DESC LIMIT 1
```

**Expect:**

- Exactly one row.
- `status = 'RECOVERY_ARCHIVE_ROLLED_BACK'` (the second renamed status — second headline assertion).
- `archive_mode IS NULL`.
- `archive_delta_version IS NOT NULL` and equal to the post-RESTORE Delta version (typically `2`).
- `record_count = BASELINE_2020` (rows are back).
- `error_message IS NULL` (rollback writes no reason text).
- `needs_review = true`.
- Record `audit_id` as `ROLLBACK_AUDIT_ID`.

### 6c. Archive Delta has a RESTORE commit; rows back to baseline

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = BASELINE_2020`.

```bash
databricks experimental aitools tools query \
  "DESCRIBE HISTORY delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** A new commit (typically `version = 2`) with `operation = 'RESTORE'`. The audit row's `archive_delta_version` matches this version.

---

## Phase 7 — Validate that `RECOVERY_ARCHIVE_ROLLED_BACK` is itself rollback-able

This is the only phase that exercises the second member of `_ROLLBACK_TARGET_ALLOWED_STATUSES`. Without this assertion, the membership change in [src/recovery.py](../../../src/recovery.py) line 35 has no end-to-end coverage.

### 7a. Re-run live `caresource_delete_archived_slice` to wipe the archive again

```bash
databricks bundle run caresource_delete_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_ids="sandeep_manocha.source_data_samples.providers",year="2020",reason="38T phase 7 — set up rollback-of-rollback test",dry_run="false"
```

This writes a second `RECOVERY_ARCHIVE_DELETED` audit row (`DELETE_AUDIT_ID_2`) and brings the archive Delta to a new DELETE commit (typically `version = 3`).

### 7b. Dry-run rollback targeting the Phase-6 `ROLLBACK_AUDIT_ID`

```bash
databricks bundle run caresource_rollback_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_id="providers",year="2020",target_audit_id="${ROLLBACK_AUDIT_ID}",dry_run="true"
```

### 7c. Notebook output

The `run_rollback_archived_slice` task must succeed. The displayHTML summary must show `action = WOULD_RECOVERY_ARCHIVE_ROLLED_BACK`, `target_version` equal to the `archive_delta_version` recorded on `ROLLBACK_AUDIT_ID` (typically `2`), `dry_run = True`. Job status `Succeeded`.

**Critical assertion:** the gate did NOT raise `target_status_not_rollbackable` even though the target row's status is `RECOVERY_ARCHIVE_ROLLED_BACK`. If this phase fails with that exception, the membership change in `_ROLLBACK_TARGET_ALLOWED_STATUSES` regressed — capture the full notebook stack trace in the results file.

---

## Phase 8 — Negative: rolling back to a `RECOVERY_ARCHIVE_DELETED` row is refused

### 8a. Dry-run rollback targeting `DELETE_AUDIT_ID_1`

```bash
databricks bundle run caresource_rollback_archived_slice -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",table_configs_table="sandeep_manocha.caresource_audit.table_configs",table_id="providers",year="2020",target_audit_id="${DELETE_AUDIT_ID_1}",dry_run="true"
```

### 8b. Expect the `run_rollback_archived_slice` task to fail with the renamed gate

In the Databricks Jobs UI, the task fails. The notebook stack trace must contain:

- `ArchiveOperationError` raised from `src/recovery.py` `rollback_archived_slice`.
- `reason='target_status_not_rollbackable'` (`ArchiveOperationError.reason` attribute, visible in the displayHTML failure summary or the task log).
- The diagnostic message must contain the substring `RECOVERY_ARCHIVE_ROLLED_BACK` (cited as a valid target) AND `RECOVERY_ARCHIVE_DELETED` (cited as the actual target status). This confirms the rewritten template in [src/exceptions.py](../../../src/exceptions.py) lines 85–95 is being used.

The gate triggers BEFORE the dry-run check (status check at line 138-154 of `src/recovery.py` runs before `if dry_run:`), so this fails identically with `dry_run=true` and `dry_run=false`. Dry-run keeps the test side-effect-free.

### 8c. Confirm no Delta mutation and no new audit row

```bash
databricks experimental aitools tools query \
  "DESCRIBE HISTORY delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** Latest commit is still the Phase 7a DELETE (version 3); no new RESTORE entry. No new `archive_audit_log` row for providers/2020 since Phase 7a.

---

## Phase 9 — Cleanup

### 9a. Clear audit rows for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
```

### 9b. Remove archive folder for providers

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 9c. Confirm `delete_after_archive` is still `false` (Phase 1 already set this; sanity check)

```sql
SELECT delete_after_archive
FROM sandeep_manocha.caresource_audit.table_configs
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

**Expect:** `false`.

### 9d. Source row count unchanged

```sql
SELECT COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2020
```

**Expect:** `cnt = BASELINE_2020`. Recovery jobs never touch source. If this fails, something else (likely 30T) drained source data — re-run `generate_test_data` before the next test.
