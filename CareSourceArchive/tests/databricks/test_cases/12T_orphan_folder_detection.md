# 12 — Orphan Archive Slice Detection

**Goal:** Archive detects when an archive slice classifies as `ORPHAN` (or `VALID` with no matching audit entry), logs `FAILED / archive_folder_orphan`, then recovers after the orphan is reconciled (via `recovery.delete_archived_slice` or by restoring the audit entry).

> **Phase 2 terminology note.** The legacy `folder_exists` + `orphan_folder` vocabulary was retired. State is now computed by `src.utils.archive_state_and_count(spark, base_path, table_name, year)`, which returns one of `("VALID", count)`, `("MISSING", 0)`, or `("ORPHAN", 0)`. Empty archive folders raise `[DELTA_MISSING_TRANSACTION_LOG]` from Spark; `_classify_delta_probe_exception` maps that fragment to `archive_folder_orphan` (added 2026-04-27). The audit `error_message` column carries the `archive_folder_orphan` diagnostic template. See [docs/runbooks/recovery.md](../../../docs/runbooks/recovery.md) and [docs/status_affects.md](../../../docs/status_affects.md) Story 4.

**Depends on:** 05_archive_live_create (claims must be fully archived first so we can reset a single year)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/12R_orphan_folder_detection_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims year 2018`. Check:
>    - **Audit log (claims year 2018):** Check if any entries exist. Phase 1 will delete them to create a clean slate. If stale STARTED/FAILED entries exist from prior test runs, note them.
>    - **Archive volume (claims year 2018):** Check if `claims/year_2018/` folder exists. Phase 1 deletes it, Phase 2 recreates it as an empty orphan. If it exists with data from a prior run, the user needs to delete it via `dbutils.fs.rm`.
>    - **Other claims years:** Do NOT touch years other than 2018. Their audit entries and archive folders should remain intact.
>    - **Other tables:** Do NOT touch `providers` or `members` data, audit entries, or archive folders.

---

## Phase 1 — Establish Preconditions

Reset claims year 2018 to a state where the audit log has no successful archive but the folder does not yet exist. This gives us a clean year to inject the orphan.

### 1a. Delete the archive folder for claims year 2018

**Manual step** — run in workspace notebook:

```python
dbutils.fs.rm("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims/year_2018", recurse=True)
```

### 1b. Delete audit entries for claims year 2018

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
  AND year = 2018
```

### 1c. Verify clean state

```sql
SELECT
  (SELECT COUNT(*) FROM sandeep_manocha.caresource_audit.archive_audit_log
   WHERE table_name = 'sandeep_manocha.source_data_samples.claims' AND year = 2018) AS audit_entries
```

**Expect:** `audit_entries = 0`

---

## Phase 2 — Create Orphan + Run (expect failure)

### 2a. Create an orphan folder (no data, no audit trail)

**Manual step** — run in workspace notebook:

```python
dbutils.fs.mkdirs("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims/year_2018")
```

This creates the folder without any data or audit trail. `archive_state_and_count` returns `("ORPHAN", 0)` because the COUNT(*) probe surfaces `[DELTA_MISSING_TRANSACTION_LOG] ... is not a Delta table`, which `_classify_delta_probe_exception` maps to `archive_folder_orphan`. That is the exact condition that drives the resolver to raise `archive_folder_orphan`. Note: if the folder contains a valid (but undocumented) Delta table instead, `archive_state_and_count` returns `("VALID", count)` and the D14 resolver (rule C, "VALID + last_status=None") still raises `archive_folder_orphan` — same failure class.

### 2b. Run archive (claims only)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

### 2c. Check audit log

```sql
SELECT table_name, year, status, SUBSTRING(error_message, 1, 150) AS error_excerpt, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%claims%'
  AND status IN ('STARTED', 'FAILED')
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2018: STARTED → FAILED. The audit row carries the `archive_folder_orphan` diagnostic from [src/exceptions.py](../../../src/exceptions.py): "{table} year 2018: Archive path {path} has data files but no valid Delta transaction log (orphan or corrupted folder). Use delete_archived_slice to wipe the orphan path, then re-run the archive job. See docs/runbooks/recovery.md."
- The raw Spark `[DELTA_MISSING_TRANSACTION_LOG]` exception MUST NOT propagate into `error_message` — that was the pre-fix bug surfaced in [12R](../test_results/12R_orphan_folder_detection_results.md) (Run 2026-04-27, aborted at phase 2b).
- The for-each loop stops at the first failure (year 2018), so later years are not processed.
- May see a second STARTED → FAILED pair if the for-each task retries automatically (same behavior as test 11 Phase 2).

> **Note:** The internal reason code is `archive_folder_orphan` (set on the `ArchiveOperationError.reason` attribute in `archiver.py`), but the audit log's `error_message` column stores the human-readable diagnostic text, not the reason code. The legacy reason `orphan_folder` was renamed in Phase 1 Batch 1.

---

## Phase 3 — Fix + Re-run (recovery)

### 3a. Reset the orphan slice

> **Reproduce first.** Before any destructive action, re-run the orphan classification from a notebook cell on a stable cluster to rule out a transient DESCRIBE HISTORY failure:
>
> ```python
> from src.utils import archive_state_and_count
> state, count = archive_state_and_count(
>     spark,
>     "/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples",
>     "claims",
>     2018,
> )
> print(state, count)   # expect ("ORPHAN", 0) for a genuinely unreadable slice
> ```

**Manual step** — run in workspace notebook (direct `dbutils.fs.rm` acceptable here because the test intentionally planted an empty orphan folder and no audit row exists to preserve):

```python
dbutils.fs.rm("/Volumes/sandeep_manocha/caresource_archive/caresource_archive_vol/source_data_samples/claims/year_2018", recurse=True)
```

For production-like recoveries (real Delta commits present), prefer `notebooks/recovery/delete_archived_slice.py` instead — it records the reset in the audit trail.

### 3b. Re-run archive

Same command as Phase 2b.

### 3c. Check audit log

```sql
SELECT table_name, year, status, archive_mode, record_count, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%claims%'
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2018: "Previous run FAILED... Retrying." → ARCHIVED with `archive_mode = CREATE`
- Other years: STARTED → SKIPPED (already archived, no new data)
