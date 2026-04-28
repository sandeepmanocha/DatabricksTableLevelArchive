# 30 — Dedicated Delete Job End-to-End

**Goal:** Exercise the Phase 2 `caresource_delete_source_after_archive` job across the four scenarios that matter, in a single test, using exactly one preparatory archive run:

1. **Dry-run preview** emits `DRY_RUN / action=WOULD_DELETE` rows without touching source.
2. **Live delete, scoped** writes `ARCHIVED_AND_DELETED` with `archive_mode = DELETE` and `archive_delta_version IS NULL`, and deletes rows from source only for the scoped years.
3. **D13 eligibility guard** — a year with no prior `ARCHIVED` row fails with `FAILED / not_eligible_for_delete` and `archive_mode IS NULL`.
4. **Scope guard** — a live delete with every filter widget empty is refused by `generate_parameters.py` with `ArchiveConfigError("Delete job refused: at least one filter widget is required for a live run.")`.

**Covers:** `DeleteJob.run` (in `src/delete_job.py`, extends `ArchiveBase`), `AuditLogger.is_eligible_for_delete`, `AuditLogger.get_prior_archived_run`, `log_dry_run` action validation (WOULD_DELETE / SKIP_NOT_ELIGIBLE), `archive_mode = DELETE` with `archive_delta_version = NULL`, `generate_parameters.py` live-delete scope guard.

**Depends on:** 01_setup_and_deploy (fresh test data). Uses `providers` in isolation. **One archive run only** — the rest of the test uses delete-job runs and SQL.

**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/30R_delete_job_end_to_end_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers`. Check:
>    - **Source table (`providers`):** Must have data for all years (2020–2025). If rows were deleted by prior tests, re-run `generate_test_data` to restore.
>    - **Audit log (`providers` only):** Must be empty (Phase 1 clears it). If entries remain, note them.
>    - **Archive volume (`providers` only):** No `year_*` folders should exist. If folders exist, tell the user to delete via `databricks fs rm`.
>    - **`table_configs` (`providers`):** `delete_after_archive` starts `false` (Phase 1 sets it explicitly). `archive_base_path` must be correct.
>    - **Bundle deploy:** The `caresource_delete_source_after_archive` job must be deployed. Run `databricks bundle deploy -t dev --profile DEFAULT` if it does not show up in `databricks jobs list`.
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

### 1c. Set `delete_after_archive = false`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 30: baseline archive without delete'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 1d. Record baseline source counts per year

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
GROUP BY 1 ORDER BY 1
```

**Expect:** One row per year 2020–2025. Record these as `BASELINE_YYYY`. The test targets **years 2020 and 2021** for the live scoped delete and **year 2019 (never archived)** for the D13 eligibility guard.

> **Precondition for 1d.** The test assumes retention puts years 2020 and 2021 inside the eligible window (the default `retention_years = 2` makes everything `<= current_year - 2` eligible). If `global_settings.retention_years` is set tighter and 2020–2021 fall outside, either bump retention temporarily or pick different eligible years and substitute them throughout. If `providers` has no rows for 2019 after `generate_test_data`, the D13 assertion in Phase 4 still works — the audit row with zero `record_count` is written from the error path, not from a source query.

---

## Phase 2 — Seed ARCHIVED state (one archive run, `delete_after_archive = false`)

### 2a. Run archive (providers only, live, delete off)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 2b. Verify audit: every eligible year reached `ARCHIVED`, no `ARCHIVED_AND_DELETED`

```sql
SELECT year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
ORDER BY year, created_at
```

**Expect:** `STARTED` → `ARCHIVED` (archive_mode = `CREATE`) for each eligible year. **No `ARCHIVED_AND_DELETED` row.** Record the `archive_run_id` as `SEED_RUN_ID` and the `record_count` for years 2020 and 2021 as `ARCHIVED_2020` and `ARCHIVED_2021`.

### 2c. Verify source still has every baseline row

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
GROUP BY 1 ORDER BY 1
```

**Expect:** Every `cnt = BASELINE_YYYY`.

### 2d. Verify archive row counts match for years 2020 and 2021

```bash
databricks experimental aitools tools query \
  "SELECT '2020' AS yr, COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`
   UNION ALL
   SELECT '2021', COUNT(*) FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2021\`" \
  --profile DEFAULT
```

