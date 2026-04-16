# 13 — Stale STARTED Detection

**Goal:** Archive detects an old STARTED entry from a different run and treats it as abandoned (auto-recovers). A recent STARTED from a different run blocks the year with SKIPPED_CONCURRENT, then recovers after the fake entry is cleaned up.

**Depends on:** 05_archive_live_create (claims must be fully archived first)
**Results:** Append a new `## Run — <date> <time>` section **at the top** of `test_results/13R_stale_started_detection_results.md` (below the H1 title). Do not overwrite previous runs.

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
> 8. **Pre-flight check.** Before running, verify the environment state and report findings. If cleanup is needed, **tell the user what and why** — do not clean up without approval. Only touch `claims year 2020`. Check:
>    - **Audit log (claims year 2020):** Check latest status. Must be `ARCHIVED` or `SKIPPED` (not a dangling STARTED). If a dangling STARTED exists from a crashed prior run, tell the user — it will interfere with the fake STARTED entries this test inserts.
>    - **Archive volume (claims year 2020):** Folder must exist with valid Delta data (from test 05). The archiver checks folder existence during action resolution.
>    - **Audit log consistency:** Verify no `fake-stale-run-00000` or `fake-concurrent-run-00000` entries remain from a prior test 13 run. If found, tell user to delete them.
>    - **Other tables:** Do NOT touch `providers` or `members` data, audit entries, or archive folders.

---

## Phase 1 — Establish Preconditions

Verify claims year 2020 has no dangling STARTED entries from prior test runs.

```sql
SELECT status, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name = 'sandeep_manocha.source_data_samples.claims'
  AND year = 2020
ORDER BY created_at DESC LIMIT 5
```

**Expect:** Most recent entry is ARCHIVED or SKIPPED — no orphan STARTED rows. If any exist from a crashed run, delete them before proceeding.

---

## Phase 2 — Stale STARTED (auto-recovery)

A STARTED entry older than `stale_started_threshold_hours` (default 4h) is treated as abandoned. The archiver logs a WARNING and proceeds normally.

### 2a. Insert a fake stale STARTED entry

```sql
INSERT INTO sandeep_manocha.caresource_audit.archive_audit_log
  (audit_id, archive_run_id, table_name, year, status, record_count, created_at, archived_by)
VALUES
  (uuid(), 'fake-stale-run-00000', 'sandeep_manocha.source_data_samples.claims', 2020, 'STARTED', 0,
   current_timestamp() - INTERVAL 24 HOURS, current_user())
```

This simulates a run that started 24 hours ago and never completed (well above the 4-hour threshold).

### 2b. Run archive (claims only)

```bash
databricks bundle run caresource_archive_run -t dev --profile DEFAULT \
  --params config_table="sandeep_manocha.caresource_audit.global_settings",dry_run="false",source_catalog="sandeep_manocha",source_schema="source_data_samples",table_config_filter="source_table = 'claims'"
```

### 2c. Check audit log

```sql
SELECT table_name, year, status, archive_mode, record_count, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%claims%'
  AND year = 2020
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Job logs contain WARNING: `"Found stale STARTED from run fake-stale-run-00000 (24.0 hours ago). Treating as abandoned."`
- Archive proceeds normally — year 2020 is NOT blocked
- Year 2020 outcome depends on prior state: SKIPPED (already archived, no new data) or APPEND (if new data exists)
- The fake stale STARTED entry remains in the log but is harmless

### 2d. Clean up the stale entry

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE archive_run_id = 'fake-stale-run-00000'
```

---

## Phase 3 — Concurrent STARTED (blocked + manual recovery)

A STARTED entry younger than the stale threshold is treated as an active concurrent run. The archiver skips the year with SKIPPED_CONCURRENT to prevent data corruption.

### 3a. Insert a fake concurrent STARTED entry

```sql
INSERT INTO sandeep_manocha.caresource_audit.archive_audit_log
  (audit_id, archive_run_id, table_name, year, status, record_count, created_at, archived_by)
VALUES
  (uuid(), 'fake-concurrent-run-00000', 'sandeep_manocha.source_data_samples.claims', 2020, 'STARTED', 0,
   current_timestamp() - INTERVAL 1 MINUTE, current_user())
```

This simulates a run that started 1 minute ago (well below the 4-hour threshold).

### 3b. Run archive (claims only)

Same command as Phase 2b.

### 3c. Check audit log

```sql
SELECT table_name, year, status, archive_run_id, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%claims%'
  AND year = 2020
  AND status IN ('STARTED', 'SKIPPED_CONCURRENT')
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2020: STARTED → SKIPPED_CONCURRENT (another run appears active)
- Other years process normally (SKIPPED if already archived)

### 3d. Clean up and re-run (recovery)

Delete the fake concurrent entry:

```sql
DELETE FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE archive_run_id = 'fake-concurrent-run-00000'
```

Re-run the archive (same command as Phase 2b).

### 3e. Verify recovery

```sql
SELECT table_name, year, status, archive_mode, record_count, created_at
FROM sandeep_manocha.caresource_audit.archive_audit_log
WHERE table_name LIKE '%claims%'
ORDER BY created_at DESC LIMIT 10
```

**Expect:**
- Year 2020 processes normally — SKIPPED (already archived, no new data) or APPEND
- No SKIPPED_CONCURRENT entries from this run
