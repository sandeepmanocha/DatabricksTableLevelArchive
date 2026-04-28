# 36 — Folder Deleted, Then `delete_after_archive` Flipped On (self-heal + first delete)

**Goal:** Variant of test 34 with `delete_after_archive = true` enabled **after** the original archive ran. Proves that when the archiver finds `MISSING + ARCHIVED` and `delete_after_archive = true`, it does the safe thing: re-CREATE the folder from source, then run the source-delete step in the same run, ending at `ARCHIVED_AND_DELETED`.

> **Why this matters.** Test 34 covers `MISSING + ARCHIVED` with DAA=false (silent re-CREATE). Test 19 covers `MISSING + ARCHIVED_AND_DELETED` (ERROR — data loss risk). This test covers the third real combo: folder deleted by hand, audit only says ARCHIVED, but the operator has since switched the table to delete-after-archive. The system must recover safely without skipping the delete or losing rows.

> **State machine path.** `archive_state = MISSING`, `last_status = ARCHIVED`, `delete_after = true`. Falls past the `RESUME_DELETE` branch (state is MISSING, not VALID) → into rule F (`MISSING → CREATE`) → CREATE writes the folder → `delete_after = true` triggers the delete-source flow → final audit row is `ARCHIVED_AND_DELETED`.

**Depends on:** 05_archive_live_create (providers must have been archived once with DAA=false; folder + source data both present)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/36R_archive_folder_deleted_with_delete_after_archive_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `providers year 2020`. Check:
>    - **Audit log (providers year 2020):** Most recent row must be `ARCHIVED` (not `ARCHIVED_AND_DELETED`, not `FAILED`). If `ARCHIVED_AND_DELETED`, this is the test 19 scenario — re-run test 05 to restore a plain ARCHIVED state.
>    - **Archive volume (providers year 2020):** Folder must exist with valid Delta data. Phase 2 deletes it.
>    - **Source table (providers):** Source rows for year 2020 must still exist (count > 0). The plain `ARCHIVED` audit state implies DAA was false on the original run, so source rows should still be there. If absent, re-seed with `generate_test_data`.
>    - **table_configs (providers):** `delete_after_archive` should currently be `false`. Phase 1 flips it to `true`.
>    - **Retention window:** Year 2020 must be inside the current retention window. With seeded `default_retention_years = 0`, all years qualify.
>    - **Other tables/years:** Do NOT touch `claims`, `members`, or other providers years.

---

## Workspace Parameters

> See [`_workspace_params.md`](./_workspace_params.md).

---

## Phase 1 — Establish preconditions

### 1a. Confirm providers year 2020 ends in plain `ARCHIVED`

```sql
SELECT status, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND year = 2020
  AND status = 'ARCHIVED'
ORDER BY created_at DESC LIMIT 1
```

**Expect:** Exactly one row, `status = ARCHIVED`. Note `record_count` as `pre_count`.