**Expect:** `cnt` for each year equals `ARCHIVED_2020` / `ARCHIVED_2021`, which in turn equals `BASELINE_2020` / `BASELINE_2021`.

---

## Phase 3 — Delete-job run #1: dry-run preview for years 2020 and 2021

### 3a. Run the delete job in dry-run mode, scoped to years 2020, 2021

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'",years='"2020,2021"'
```

> **CLI quoting note.** The `years` CSV value must be quoted to survive shell parsing: `--params 'years="2020,2021"'`. See `_workspace_params.md`.

### 3b. Inspect audit for DRY_RUN rows

```sql
SELECT year, status, record_count, archive_mode, archive_delta_version,
       get_json_object(conditions_applied, '$.action') AS action,
       archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year IN (2020, 2021)
  AND status = 'DRY_RUN'
ORDER BY year, created_at DESC
```

**Expect:** One `DRY_RUN` row per year (2020 and 2021) with:

- `action = "WOULD_DELETE"`
- `record_count` equal to the live source count for that year (i.e. `BASELINE_2020` / `BASELINE_2021`)
- `archive_mode` and `archive_delta_version` both `NULL` on dry-run rows (dry-run does not touch either column)
- `archive_run_id` is a new run id — call it `DRY_RUN_1_ID`, and note it differs from `SEED_RUN_ID`.

### 3c. Verify source is untouched by the dry-run

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2020, 2021)
GROUP BY 1 ORDER BY 1
```

**Expect:** Both counts unchanged — `BASELINE_2020` and `BASELINE_2021`.

---

## Phase 4 — Delete-job run #2: live delete scoped to year 2020 only

Intent: narrow the scope to a single year so Phase 5 can exercise the D13 eligibility guard on an unarchived year without the delete job trying to touch it on the happy path.

### 4a. Run the delete job live, scoped to year 2020

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'",years='"2020"'
```

### 4b. Audit: `ARCHIVED_AND_DELETED` with `archive_mode = DELETE` and `archive_delta_version IS NULL`

```sql
SELECT year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 200) AS message_excerpt,
       archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED_AND_DELETED'
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Latest row has:

- `status = ARCHIVED_AND_DELETED`
- `archive_mode = 'DELETE'` (exact string — this is the delete-job signature)
- `archive_delta_version IS NULL` (the delete job does not rewrite the archive Delta, so there is no new version to record)
- `record_count = ARCHIVED_2020 = BASELINE_2020`
- `message_excerpt` contains `"Deleted"`, `ARCHIVED_2020` as a number, and a reference to `SEED_RUN_ID` (the prior archive run that owns the data). Format: `Deleted <N> rows from source. Originally archived by run <SEED_RUN_ID> at <timestamp>.`
- `archive_run_id` is a new run id — call it `DELETE_RUN_ID` — and differs from both `SEED_RUN_ID` and `DRY_RUN_1_ID`.

### 4c. Source: year 2020 emptied, year 2021 untouched

```sql
SELECT YEAR(effective_date) AS yr, COUNT(*) AS cnt
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) IN (2020, 2021)
GROUP BY 1 ORDER BY 1
```

**Expect:**

- `yr = 2020`: `cnt = 0`. The delete job removed every eligible row.
- `yr = 2021`: `cnt = BASELINE_2021`. Not scoped into this run, so untouched.

### 4d. Archive Delta for year 2020 is unchanged on disk

```bash
databricks experimental aitools tools query \
  "SELECT COUNT(*) AS cnt FROM delta.\`/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers/year_2020\`" \
  --profile DEFAULT
```

**Expect:** `cnt = ARCHIVED_2020`. Delete job only removes source rows; the archive is the durable copy.

### 4e. Sanity: the original `ARCHIVED` row for 2020 owned by `SEED_RUN_ID` still exists

```sql
SELECT year, status, archive_mode, archive_run_id
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED'
```

**Expect:** At least one row with `archive_mode = 'CREATE'` and `archive_run_id = SEED_RUN_ID`. The delete job appends a new `ARCHIVED_AND_DELETED` row; it does not rewrite history.

---

## Phase 5 — Delete-job run #3: D13 eligibility guard on a year with no ARCHIVED row

### 5a. Confirm no `ARCHIVED` row exists for year 2019

```sql
SELECT COUNT(*) AS archived_rows_2019
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2019
  AND status = 'ARCHIVED'
```

**Expect:** `archived_rows_2019 = 0`. (Providers has no 2019 data in the seeded dataset, so Phase 2 did not archive it.)

### 5b. Run the delete job live, scoped to year 2019

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'providers'",years='"2019"'
```

### 5c. Audit: `FAILED / not_eligible_for_delete` with `archive_mode IS NULL`

```sql
SELECT year, status, archive_mode, archive_delta_version, record_count,
       SUBSTRING(error_message, 1, 300) AS message_excerpt,
       archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND year = 2019
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Latest row has:

- `status = 'FAILED'`
- `archive_mode IS NULL` (D13-ineligible rows omit the mode; the delete never happened)
- `archive_delta_version IS NULL`
- `record_count = 0`
- `message_excerpt` contains the reason code `not_archived_state` (or whatever `is_eligible_for_delete` returned) and points to `docs/runbooks/delete-source-after-archive.md`.

### 5d. Source for year 2019 unchanged

```sql
SELECT COUNT(*) AS providers_2019
FROM sandeep_manocha.source_data_samples.providers
WHERE YEAR(effective_date) = 2019
```

**Expect:** Whatever the baseline was (typically 0 since providers starts at 2020). If nonzero, that count is unchanged.

### 5e. Job exit status

**Expect:** The Databricks job run for Phase 5b completes successfully (the ForEach task for year 2019 records the FAILED audit row but does not raise — the task is considered a successful failure log). If the task raises, that's a regression — capture the notebook stack trace in the results file.

---

## Phase 6 — Delete-job run #4: live scope guard (no filters)

### 6a. Run the delete job live with every filter widget empty

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false"
```

Note: no `source_catalog`, no `source_schema`, no `table_config_filter`, no `years`.

### 6b. Expect the job to fail **inside `generate_parameters`** — not at delete time

**Expect:**

- The `generate_parameters` task fails with `ArchiveConfigError`. The error message contains the literal phrase `"Delete job refused: at least one filter widget is required for a live run."`
- The `run_delete` ForEach task never executes (no inputs were produced).
- No new audit rows for providers are written by this run:

```sql
SELECT COUNT(*) AS rows_added_since_phase5
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
  AND created_at > current_timestamp() - INTERVAL 2 MINUTES
  AND status NOT IN ('DRY_RUN', 'FAILED', 'ARCHIVED_AND_DELETED')
```

**Expect:** `rows_added_since_phase5 = 0`.

### 6c. Dry-run equivalent **does not** trip the scope guard

Quick sanity: with `dry_run="true"`, the same no-filter invocation is permitted (dry-run is safe to run at full scope).

```bash
databricks bundle run caresource_delete_source_after_archive -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="true"
```

**Expect:** Job succeeds. This may produce `DRY_RUN` rows for any eligible `(table, year)` in the entire `table_configs` catalog. That's allowed by design — only **live** no-filter delete runs are refused. For the purposes of this test, record that the job succeeded; do not assert on its full audit output.

---

## Phase 7 — Cleanup

### 7a. Clear audit rows for providers

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.providers'
```

### 7b. Also clear any `DRY_RUN` rows introduced by Phase 6c (broad scan)

```sql
SELECT DISTINCT table_name, COUNT(*) AS rows
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE created_at > current_timestamp() - INTERVAL 15 MINUTES
  AND status = 'DRY_RUN'
GROUP BY table_name ORDER BY table_name
```

If any rows are listed, delete them (scope by `table_name` so we don't wipe other tests' audit history). For each row: `DELETE FROM ... WHERE table_name = '<row.table_name>' AND status = 'DRY_RUN' AND created_at > current_timestamp() - INTERVAL 15 MINUTES`.

### 7c. Remove archive folders

```bash
databricks fs rm -r \
  dbfs:/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/providers \
  --profile DEFAULT
```

### 7d. Reset `delete_after_archive`

```sql
UPDATE sandeep_manocha.caresource_audit.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Reset after test 30'
WHERE table_id = 'sandeep_manocha.source_data_samples.providers'
```

### 7e. Restore source data

Re-run `generate_test_data` to restore providers (year 2020 was emptied by Phase 4b).