### 1b. Confirm folder exists and source still has the rows

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020")
```

```sql
SELECT
  (SELECT COUNT(*) FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020`) AS archive_rows,
  (SELECT COUNT(*) FROM dev2_archive.source_data_samples.providers WHERE YEAR(effective_date) = 2020 AND effective_date IS NOT NULL) AS source_rows
```

**Expect:** `archive_rows = source_rows = pre_count`.

### 1c. Flip `delete_after_archive` to `true`

```sql
UPDATE dev2_archive.metadata.table_configs
SET delete_after_archive = true,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 36: enable DAA after the original ARCHIVED run'
WHERE table_id = 'dev2_archive.source_data_samples.providers'
```

---

## Phase 2 — Delete the folder + Re-run (expect CREATE → ARCHIVED_AND_DELETED)

### 2a. Delete the archive folder

```python
dbutils.fs.rm("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020", recurse=True)
```

State now: audit = `ARCHIVED`, folder = MISSING, source still has the rows, DAA = true.

### 2b. Run archive (providers only)

```bash
databricks bundle run caresource_archive_run -t dev-serverless --profile fe-sandbox-manocha \
  --params config_table="dev2_archive.metadata.global_settings",dry_run="false",source_catalog="dev2_archive",source_schema="source_data_samples",table_config_filter="source_table = 'providers'"
```

### 2c. Check audit — three new rows for year 2020

```sql
SELECT status, archive_mode, record_count, archive_run_id, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND year = 2020
ORDER BY created_at DESC LIMIT 5
```

**Expect (newest to oldest in this run):**
1. `ARCHIVED_AND_DELETED` — `record_count = pre_count`, same `archive_run_id` as the next two rows.
2. `ARCHIVED` — `archive_mode = CREATE`, `record_count = pre_count`.
3. `STARTED` — `record_count = 0`.
4. (older) The original `ARCHIVED` row from test 05, untouched.

### 2d. Verify folder is back

```python
dbutils.fs.ls("/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020")
```

```sql
SELECT COUNT(*) AS archive_rows
FROM delta.`/Volumes/dev2_archive/source_data_samples_archive/sample_data_archive_ext_vol/source_data_samples/providers/year_2020`
```

**Expect:** Folder present; `archive_rows = pre_count`.

### 2e. Verify source year 2020 is empty

```sql
SELECT COUNT(*) AS source_rows
FROM dev2_archive.source_data_samples.providers
WHERE YEAR(effective_date) = 2020 AND effective_date IS NOT NULL
```

**Expect:** `source_rows = 0`. The DAA delete ran successfully on the freshly re-created archive.

### 2f. Confirm no FAILED rows for this run

```sql
SELECT COUNT(*) AS failed_rows
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND year = 2020
  AND status = 'FAILED'
  AND created_at >= current_timestamp() - INTERVAL 1 HOUR
```

**Expect:** `failed_rows = 0`. The combination MISSING + ARCHIVED + DAA=true is a normal recovery path, not an error.

---

## Phase 3 — Re-run again (expect SKIP)

### 3a. Re-run archive

Same command as 2b.

### 3b. Check audit

```sql
SELECT status, archive_mode, created_at
FROM dev2_archive.metadata.archive_audit_log
WHERE table_name = 'dev2_archive.source_data_samples.providers'
  AND year = 2020
ORDER BY created_at DESC LIMIT 3
```

**Expect:** Newest row is `SKIPPED` (rule D: VALID + ARCHIVED_AND_DELETED → historical no-data → SKIP). System is steady.

---

## Phase 4 — Cleanup

### 4a. Reset `delete_after_archive`

```sql
UPDATE dev2_archive.metadata.table_configs
SET delete_after_archive = false,
    modified_by = 'manual',
    modified_at = current_timestamp(),
    change_reason = 'Test 36 cleanup: reset DAA'
WHERE table_id = 'dev2_archive.source_data_samples.providers'
```

### 4b. (Optional) Restore source data

Source rows for year 2020 are now in the archive only. To run later tests that need source data for 2020, re-run `generate_test_data` (it idempotently restores source) or use the rehydration notebook (test 14 / 25).

---

## What this proves

| Behavior | Pass criteria |
|----------|---------------|
| Folder deleted while audit = `ARCHIVED` and DAA flipped to `true` | Detected as `MISSING + ARCHIVED + DAA=true` |
| Action chosen | `CREATE` (rule F wins; RESUME_DELETE skipped because state is MISSING, not VALID) |
| Self-heal | Folder rebuilt from source; new `ARCHIVED` audit row with `archive_mode = CREATE` |
| Delete-after-archive runs in the same run | Source year 2020 = 0 rows; final audit row = `ARCHIVED_AND_DELETED` |
| No FAILED, no SKIPPED_CONCURRENT | Recovery is silent and complete |
| Re-run after recovery | Rule D triggers SKIP (no source data left to archive) |

## What this does NOT cover

- Folder deleted **and** audit already shows `ARCHIVED_AND_DELETED` — see test 19 (ERROR — source data unrecoverable).
- DAA flipped on **before** the very first archive run — that is the standard test 09 path (`CREATE → ARCHIVED → ARCHIVED_AND_DELETED` in the same run, no folder delete in between).
- Folder partially corrupted (Delta history present but unreadable) with DAA=true — see test 12 (ORPHAN; archive errors out before reaching the delete step regardless of DAA).
